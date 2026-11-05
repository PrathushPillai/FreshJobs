"""Tests for reading jobs straight off a company's own careers page (sources/website.py)."""

import json

import httpx
import pytest
import respx

from app.sources import ADAPTERS
from app.sources.website import inline_jobs, job_links

DESCRIPTION = """<p>Location: Boston, MA (Hybrid)</p><p>Experience Level: 0-1 Years</p>
<h3>What you'll do</h3><p>Be a go-to person for customers over email, chat and calls, helping admins get unstuck.</p>
<h3>Who you are</h3><p>You love helping people and explaining things clearly. Requirements: excellent English.</p>
<p>Salary: $50,000-$75,000. To apply, email jobs@example.com with your resume.</p>"""

# The shape of wizer-training.com/careers: an accordion of jobs, each a heading plus its description.
INLINE_PAGE = f"""<html><h1>Wizer Careers</h1><p>We're a small team making a real difference.</p>
<h2><span>Support Specialist (Hybrid)</span>   <span>Boston, MA</span></h2><div>{DESCRIPTION}</div>
<h2>MDF Program Application</h2><p>Partners can apply for marketing funds here.</p>
<h2>Contact Sales</h2></html>"""


def test_inline_jobs_reads_role_sections_only():
    [job] = inline_jobs(INLINE_PAGE, "https://acme.com/careers")
    assert job.title == "Support Specialist (Hybrid)"
    assert job.locations == ["Boston, MA (Hybrid)"] and job.url == "https://acme.com/careers"
    assert "go-to person" in job.description


@pytest.mark.asyncio
@respx.mock
async def test_website_prefers_json_ld_job_postings():
    ld = {"@context": "https://schema.org", "@graph": [
        {"@type": "JobPosting", "title": "Data Analyst", "identifier": {"value": "DA-7"}, "datePosted": "2026-11-03T13:00:00Z",
         "description": "&lt;p&gt;SQL and Tableau.&lt;/p&gt;", "url": "https://acme.com/careers/data-analyst",
         "jobLocation": {"address": {"addressLocality": "Austin", "addressRegion": "TX", "addressCountry": "US"}}},
    ]}
    page = f'<html><script type="application/ld+json">{json.dumps(ld)}</script></html>'
    respx.get("https://acme.com/careers").mock(return_value=httpx.Response(200, text=page))
    async with httpx.AsyncClient() as client:
        [job] = await ADAPTERS["website"].fetch(client, "https://acme.com/careers")
    assert job.external_id == "DA-7" and job.title == "Data Analyst" and job.locations == ["Austin, TX, US"]
    assert job.description == "SQL and Tableau." and job.posted_at.hour == 13


@pytest.mark.asyncio
@respx.mock
async def test_website_follows_job_links_and_skips_non_jobs():
    listing = """<a href="/careers/data-analyst">Data Analyst</a><a href="/careers/life-at-acme">Life at Acme</a>
                 <a href="https://boards.greenhouse.io/acme/jobs/1">Engineer</a><a href="/blog/post">Blog</a>"""
    respx.get("https://acme.com/careers").mock(return_value=httpx.Response(200, text=listing))
    respx.get("https://acme.com/careers/data-analyst").mock(
        return_value=httpx.Response(200, text=f"<h1>Data Analyst</h1>{DESCRIPTION}"))
    respx.get("https://acme.com/careers/life-at-acme").mock(
        return_value=httpx.Response(200, text="<h1>Life at Acme</h1><p>We have snacks.</p>"))
    assert set(job_links(listing, "https://acme.com/careers")) == {
        "https://acme.com/careers/data-analyst", "https://acme.com/careers/life-at-acme"}
    async with httpx.AsyncClient() as client:
        jobs = await ADAPTERS["website"].fetch(client, "https://acme.com/careers")
        assert [(j.title, j.external_id) for j in jobs] == [("Data Analyst", "https://acme.com/careers/data-analyst")]
        # Known jobs aren't fetched again.
        detail = respx.get("https://acme.com/careers/data-analyst")
        calls = detail.call_count
        await ADAPTERS["website"].fetch(client, "https://acme.com/careers", known_ids={"https://acme.com/careers/data-analyst"})
        assert detail.call_count == calls


@pytest.mark.asyncio
@respx.mock
async def test_add_careers_url_without_ats_uses_website_reader(session):
    from fastapi.testclient import TestClient

    from app.main import app

    respx.get("https://wizer.example/careers").mock(return_value=httpx.Response(200, text=INLINE_PAGE))
    with TestClient(app) as c:
        r = c.post("/api/companies/from-url", json={"url": "https://wizer.example/careers", "name": "Wizer"})
    assert r.status_code == 200, r.text
    assert r.json()["company"]["ats"] == "website" and r.json()["company"]["slug"] == "https://wizer.example/careers"
