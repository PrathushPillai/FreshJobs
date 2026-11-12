import httpx
import pytest
import respx

from app.sources import ADAPTERS

GREENHOUSE = {
    "jobs": [
        {
            "id": 123,
            "title": " Software Engineer, New Grad ",
            "absolute_url": "https://boards.greenhouse.io/acme/jobs/123",
            "location": {"name": "New York, NY"},
            "updated_at": "2026-11-03T09:00:00-05:00",
            "first_published": "2026-11-03T08:30:00-05:00",
            "content": "&lt;p&gt;Build things.&lt;/p&gt;&lt;p&gt;We will not sponsor visas.&lt;/p&gt;",
            "departments": [{"name": "Engineering"}],
        }
    ]
}
LEVER = [
    {
        "id": "abc-1",
        "text": "Data Scientist",
        "hostedUrl": "https://jobs.lever.co/acme/abc-1",
        "categories": {"location": "Remote - US", "allLocations": ["Remote - US"], "team": "Data"},
        "workplaceType": "remote",
        "createdAt": 1793714400000,
        "descriptionPlain": "Analyze data.",
    }
]
ASHBY = {
    "jobs": [
        {
            "id": "a1",
            "title": "ML Engineer",
            "location": "San Francisco",
            "secondaryLocations": [{"location": "Seattle"}],
            "department": "AI",
            "isRemote": False,
            "isListed": True,
            "publishedAt": "2026-11-03T12:00:00.000+00:00",
            "jobUrl": "https://jobs.ashbyhq.com/acme/a1",
            "descriptionPlain": "Train models.",
        },
        {"id": "hidden", "title": "Unlisted", "isListed": False, "jobUrl": ""},
    ]
}


@pytest.mark.asyncio
@respx.mock
async def test_greenhouse():
    respx.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs").mock(return_value=httpx.Response(200, json=GREENHOUSE))
    async with httpx.AsyncClient() as client:
        [job] = await ADAPTERS["greenhouse"].fetch(client, "acme")
    assert job.external_id == "123"
    assert job.title == "Software Engineer, New Grad"
    assert job.location == "New York, NY"
    assert job.department == "Engineering"
    assert "will not sponsor" in job.description and "<p>" not in job.description
    assert job.posted_at.isoformat() == "2026-11-03T08:30:00-05:00"


@pytest.mark.asyncio
@respx.mock
async def test_lever():
    respx.get("https://api.lever.co/v0/postings/acme").mock(return_value=httpx.Response(200, json=LEVER))
    async with httpx.AsyncClient() as client:
        [job] = await ADAPTERS["lever"].fetch(client, "acme")
    assert job.remote and job.location == "Remote - US"
    assert job.posted_at.year == 2026


@pytest.mark.asyncio
@respx.mock
async def test_ashby_skips_unlisted():
    respx.get("https://api.ashbyhq.com/posting-api/job-board/acme").mock(return_value=httpx.Response(200, json=ASHBY))
    async with httpx.AsyncClient() as client:
        jobs = await ADAPTERS["ashby"].fetch(client, "acme")
    assert [j.external_id for j in jobs] == ["a1"]
    assert jobs[0].location == "San Francisco | Seattle"


@pytest.mark.asyncio
@respx.mock
async def test_greenhouse_later_polls_fetch_details_only_for_new_jobs():
    listed = {"jobs": [
        {"id": 1, "title": "Data Analyst", "absolute_url": "https://x/1", "location": {"name": "NYC"}},
        {"id": 2, "title": "Data Engineer", "absolute_url": "https://x/2", "location": {"name": "Remote"}},
    ]}
    respx.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs").mock(return_value=httpx.Response(200, json=listed))
    new = respx.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs/2").mock(return_value=httpx.Response(
        200, json={"content": "&lt;p&gt;Pipelines.&lt;/p&gt;", "departments": [{"name": "Data"}]}))
    known = respx.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs/1")
    async with httpx.AsyncClient() as client:
        old, fresh = await ADAPTERS["greenhouse"].fetch(client, "acme", known_ids={"1"})
    assert new.called and not known.called
    assert respx.calls[0].request.url.params.get("content") is None
    assert fresh.description == "Pipelines." and fresh.department == "Data" and fresh.remote
    assert old.title == "Data Analyst" and old.description == ""


def test_greenhouse_job_with_null_location_and_department():
    from app.sources.greenhouse import _job

    job = _job({"id": 1, "title": "Engineer", "absolute_url": "u", "location": {"name": None},
                "departments": [{"name": None}], "content": "x"})
    assert job.locations == [] and job.remote is False and job.department == ""


@pytest.mark.asyncio
@respx.mock
async def test_known_jobs_skip_their_description_on_ashby_lever_and_gem():
    # Converting every job's HTML on every poll was about a fifth of the fast lane's CPU; only new jobs need it.
    from app.sources.ashby import AshbyAdapter
    from app.sources.gem import GemAdapter
    from app.sources.lever import LeverAdapter

    body = "<p>" + "Build data pipelines in Spark and SQL. " * 5 + "</p>"
    respx.get("https://api.ashbyhq.com/posting-api/job-board/acme").mock(return_value=httpx.Response(200, json={"jobs": [
        {"id": "a1", "title": "Data Engineer", "jobUrl": "https://x/a1", "descriptionHtml": body},
        {"id": "a2", "title": "ML Engineer", "jobUrl": "https://x/a2", "descriptionHtml": body}]}))
    respx.get("https://api.lever.co/v0/postings/acme").mock(return_value=httpx.Response(200, json=[
        {"id": "l1", "text": "Data Engineer", "hostedUrl": "https://x/l1", "descriptionPlain": body},
        {"id": "l2", "text": "ML Engineer", "hostedUrl": "https://x/l2", "descriptionPlain": body}]))
    respx.get(url__regex=r"https://api\.gem\.com/job_board/v0/acme/.*").mock(return_value=httpx.Response(200, json=[
        {"id": 1, "title": "Data Engineer", "absolute_url": "https://x/g1", "content": body},
        {"id": 2, "title": "ML Engineer", "absolute_url": "https://x/g2", "content": body}]))
    async with httpx.AsyncClient() as client:
        ashby = await AshbyAdapter().fetch(client, "acme", known_ids={"a1"})
        lever = await LeverAdapter().fetch(client, "acme", known_ids={"l1"})
        gem = await GemAdapter().fetch(client, "acme", known_ids={"1"})
    for jobs, known in ((ashby, "a1"), (lever, "l1"), (gem, "1")):
        by_id = {j.external_id: j for j in jobs}
        assert by_id[known].description == "" and by_id[known].title == "Data Engineer"
        assert all("Spark and SQL" in j.description for k, j in by_id.items() if k != known)
