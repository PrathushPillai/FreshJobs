"""respx-mocked tests for the Gem, Polymer and JazzHR adapters (fields trimmed from real responses, Nov 2026)."""

import httpx
import pytest
import respx

from app.pipeline.detect import detect_board
from app.sources import ADAPTERS


async def fetch(ats: str, slug: str, **kwargs):
    async with httpx.AsyncClient() as client:
        return await ADAPTERS[ats].fetch(client, slug, **kwargs)


def test_detects_gem_and_polymer_boards():
    assert detect_board("https://jobs.gem.com/11x-ai/am9icG9zdDq8") == ("gem", "11x-ai")
    assert detect_board("https://api.gem.com/job_board/v0/11x-ai/job_posts/") == ("gem", "11x-ai")
    assert detect_board("https://jobs.polymer.co/axoft/41739") == ("polymer", "axoft")


@pytest.mark.asyncio
@respx.mock
async def test_gem_reads_one_request_with_descriptions():
    respx.get("https://api.gem.com/job_board/v0/acme/job_posts/").mock(return_value=httpx.Response(200, json=[{
        "id": "j1", "title": "Data Engineer", "absolute_url": "https://jobs.gem.com/acme/j1", "location": {"name": "New York, NY"},
        "location_type": "remote", "content_plain": "Build pipelines in SQL and Python.", "departments": [{"name": "Data"}],
        "first_published_at": "2026-11-04T19:17:02.814Z", "offices": [],
    }]))
    [job] = await fetch("gem", "acme")
    assert (job.external_id, job.title, job.locations, job.remote, job.department) == ("j1", "Data Engineer", ["New York, NY"], True, "Data")
    assert job.posted_at.hour == 19 and "SQL" in job.description


@pytest.mark.asyncio
@respx.mock
async def test_polymer_pages_and_fetches_details_for_new_jobs():
    api = "https://api.polymer.co/v1/hire/organizations/axoft/jobs"
    item = lambda i: {"id": i, "title": f"Engineer {i}", "display_location": "Cambridge, MA", "remoteness_pretty": "No remote",
                      "job_post_url": f"https://jobs.polymer.co/axoft/{i}", "published_at": "2026-11-03T15:29:54.878Z"}
    respx.get(api, params={"page": 1}).mock(return_value=httpx.Response(200, json={"items": [item(1)], "meta": {"next_page": 2}}))
    respx.get(api, params={"page": 2}).mock(return_value=httpx.Response(200, json={"items": [item(2)], "meta": {"next_page": None}}))
    detail = respx.get(f"{api}/2").mock(return_value=httpx.Response(200, json={"description": "<p>Build implants</p>"}))
    jobs = await fetch("polymer", "axoft", known_ids={"1"})
    assert [j.external_id for j in jobs] == ["1", "2"] and detail.called
    assert jobs[1].description == "Build implants" and not jobs[1].remote and jobs[1].locations == ["Cambridge, MA"]


SITEMAP = """<?xml version="1.0"?><urlset>
<url><loc>https://easeinc.applytojob.com/apply/vMHpWDZa3l/Data-Analyst?source=GS</loc></url>
<url><loc>https://brennancenter.applytojob.com/apply/WpIfUaLo9Q/Associate-Editor?source=GS</loc></url>
</urlset>"""
PAGE = """<script type="application/ld+json">{"@type": "JobPosting", "title": "Data Analyst", "datePosted": "2026-11-04",
"hiringOrganization": {"name": "Ease Inc"}, "jobLocation": {"address": {"addressLocality": "Austin", "addressRegion": "TX"}},
"description": "<p>SQL dashboards</p>"}</script>"""


@pytest.mark.asyncio
@respx.mock
async def test_jazzhr_lists_every_employer_and_reads_new_job_pages():
    respx.get("http://app.jazz.co/feeds/google/xml/0").mock(return_value=httpx.Response(200, text=SITEMAP))
    respx.get("http://app.jazz.co/feeds/google/xml/1").mock(return_value=httpx.Response(404))
    page = respx.get("https://easeinc.applytojob.com/apply/vMHpWDZa3l/Data-Analyst").mock(return_value=httpx.Response(200, text=PAGE))
    first = await fetch("jazzhr", "all", first_scan=True)
    assert [(j.external_id, j.title, j.company_name) for j in first] == [
        ("easeinc/vMHpWDZa3l", "Data Analyst", "Easeinc"), ("brennancenter/WpIfUaLo9Q", "Associate Editor", "Brennancenter")]
    assert not page.called  # a first scan saves URLs only
    later = await fetch("jazzhr", "all", known_ids={"brennancenter/WpIfUaLo9Q"})
    assert page.called and later[0].company_name == "Ease Inc" and later[0].description == "SQL dashboards"
    assert later[0].posted_at.date().isoformat() == "2026-11-04" and later[0].locations
