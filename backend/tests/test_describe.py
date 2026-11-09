import httpx
import pytest
import respx

from app.pipeline.describe import fetch_description


@pytest.mark.asyncio
@respx.mock
async def test_lever_description_comes_from_every_section():
    respx.get("https://api.lever.co/v0/postings/pigment/3401c965-2e1a-426d-8d58-9ca56c5054bf").mock(
        return_value=httpx.Response(200, json={"descriptionPlain": "", "lists": [{"text": "Your mission", "content": "<li>Build</li>"}]}))
    async with httpx.AsyncClient() as client:
        text = await fetch_description(client, "https://jobs.lever.co/pigment/3401c965-2e1a-426d-8d58-9ca56c5054bf")
    assert text == "Your mission\nBuild"


@pytest.mark.asyncio
@respx.mock
async def test_workday_greenhouse_and_json_ld():
    respx.get("https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/Careers/job/Austin/Intern_R1").mock(
        return_value=httpx.Response(200, json={"jobPostingInfo": {"jobDescription": "<p>Workday text</p>"}}))
    respx.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs/123").mock(
        return_value=httpx.Response(200, json={"content": "&lt;p&gt;Greenhouse text&lt;/p&gt;"}))
    page = '<script type="application/ld+json">{"@type":"JobPosting","description":"<p>From the page</p>"}</script>'
    respx.get("https://careers.example.com/jobs/9").mock(return_value=httpx.Response(200, text=page))
    respx.get("https://careers.example.com/jobs/gone").mock(return_value=httpx.Response(404))
    async with httpx.AsyncClient() as client:
        assert await fetch_description(client, "https://acme.wd5.myworkdayjobs.com/Careers/job/Austin/Intern_R1") == "Workday text"
        assert await fetch_description(client, "https://job-boards.greenhouse.io/acme/jobs/123") == "Greenhouse text"
        assert await fetch_description(client, "https://careers.example.com/jobs/9") == "From the page"
        assert await fetch_description(client, "https://careers.example.com/jobs/gone") is None
        assert await fetch_description(client, "https://www.linkedin.com/jobs/view/1") is None  # never fetched


def test_short_descriptions_count_as_missing(session, now):
    from app.models import Company
    from app.pipeline.poller import process_company
    from app.prefs import get_prefs
    from app.sources import NormalizedJob

    company = Company(name="Acme", ats="lever", slug="acme")
    session.add(company)
    session.flush()
    jobs = process_company(session, company, [
        NormalizedJob(external_id="1", title="Intern", url="u1", locations=["NY"], description="\n"),
        NormalizedJob(external_id="2", title="Intern", url="u2", locations=["NY"], description="Category: Software\nTerms: Summer 2026"),
        NormalizedJob(external_id="3", title="Intern", url="u3", locations=["NY"], description="A real description. " * 10),
    ], get_prefs(session), now)
    assert [j.described for j in sorted(jobs, key=lambda j: j.external_id)] == [False, False, True]
