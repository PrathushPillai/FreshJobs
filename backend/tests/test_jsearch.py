import httpx
import pytest
import respx

from app.sources.jsearch import JSearchAdapter

RESPONSE = {
    "status": "OK",
    "data": [
        {
            "job_id": "abc123==",
            "employer_name": "Acme Analytics",
            "job_publisher": "LinkedIn",
            "job_title": "Data Analyst",
            "job_apply_link": "https://www.linkedin.com/jobs/view/1",
            "apply_options": [
                {"publisher": "LinkedIn", "apply_link": "https://www.linkedin.com/jobs/view/1", "is_direct": False},
                {"publisher": "Acme Careers", "apply_link": "https://careers.acme.com/1", "is_direct": True},
            ],
            "job_description": "Analyze data. 2+ years of SQL experience.",
            "job_is_remote": False,
            "job_posted_at_datetime_utc": "2026-11-03T14:00:00.000Z",
            "job_city": "Austin",
            "job_state": "TX",
            "job_country": "US",
            "job_highlights": {"Qualifications": ["Bachelor's degree"]},
        }
    ],
}


@pytest.mark.asyncio
@respx.mock
async def test_jsearch_parses_and_prefers_direct_apply(monkeypatch):
    monkeypatch.setattr("app.sources.jsearch.settings.jsearch_api_key", "k")
    route = respx.get("https://jsearch.p.rapidapi.com/search-v2").mock(return_value=httpx.Response(200, json=RESPONSE))
    async with httpx.AsyncClient() as client:
        [job] = await JSearchAdapter().fetch(client, "query=data analyst in USA&date_posted=3days")
    params = route.calls.last.request.url.params
    assert params["query"] == "data analyst in USA" and params["date_posted"] == "3days"
    assert route.calls.last.request.headers["X-RapidAPI-Key"] == "k"
    assert job.company_name == "Acme Analytics"
    assert job.url == "https://careers.acme.com/1"
    assert job.location == "Austin, TX, US"
    assert "Bachelor's degree" in job.description and job.department == "via LinkedIn"
    assert job.posted_at.isoformat() == "2026-11-03T14:00:00+00:00"


def test_jsearch_needs_key(monkeypatch):
    monkeypatch.setattr("app.sources.jsearch.settings.jsearch_api_key", "")
    assert not JSearchAdapter().is_configured()


@pytest.mark.asyncio
@respx.mock
async def test_jsearch_rejects_unexpected_shapes(monkeypatch):
    monkeypatch.setattr("app.sources.jsearch.settings.jsearch_api_key", "k")
    respx.get("https://jsearch.p.rapidapi.com/search-v2").mock(
        return_value=httpx.Response(200, json={"status": "ERROR", "data": "quota exceeded"})
    )
    async with httpx.AsyncClient() as client:
        with pytest.raises(ValueError, match="unexpected JSearch response"):
            await JSearchAdapter().fetch(client, "query=x")


@pytest.mark.asyncio
@respx.mock
async def test_jsearch_skips_non_job_items(monkeypatch):
    monkeypatch.setattr("app.sources.jsearch.settings.jsearch_api_key", "k")
    body = {"status": "OK", "data": ["ad", RESPONSE["data"][0]]}
    respx.get("https://jsearch.p.rapidapi.com/search-v2").mock(return_value=httpx.Response(200, json=body))
    async with httpx.AsyncClient() as client:
        jobs = await JSearchAdapter().fetch(client, "query=x")
    assert [j.external_id for j in jobs] == ["abc123=="]
