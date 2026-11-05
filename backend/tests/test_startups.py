"""respx-mocked tests for the startup sources: YC "Work at a Startup" pages, HN "Who is hiring?",
and the YC hiring-company sync. Fixtures are trimmed copies of real responses (Nov 2026)."""

import html
import json

import httpx
import pytest
import respx

from app.models import Company
from app.pipeline.sponsorship import find_no_sponsor_phrase
from app.pipeline.startups import YC_HIRING, sync_yc
from app.sources import ADAPTERS
from app.sources.hackernews import parse_header
from app.sources.yc import visa_line


def yc_page(component: str, props: dict, extra: str = "") -> str:
    data = html.escape(json.dumps({"component": component, "props": props}), quote=True)
    return f'<html><head>{extra}</head><div id="root" data-page="{data}"></div></html>'


LISTING = {
    "id": 81917, "title": "Computer Vision Engineer", "url": "/companies/circuithub/jobs/js2U2cL-cv-engineer",
    "location": "Boston, MA / Remote (US)", "prettyRole": "Engineering", "salaryRange": "$120K - $150K",
    "minExperience": "Any (new grads ok)", "visa": "Will sponsor", "skills": ["PyTorch"],
}


@pytest.mark.asyncio
@respx.mock
async def test_yc_jobs_page_and_detail():
    respx.get("https://www.ycombinator.com/companies/circuithub/jobs").mock(
        return_value=httpx.Response(200, text=yc_page("WaasShowJobsPage", {"jobPostings": [LISTING]})))
    ld = '<script type="application/ld+json">{"@type": "JobPosting", "datePosted": "2026-11-01T10:00:00Z"}</script>'
    job_props = {"job": {"description": "### About\n\nWe build robots.", "hiringManager": {"first_name": "Andrew", "last_name": "S"}}}
    respx.get("https://www.ycombinator.com" + LISTING["url"]).mock(
        return_value=httpx.Response(200, text=yc_page("WaasShowJobPage", job_props, ld)))
    async with httpx.AsyncClient() as client:
        [job] = await ADAPTERS["yc"].fetch(client, "circuithub")
    assert job.external_id == "81917" and job.locations == ["Boston, MA", "Remote (US)"] and job.remote
    assert "We build robots." in job.description and "Hiring manager: Andrew S." in job.description
    assert "Visa sponsorship: Will sponsor." in job.description and not find_no_sponsor_phrase(job.description)
    assert job.posted_at.isoformat() == "2026-11-01T10:00:00+00:00"


def test_yc_visa_policy_feeds_sponsorship_detector():
    assert find_no_sponsor_phrase(visa_line("US citizen/visa only"))
    assert not find_no_sponsor_phrase(visa_line("Will sponsor"))
    assert visa_line("Unknown") == ""


@pytest.mark.parametrize("text, expected", [
    ("Synteq | Growth Lead &amp; Systems Administrator | Austin, TX | REMOTE (US) | Full-time<p>We run GPUs.",
     ("Synteq", "Growth Lead & Systems Administrator", ["Austin, TX", "REMOTE (US)"], True)),
    ("Shepherd (Series B) | ONSITE | San Francisco, CA<p>Autonomous underwriting.",
     ("Shepherd (Series B)", "Open roles (see post)", ["San Francisco, CA"], False)),
    ("Kanmon | Full Stack Engineer (2-4 YOE) | San Mateo, CA | $180k | Visa sponsorship possible",
     ("Kanmon", "Full Stack Engineer (2-4 YOE)", ["San Mateo, CA"], False)),
    ("Just some text without a header", ("", "", [], False)),
])
def test_hn_header_parsing(text, expected):
    assert parse_header(text) == expected


@pytest.mark.asyncio
@respx.mock
async def test_hn_uses_latest_hiring_thread_and_prefers_ats_links():
    respx.get("https://hn.algolia.com/api/v1/search_by_date").mock(return_value=httpx.Response(200, json={"hits": [
        {"title": "Ask HN: Who wants to be hired? (November 2026)", "objectID": "1"},
        {"title": "Ask HN: Who is hiring? (November 2026)", "objectID": "2"},
    ]}))
    respx.get("https://hn.algolia.com/api/v1/items/2").mock(return_value=httpx.Response(200, json={"children": [
        {"id": 11, "created_at": "2026-11-02T15:00:00Z",
         "text": 'United Risk | Data Analyst | New York, NY<p>Apply: <a href="https:&#x2F;&#x2F;jobs.lever.co&#x2F;auw&#x2F;9234">here</a>'},
        {"id": 12, "created_at": "2026-11-02T15:01:00Z", "text": None},  # deleted
    ]}))
    async with httpx.AsyncClient() as client:
        [job] = await ADAPTERS["hackernews"].fetch(client, "whoishiring")
    assert job.company_name == "United Risk" and job.title == "Data Analyst"
    assert job.url == "https://jobs.lever.co/auw/9234"
    assert ADAPTERS["hackernews"].aggregator


@pytest.mark.asyncio
@respx.mock
async def test_sync_yc_adds_us_hiring_startups_not_tracked(session):
    session.add(Company(name="Stripe", ats="greenhouse", slug="stripe"))
    session.flush()
    respx.get(YC_HIRING).mock(return_value=httpx.Response(200, json=[
        {"name": "CircuitHub", "slug": "circuithub", "isHiring": True, "status": "Active", "regions": ["Remote"]},
        {"name": "Stripe", "slug": "stripe", "isHiring": True, "status": "Public", "regions": ["United States of America"]},
        {"name": "EuroCo", "slug": "euroco", "isHiring": True, "status": "Active", "regions": ["Europe"]},
        {"name": "Gone", "slug": "gone", "isHiring": True, "status": "Inactive", "regions": ["Remote"]},
    ]))
    async with httpx.AsyncClient() as client:
        added = await sync_yc(session, client)
    assert [(c.name, c.ats, c.slug) for c in added] == [("CircuitHub", "yc", "circuithub")]
