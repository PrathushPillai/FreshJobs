"""respx-mocked tests for the Rippling, BambooHR, Recruitee, Breezy, Pinpoint and SimplifyJobs adapters,
plus board harvesting from aggregator links.

Fixtures are trimmed-down copies of real responses captured in Nov 2026.
"""


from datetime import timezone

import httpx
import pytest
import respx

from app.models import Company
from app.pipeline.detect import detect_board
from app.pipeline.harvest import harvest
from app.sources import ADAPTERS


async def fetch(ats: str, slug: str, **kwargs):
    async with httpx.AsyncClient() as client:
        return await ADAPTERS[ats].fetch(client, slug, **kwargs)


@pytest.mark.asyncio
@respx.mock
async def test_rippling_fetches_details_only_for_new_jobs():
    api = "https://api.rippling.com/platform/api/ats/v1/board/flexai/jobs"
    respx.get(api).mock(return_value=httpx.Response(200, json=[
        {"uuid": "a1", "name": "Backend Engineer", "url": "https://ats.rippling.com/flexai/jobs/a1",
         "department": {"label": "Engineering"}, "workLocation": {"label": "Santa Clara, CA"}},
        {"uuid": "b2", "name": "Data Analyst", "url": "https://ats.rippling.com/flexai/jobs/b2",
         "department": {"label": "Data"}, "workLocation": {"label": "Remote (US)"}},
    ]))
    detail = respx.get(f"{api}/a1").mock(return_value=httpx.Response(200, json={
        "description": {"company": "<p>About FlexAI</p>", "role": "<p>3+ years of Go.</p>"},
        "workLocations": ["San Jose, CA", "Santa Clara, CA"], "createdOn": "2026-03-23T18:05:44.354000-07:00",
    }))
    known = respx.get(f"{api}/b2")
    a1, b2 = await fetch("rippling", "flexai", known_ids={"b2"})
    assert detail.called and not known.called
    assert a1.locations == ["San Jose, CA", "Santa Clara, CA"] and "3+ years of Go." in a1.description
    assert a1.posted_at.astimezone(timezone.utc).isoformat() == "2026-03-24T01:05:44.354000+00:00" and a1.department == "Engineering"
    assert b2.remote and b2.description == ""


@pytest.mark.asyncio
@respx.mock
async def test_bamboohr():
    base = "https://asuresoftware.bamboohr.com/careers"
    respx.get(f"{base}/list").mock(return_value=httpx.Response(200, json={"result": [
        {"id": "774", "jobOpeningName": "Data Analyst ", "departmentLabel": "Finance", "isRemote": None,
         "location": {"city": None, "state": None},
         "atsLocation": {"country": "United States", "state": "California", "city": "San Jose"}},
    ]}))
    respx.get(f"{base}/774/detail").mock(return_value=httpx.Response(200, json={"result": {"jobOpening": {
        "description": "<p>We will not sponsor visas.</p>", "datePosted": "2026-02-09"}}}))
    [job] = await fetch("bamboohr", "asuresoftware")
    assert job.title == "Data Analyst" and job.locations == ["San Jose, California, United States"]
    assert job.url == f"{base}/774" and "not sponsor" in job.description
    assert job.posted_at.isoformat() == "2026-02-09T00:00:00+00:00"


@pytest.mark.asyncio
@respx.mock
async def test_recruitee():
    respx.get("https://hardrockdigital.recruitee.com/api/offers/").mock(return_value=httpx.Response(200, json={"offers": [
        {"id": 2813877, "slug": "senior-devops-engineer-7", "title": "Senior DevOps Engineer",
         "careers_url": "https://hardrockdigital.recruitee.com/o/senior-devops-engineer-7",
         "locations": [{"name": "Hollywood, FL", "city": "Hollywood", "state_code": "FL", "country": "United States"}],
         "remote": False, "published_at": "2026-11-03 16:43:55 UTC", "department": "Technology",
         "description": "<p>Build things.</p>", "requirements": "<ul><li>5+ years</li></ul>"},
    ]}))
    [job] = await fetch("recruitee", "hardrockdigital")
    assert job.external_id == "2813877" and job.locations == ["Hollywood, FL"]
    assert "Build things." in job.description and "5+ years" in job.description
    assert job.posted_at.isoformat() == "2026-11-03T16:43:55+00:00"


BREEZY_PAGE = """<html><div class="description"><p>Experience : 2 to 4 years</p><p>SQL and Excel.</p></div>
<div class="apply-button linkedin-container">Apply</div></html>"""


@pytest.mark.asyncio
@respx.mock
async def test_breezy_reads_description_from_job_page():
    url = "https://nucleusteq.breezy.hr/p/687f31c86ba8-accounts-executive"
    respx.get("https://nucleusteq.breezy.hr/json").mock(return_value=httpx.Response(200, json=[
        {"id": "687f31c86ba8", "name": "Accounts Executive", "url": url, "published_date": "2026-10-26T10:43:52.046Z",
         "department": None, "locations": [{"country": {"name": "United States", "id": "US"}, "city": "Austin",
                                            "state": {"name": "Texas"}, "is_remote": False}]},
    ]))
    respx.get(url).mock(return_value=httpx.Response(200, text=BREEZY_PAGE))
    [job] = await fetch("breezy", "nucleusteq")
    assert job.locations == ["Austin, Texas, United States"] and not job.remote
    assert "SQL and Excel." in job.description and "Apply" not in job.description


