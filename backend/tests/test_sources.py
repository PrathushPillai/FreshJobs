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