@pytest.mark.asyncio
@respx.mock
async def test_pinpoint():
    respx.get("https://wolve.pinpointhq.com/postings.json").mock(return_value=httpx.Response(200, json={"data": [
        {"id": "446898", "title": "Data Engineer", "url": "https://careers.wolve.com/en/postings/856f",
         "location": {"city": "Chicago", "name": "Chicago, IL", "province": "Illinois"}, "workplace_type": "onsite",
         "description": "<p>Pipelines.</p>", "key_responsibilities": "<p>Own ETL.</p>", "benefits": None,
         "job": {"department": {"name": "Technology"}}},
    ]}))
    [job] = await fetch("pinpoint", "wolve")
    assert job.locations == ["Chicago, IL"] and job.department == "Technology"
    assert "Pipelines." in job.description and "Own ETL." in job.description


SIMPLIFY = [
    {"id": "u1", "company_name": "Palantir", "title": "Software Engineer, New Grad", "active": True, "is_visible": True,
     "url": "https://jobs.lever.co/palantir/ac0dc094/apply", "locations": ["NYC", "Remote in USA"],
     "date_posted": 1793700000, "category": "Software", "sponsorship": "Other", "degrees": ["Bachelor's"]},
    {"id": "u2", "company_name": "Anduril", "title": "Data Analyst", "active": True, "is_visible": True,
     "url": "https://boards.greenhouse.io/andurilindustries/jobs/1", "locations": ["Costa Mesa, CA"],
     "date_posted": 1793700000, "sponsorship": "U.S. Citizenship is Required"},
    {"id": "u3", "company_name": "Old Co", "title": "Analyst", "active": False, "url": "https://x", "date_posted": 1},
    {"id": "u4", "company_name": "Hidden", "title": "Analyst", "active": True, "is_visible": False, "url": "https://y"},
]


@pytest.mark.asyncio
@respx.mock
async def test_simplify_keeps_active_visible_listings():
    route = respx.get("https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/.github/scripts/listings.json")
    route.mock(return_value=httpx.Response(200, json=SIMPLIFY))
    jobs = await fetch("simplify", "SimplifyJobs/New-Grad-Positions")
    assert [j.external_id for j in jobs] == ["u1", "u2"]
    assert jobs[0].company_name == "Palantir" and jobs[0].remote and jobs[0].posted_at.year == 2026
    from app.pipeline.sponsorship import find_no_sponsor_phrase
    assert find_no_sponsor_phrase(jobs[1].description) and not find_no_sponsor_phrase(jobs[0].description)


def test_simplify_is_a_complete_aggregator():
    adapter = ADAPTERS["simplify"]
    assert adapter.complete_feed and adapter.aggregator


@pytest.mark.parametrize("url, expected", [
    ("https://ats.rippling.com/flexai/jobs/93ada67c", ("rippling", "flexai")),
    ("https://asuresoftware.bamboohr.com/careers/1053/", ("bamboohr", "asuresoftware")),
    ("https://hardrockdigital.recruitee.com/o/associate-pm", ("recruitee", "hardrockdigital")),
    ("https://forge-nano.breezy.hr/p/66630c61f7e4/apply", ("breezy", "forge-nano")),
    ("https://wolve.pinpointhq.com/en/postings/e03d9864?ats=pinpointhq", ("pinpoint", "wolve")),
    ("https://www.bamboohr.com/pricing", None),
])
def test_detect_small_ats_boards(url, expected):
    assert detect_board(url) == expected


def test_harvest_adds_untracked_boards_most_linked_first(session):
    session.add(Company(name="Palantir", ats="lever", slug="palantir"))
    session.flush()
    links = [
        ("Palantir", "https://jobs.lever.co/palantir/1"),  # already tracked
        ("NVIDIA", "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite/job/x_JR1"),
        ("NVIDIA", "https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite/job/y_JR2"),
        ("Anduril", "https://boards.greenhouse.io/andurilindustries/jobs/1"),
        ("Amazon", "https://www.amazon.jobs/en/jobs/123"),  # big-company site, tracked by hand
        ("Acme", "https://acme.com/careers/1"),  # not an ATS we know
    ]
    added = harvest(session, links, limit=1)
    assert [(c.name, c.ats, c.slug) for c in added] == [("NVIDIA", "workday", "nvidia|wd5|NVIDIAExternalCareerSite")]
    session.flush()
    added = harvest(session, links, limit=10)
    assert [(c.ats, c.slug) for c in added] == [("greenhouse", "andurilindustries")]


def test_harvest_ignores_case_differences(session):
    session.add(Company(name="Figma", ats="greenhouse", slug="figma"))
    session.flush()
    assert harvest(session, [("Figma", "https://boards.greenhouse.io/Figma/jobs/1")], limit=5) == []
