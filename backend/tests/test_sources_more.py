"""respx-mocked tests for the Workday, SmartRecruiters, Workable and aggregator adapters.

Fixtures are trimmed-down copies of real responses captured in Nov 2026 (Adzuna's is built from
its documented response format, since we have no key).
"""

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx

from app.config import settings
from app.sources import ADAPTERS
from app.sources.remoteok import fix_mojibake
from app.sources.workday import WorkdayAdapter, _pinned_end, days_ago, job_id, parse_slug

# --- Workday -----------------------------------------------------------------------------------

WD_HOST = "https://nvidia.wd5.myworkdayjobs.com"
WD_API = f"{WD_HOST}/wday/cxs/nvidia/NVIDIAExternalCareerSite"
WD_SLUG = "nvidia|wd5|NVIDIAExternalCareerSite"

WD_POSTING = {
    "title": "Technical Product Manager - AI Infra Resilience",
    "externalPath": "/job/US-CA-Santa-Clara/Technical-Product-Manager---AI-Infra-Resilience_JR2019819",
    "locationsText": "3 Locations",
    "postedOn": "Posted Today",
    "bulletFields": ["JR2019819"],
}
WD_DETAIL = {
    "jobPostingInfo": {
        "id": "c8ced1f12535101117bcba42fb7d0000",
        "title": "Technical Product Manager - AI Infra Resilience",
        "jobDescription": "<p>GPU clusters fail.</p><p>NVIDIA is unable to sponsor visas for this role.</p>",
        "location": "US, CA, Santa Clara",
        "additionalLocations": ["US, NY, New York", "US, WA, Seattle"],
        "postedOn": "Posted Today",
        "startDate": "2026-11-03",
        "timeType": "Full time",
        "jobReqId": "JR2019819",
        "externalUrl": f"{WD_HOST}/NVIDIAExternalCareerSite/job/US-CA-Santa-Clara/Technical-Product-Manager---AI-Infra-Resilience_JR2019819",
    },
    "hiringOrganization": {"name": "2100 NVIDIA USA", "url": ""},
}


def wd_posting(i: int, posted_on: str = "Posted 3 Days Ago") -> dict:
    return {
        "title": f"Engineer {i}",
        "externalPath": f"/job/US-TX-Austin/Engineer-{i}_JR{1000 + i}-1",
        "locationsText": "US, TX, Austin",
        "postedOn": posted_on,
        "bulletFields": [f"JR{1000 + i}"],
    }


def wd_pages(postings: list[dict], *, wrap: bool = False):
    """A side effect serving `postings` 20 at a time, like Workday (total only on the first page)."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        offset = body["offset"]
        calls.append(offset)
        assert body["limit"] == 20
        if wrap and offset >= len(postings):
            offset = 0  # Workday wraps back to the start past the end
        page = postings[offset : offset + 20]
        return httpx.Response(200, json={"total": len(postings) if offset == 0 else 0, "jobPostings": page, "facets": []})

    return handler, calls


def test_workday_helpers():
    assert parse_slug(WD_SLUG) == ("nvidia", "wd5", "NVIDIAExternalCareerSite")
    with pytest.raises(ValueError):
        parse_slug("nvidia")
    assert [days_ago(s) for s in ("Posted Today", "Posted Yesterday", "Posted 3 Days Ago", "Posted 30+ Days Ago", "")] == [
        0, 1, 3, 30, None,
    ]
    assert job_id(WD_POSTING["externalPath"]) == "JR2019819"
    assert job_id("/job/Boston/Senior-Machine-Learning-Engineer_R-060246") == "R-060246"
    assert job_id("/job/Israel-Tel-Aviv/Software-Embedded-Engineer--DOCA_JR2027291-1") == "JR2027291-1"
    # A pinned block (fresh, then old), then the real newest-first list.
    seq = ["Posted Today", "Posted 9 Days Ago", "Posted 30+ Days Ago", "Posted Today", "Posted Yesterday"]
    assert _pinned_end([{"postedOn": s} for s in seq]) == 3
    assert _pinned_end([{"postedOn": s} for s in ("Posted Today", "Posted Yesterday", "Posted 4 Days Ago")]) == 0


@pytest.mark.asyncio
@respx.mock
async def test_workday_first_scan_fetches_details_only_for_recent_jobs():
    old = {**WD_POSTING, "externalPath": "/job/US-TX-Austin/Old-Job_JR9", "postedOn": "Posted 30+ Days Ago"}
    handler, _ = wd_pages([WD_POSTING, old])
    respx.post(f"{WD_API}/jobs").mock(side_effect=handler)
    fresh = respx.get(f"{WD_API}{WD_POSTING['externalPath']}").mock(return_value=httpx.Response(200, json=WD_DETAIL))
    stale = respx.get(f"{WD_API}{old['externalPath']}")

    async with httpx.AsyncClient() as client:
        jobs = await WorkdayAdapter().fetch(client, WD_SLUG, full=True, known_ids=set(), first_scan=True)

    assert fresh.called and not stale.called
    assert {j.external_id for j in jobs} == {"JR2019819", "JR9"}


@pytest.mark.asyncio
@respx.mock
async def test_workday_full_scan_fetches_details_only_for_new_jobs():
    postings = [WD_POSTING] + [wd_posting(i) for i in range(24)]
    handler, calls = wd_pages(postings)
    respx.post(f"{WD_API}/jobs").mock(side_effect=handler)
    detail = respx.get(f"{WD_API}{WD_POSTING['externalPath']}").mock(return_value=httpx.Response(200, json=WD_DETAIL))
    known = {job_id(p["externalPath"]) for p in postings[1:]}

    async with httpx.AsyncClient() as client:
        jobs = await WorkdayAdapter().fetch(client, WD_SLUG, full=True, known_ids=known)

    assert calls == [0, 20]
    assert detail.call_count == 1 and len(respx.calls) == 3
    assert len(jobs) == 25
    new = next(j for j in jobs if j.external_id == "JR2019819")
    assert new.url == WD_DETAIL["jobPostingInfo"]["externalUrl"]
    assert new.locations == ["US, CA, Santa Clara", "US, NY, New York", "US, WA, Seattle"]
    assert "unable to sponsor" in new.description and "<p>" not in new.description
    assert new.posted_at == datetime(2026, 11, 3, tzinfo=timezone.utc)
    assert not new.remote and new.company_name == ""
    known_job = next(j for j in jobs if j.external_id == "JR1000-1")
    assert known_job.description == "" and known_job.locations == ["US, TX, Austin"]
    assert known_job.url == f"{WD_HOST}/NVIDIAExternalCareerSite/job/US-TX-Austin/Engineer-0_JR1000-1"
    expected = (datetime.now(timezone.utc) - timedelta(days=3)).date()
    assert known_job.posted_at.date() == expected and known_job.posted_at.tzinfo is not None


@pytest.mark.asyncio
@respx.mock
async def test_workday_stops_when_pages_wrap_around():
    postings = [wd_posting(i) for i in range(20)]
    handler, calls = wd_pages(postings, wrap=True)
    # The first page claims more jobs than exist, so the adapter must notice the repeat.
    respx.post(f"{WD_API}/jobs").mock(
        side_effect=lambda req: (
            httpx.Response(200, json={"total": 60, "jobPostings": postings})
            if json.loads(req.content)["offset"] == 0
            else handler(req)
        )
    )
    async with httpx.AsyncClient() as client:
        jobs = await WorkdayAdapter().fetch(client, WD_SLUG, known_ids={job_id(p["externalPath"]) for p in postings})
    assert len(jobs) == 20 and len(respx.calls) == 2


@pytest.mark.asyncio
@respx.mock
async def test_workday_partial_scan_reads_only_the_newest_pages():
    # 100 jobs: the first page is fresh, the rest older. Everything is known already.
    postings = [wd_posting(i, "Posted Today") for i in range(20)] + [wd_posting(i) for i in range(20, 100)]
    known = {job_id(p["externalPath"]) for p in postings}
    handler, calls = wd_pages(postings)
    respx.post(f"{WD_API}/jobs").mock(side_effect=handler)
    adapter = WorkdayAdapter()
    async with httpx.AsyncClient() as client:
        # No pinned offset learned yet, so a partial request does a full scan.
        assert len(await adapter.fetch(client, WD_SLUG, full=False, known_ids=known)) == 100
        assert calls == [0, 20, 40, 60, 80]
        calls.clear()
        jobs = await adapter.fetch(client, WD_SLUG, full=False, known_ids=known)
    # Page 1 has fresh jobs, page 2 has nothing fresh or unknown: stop there.
    assert calls == [0, 20] and len(jobs) == 40


@pytest.mark.asyncio
@respx.mock
async def test_workday_partial_scan_starts_after_pinned_block():
    pinned = [wd_posting(i, "Posted 40 Days Ago" if i else "Posted Today") for i in range(25)]
    newest = [wd_posting(i, "Posted Today") for i in range(25, 45)] + [wd_posting(i) for i in range(45, 80)]
    postings = pinned + newest
    known = {job_id(p["externalPath"]) for p in postings}
    handler, calls = wd_pages(postings)
    respx.post(f"{WD_API}/jobs").mock(side_effect=handler)
    adapter = WorkdayAdapter()
    async with httpx.AsyncClient() as client:
        await adapter.fetch(client, WD_SLUG, full=True, known_ids=known)
        assert adapter._pinned[WD_SLUG] == 20  # the fresh list starts at index 25 -> page offset 20
        calls.clear()
        await adapter.fetch(client, WD_SLUG, full=False, known_ids=known)
    assert calls == [0, 20, 40, 60]


@pytest.mark.asyncio
@respx.mock
async def test_workday_skips_new_job_whose_detail_fails():
    handler, _ = wd_pages([WD_POSTING])
    respx.post(f"{WD_API}/jobs").mock(side_effect=handler)
    respx.get(f"{WD_API}{WD_POSTING['externalPath']}").mock(return_value=httpx.Response(404, json={"errorCode": "S21"}))
    async with httpx.AsyncClient() as client:
        assert await WorkdayAdapter().fetch(client, WD_SLUG) == []


@pytest.mark.asyncio
@respx.mock
async def test_workday_list_error_raises():
    respx.post(f"{WD_API}/jobs").mock(return_value=httpx.Response(422, json={"errorCode": "HTTP_422"}))
    async with httpx.AsyncClient() as client:
        with pytest.raises(httpx.HTTPStatusError):
            await WorkdayAdapter().fetch(client, WD_SLUG)


@pytest.mark.asyncio
@respx.mock
async def test_workday_host_without_a_wd_number():
    # NXP's wd3 host shows Workday's maintenance page; the tenant answers at nxp.myworkdayjobs.com.
    handler, _ = wd_pages([wd_posting(1)])
    respx.post("https://nxp.myworkdayjobs.com/wday/cxs/nxp/careers/jobs").mock(side_effect=handler)
    async with httpx.AsyncClient() as client:
        jobs = await WorkdayAdapter().fetch(client, "nxp|-|careers", known_ids={"JR1001-1"})
    assert [j.url for j in jobs] == ["https://nxp.myworkdayjobs.com/careers/job/US-TX-Austin/Engineer-1_JR1001-1"]


def test_workday_hosts_without_a_wd_number_are_detected():
    from app.pipeline.bulk_discovery import _workday_url
    from app.pipeline.detect import detect_board
    from app.pipeline.liveness import _workday_api

    assert detect_board("https://nxp.myworkdayjobs.com/careers") == ("workday", "nxp|-|careers")
    assert detect_board("https://nxp.myworkdayjobs.com/en-US/careers/job/Austin/Engineer_R1") == ("workday", "nxp|-|careers")
    assert detect_board("https://www.myworkdayjobs.com/") is None
    assert _workday_api("https://nxp.myworkdayjobs.com/careers/job/Austin/Engineer_R1") == (
        "https://nxp.myworkdayjobs.com/wday/cxs/nxp/careers/job/Austin/Engineer_R1")
    assert _workday_url("nxp|-|careers") == "https://nxp.myworkdayjobs.com/careers"
    assert _workday_url("nvidia|wd5|Ext") == "https://nvidia.wd5.myworkdayjobs.com/Ext"


# --- SmartRecruiters ---------------------------------------------------------------------------

SR_API = "https://api.smartrecruiters.com/v1/companies/ServiceNow/postings"
SR_POSTING = {
    "id": "744000159461620",
    "name": "Sr. Solution Sales Executive",
    "refNumber": "JB0075235",
    "company": {"identifier": "ServiceNow", "name": "ServiceNow"},
    "releasedDate": "2026-11-03T17:43:15.052Z",
    "location": {
        "city": "Kirkland", "region": "Washington", "country": "us", "remote": True, "hybrid": False,
        "fullLocation": "Kirkland, Washington, United States",
    },
    "department": {"id": "2294208", "label": "Sales"},
    "function": {"id": "sales", "label": "Sales"},
    "ref": "https://api.smartrecruiters.com/v1/companies/ServiceNow/postings/744000159461620",
}
SR_DETAIL = {
    **SR_POSTING,
    "postingUrl": "https://jobs.smartrecruiters.com/ServiceNow/744000159461620-sr-solution-sales-executive",
    "jobAd": {
        "sections": {
            "companyDescription": {"title": "Company Description", "text": "<p>It all started when engineer Fred Luddy wrote code.</p>"},
            "jobDescription": {"title": "Job Description", "text": "<p>You will drive new business sales revenue.</p>"},
            "qualifications": {"title": "Qualifications", "text": "<ul><li>10+ years of sales</li></ul>"},
            "additionalInformation": {"title": "Additional Information", "text": "<p>We do not sponsor visas.</p>"},
        }
    },
}


def sr_posting(i: int) -> dict:
    return {**SR_POSTING, "id": str(i), "name": f"Role {i}", "location": {"city": "Remote", "country": "us"}}


@pytest.mark.asyncio
@respx.mock
async def test_smartrecruiters_paginates_and_fetches_new_details():
    page1 = {"offset": 0, "limit": 100, "totalFound": 101, "content": [SR_POSTING] + [sr_posting(i) for i in range(99)]}
    page2 = {"offset": 100, "limit": 100, "totalFound": 101, "content": [sr_posting(99)]}
    lst = respx.get(SR_API).mock(side_effect=[httpx.Response(200, json=page1), httpx.Response(200, json=page2)])
    respx.get(f"{SR_API}/744000159461620").mock(return_value=httpx.Response(200, json=SR_DETAIL))
    known = {str(i) for i in range(100)}
    async with httpx.AsyncClient() as client:
        jobs = await ADAPTERS["smartrecruiters"].fetch(client, "ServiceNow", full=True, known_ids=known)
    assert lst.call_count == 2 and lst.calls[1].request.url.params["offset"] == "100"
    assert len(jobs) == 101
    job = next(j for j in jobs if j.external_id == "744000159461620")
    assert job.url == SR_DETAIL["postingUrl"]
    assert job.location == "Kirkland, Washington, United States" and job.remote
    assert job.department == "Sales"
    assert job.description.index("drive new business") < job.description.index("Fred Luddy")
    assert "do not sponsor" in job.description and "<p>" not in job.description
    assert job.posted_at == datetime(2026, 11, 3, 17, 43, 15, 52000, tzinfo=timezone.utc)
    other = next(j for j in jobs if j.external_id == "5")
    assert other.url == "https://jobs.smartrecruiters.com/ServiceNow/5" and other.description == ""


@pytest.mark.asyncio
@respx.mock
async def test_smartrecruiters_partial_reads_first_page_only():
    page1 = {"offset": 0, "limit": 100, "totalFound": 500, "content": [sr_posting(i) for i in range(100)]}
    lst = respx.get(SR_API).mock(return_value=httpx.Response(200, json=page1))
    async with httpx.AsyncClient() as client:
        jobs = await ADAPTERS["smartrecruiters"].fetch(client, "ServiceNow", full=False, known_ids={str(i) for i in range(100)})
    assert lst.call_count == 1 and len(jobs) == 100


# --- Workable ----------------------------------------------------------------------------------

WORKABLE = {
    "name": "ECP",
    "description": None,
    "jobs": [
        {
            "title": "Analytics Consultant, ECP Insights",
            "shortcode": "6575AE8110",
            "employment_type": "Full-time",
            "telecommuting": True,
            "department": None,
            "url": "https://apply.workable.com/j/6575AE8110",
            "published_on": "2026-09-23",
            "created_at": "2026-09-23",
            "country": "United States",
            "city": "Denver",
            "state": "Colorado",
            "function": "Data Analyst",
            "locations": [
                {"country": "United States", "countryCode": "US", "city": "Denver", "region": "Colorado", "hidden": False}
            ],
            "description": "<p>ECP is a market-leading SaaS solution.</p><p><strong>Requirements</strong></p><p>Bachelor degree</p>",
        }
    ],
}


@pytest.mark.asyncio
@respx.mock
async def test_workable_widget():
    route = respx.get("https://apply.workable.com/api/v1/widget/accounts/ecp-123").mock(
        return_value=httpx.Response(200, json=WORKABLE)
    )
    async with httpx.AsyncClient() as client:
        [job] = await ADAPTERS["workable"].fetch(client, "ecp-123")
    assert route.calls[0].request.url.params["details"] == "true"
    assert job.external_id == "6575AE8110"
    assert job.location == "Denver, Colorado, United States" and job.remote
    assert job.department == "Data Analyst"
    assert "Requirements" in job.description and "<p>" not in job.description
    assert job.posted_at == datetime(2026, 9, 23, tzinfo=timezone.utc)


# --- Aggregators -------------------------------------------------------------------------------

MUSE_JOB = {
    "contents": "<p><b>Position Summary...</b><br><br>Why do people love eating in our café at Sam's Club?</p>",
    "name": "Grab & Go Food Associate",
    "type": "external",
    "publication_date": "2025-08-13T03:30:37Z",
    "id": 18888634,
    "locations": [{"name": "Springdale, AR"}, {"name": "Flexible / Remote"}],
    "categories": [{"name": "Food and Hospitality Services"}],
    "levels": [{"name": "Entry Level", "short_name": "entry"}],
    "refs": {"landing_page": "https://www.themuse.com/jobs/walmart/grab-go-food-associate-61400a"},
    "company": {"id": 12010, "short_name": "walmart", "name": "Walmart"},
}


@pytest.mark.asyncio
@respx.mock
async def test_themuse_sorted_filtered_and_paged():
    route = respx.get("https://www.themuse.com/api/public/jobs").mock(
        return_value=httpx.Response(200, json={"page": 0, "page_count": 2, "total": 21, "results": [MUSE_JOB]})
    )
    async with httpx.AsyncClient() as client:
        jobs = await ADAPTERS["themuse"].fetch(client, "category=Software Engineering&level=Entry Level&level=Mid Level&pages=5")
    assert route.call_count == 2  # page_count stops it before 5
    params = route.calls[0].request.url.params
    assert params.get_list("level") == ["Entry Level", "Mid Level"] and params["category"] == "Software Engineering"
    assert params["sort"] == "publication_date" and params["descending"] == "true" and params["page"] == "0"
    assert "pages" not in params
    job = jobs[0]
    assert job.company_name == "Walmart" and job.external_id == "18888634"
    assert job.locations == ["Springdale, AR", "Remote"] and job.remote
    assert job.description.startswith("Experience level: Entry Level") and "café" in job.description
    assert job.department == "Food and Hospitality Services"
    assert job.posted_at == datetime(2025, 8, 13, 3, 30, 37, tzinfo=timezone.utc)


REMOTIVE = {
    "0-legal-notice": "Legal warning - ...",
    "job-count": 1,
    "jobs": [
        {
            "id": 2091165,
            "url": "https://remotive.com/remote-jobs/software-development/full-stack-developer-2091165",
            "title": "Full-Stack Developer",
            "company_name": "Fluence International, Inc.",
            "category": "Software Development",
            "job_type": "contract",
            "publication_date": "2026-10-29T18:22:41",
            "candidate_required_location": "Worldwide",
            "description": "<p dir=\"ltr\"><span>We’re looking for a pragmatic Full-Stack Developer.</span></p>",
        }
    ],
}


@pytest.mark.asyncio
@respx.mock
async def test_remotive():
    route = respx.get("https://remotive.com/api/remote-jobs").mock(return_value=httpx.Response(200, json=REMOTIVE))
    async with httpx.AsyncClient() as client:
        [job] = await ADAPTERS["remotive"].fetch(client, "category=software-dev")
    assert route.calls[0].request.url.params["category"] == "software-dev"
    assert job.company_name == "Fluence International, Inc." and job.remote and job.location == "Worldwide"
    assert job.url.startswith("https://remotive.com/")
    assert job.posted_at == datetime(2026, 10, 29, 18, 22, 41, tzinfo=timezone.utc)
    assert job.description == "We’re looking for a pragmatic Full-Stack Developer."


REMOTEOK = [
    {"last_updated": 1793721625, "legal": "API Terms of Service: Please link back ..."},
    {
        "slug": "remote-site-activation-manager-umed-1137620",
        "id": "1137620",
        "epoch": 1793635208,
        "date": "2026-11-02T16:00:08+00:00",
        "company": "uMed",
        "position": "Site Activation Manager 6 Month Contract Immediate start",
        "tags": ["ops", "medical"],
        "description": "<p>Our VisionÂ </p><p>uMed seeks to reduce the burden of disease.Â </p>",
        "location": "",
        "url": "https://remoteOK.com/remote-jobs/remote-site-activation-manager-umed-1137620",
    },
]


@pytest.mark.asyncio
@respx.mock
async def test_remoteok_skips_legal_notice_and_fixes_encoding():
    respx.get("https://remoteok.com/api").mock(return_value=httpx.Response(200, json=REMOTEOK))
    async with httpx.AsyncClient() as client:
        [job] = await ADAPTERS["remoteok"].fetch(client, "all")
    assert job.external_id == "1137620" and job.company_name == "uMed" and job.remote
    assert job.url == REMOTEOK[1]["url"] and job.locations == []
    assert "Â" not in job.description and job.description.startswith("Our Vision")
    assert job.posted_at == datetime(2026, 11, 2, 16, 0, 8, tzinfo=timezone.utc)
    assert fix_mojibake("cafÃ©") == "café" and fix_mojibake("We’re fine") == "We’re fine"


def adzuna_job(i: int) -> dict:
    # Shape per https://developer.adzuna.com/docs/search
    return {
        "__CLASS__": "Adzuna::API::Response::Job",
        "id": str(4000000000 + i),
        "title": "<strong>Software</strong> Engineer",
        "description": "Build APIs. Must be authorized to work in the US without sponsorship…",
        "redirect_url": f"https://www.adzuna.com/land/ad/{4000000000 + i}?se=abc&v=def",
        "created": "2026-11-03T09:15:42Z",
        "company": {"__CLASS__": "Adzuna::API::Response::Company", "display_name": "Acme Corp"},
        "location": {"__CLASS__": "Adzuna::API::Response::Location", "display_name": "Austin, Travis County", "area": ["US", "Texas", "Travis County", "Austin"]},
        "category": {"__CLASS__": "Adzuna::API::Response::Category", "label": "IT Jobs", "tag": "it-jobs"},
        "contract_time": "full_time",
        "salary_min": 120000,
        "salary_max": 150000,
    }


@pytest.mark.asyncio
@respx.mock
async def test_adzuna(monkeypatch):
    adapter = ADAPTERS["adzuna"]
    monkeypatch.setattr(settings, "adzuna_app_id", "")
    monkeypatch.setattr(settings, "adzuna_app_key", "")
    assert not adapter.is_configured()
    monkeypatch.setattr(settings, "adzuna_app_id", "test-id")
    monkeypatch.setattr(settings, "adzuna_app_key", "test-key")
    assert adapter.is_configured()

    page1 = respx.get("https://api.adzuna.com/v1/api/jobs/us/search/1").mock(
        return_value=httpx.Response(200, json={"count": 51, "results": [adzuna_job(i) for i in range(50)]})
    )
    page2 = respx.get("https://api.adzuna.com/v1/api/jobs/us/search/2").mock(
        return_value=httpx.Response(200, json={"count": 51, "results": [adzuna_job(50)]})
    )
    async with httpx.AsyncClient() as client:
        jobs = await adapter.fetch(client, "category=it-jobs&pages=5")
    assert page1.call_count == 1 and page2.call_count == 1  # short page 2 ends it
    params = page1.calls[0].request.url.params
    assert params["app_id"] == "test-id" and params["sort_by"] == "date" and params["max_days_old"] == "2"
    assert params["results_per_page"] == "50" and params["category"] == "it-jobs" and "pages" not in params
    assert len(jobs) == 51
    job = jobs[0]
    assert job.title == "Software Engineer" and job.company_name == "Acme Corp"
    assert job.location == "Austin, Travis County" and job.department == "IT Jobs"
    assert "without sponsorship" in job.description
    assert job.url.startswith("https://www.adzuna.com/land/ad/")
    assert job.posted_at == datetime(2026, 11, 3, 9, 15, 42, tzinfo=timezone.utc)


@pytest.mark.asyncio
@respx.mock
async def test_adzuna_bad_key_raises(monkeypatch):
    monkeypatch.setattr(settings, "adzuna_app_id", "bad")
    monkeypatch.setattr(settings, "adzuna_app_key", "bad")
    respx.get("https://api.adzuna.com/v1/api/jobs/us/search/1").mock(return_value=httpx.Response(401, json={"exception": "AUTH_FAIL"}))
    async with httpx.AsyncClient() as client:
        with pytest.raises(httpx.HTTPStatusError):
            await ADAPTERS["adzuna"].fetch(client, "")


def test_registry_settings():
    for ats in ("workday", "smartrecruiters", "workable", "themuse", "remotive", "remoteok", "adzuna"):
        assert ADAPTERS[ats].ats == ats
    for ats in ("themuse", "remotive", "remoteok", "adzuna"):
        assert not ADAPTERS[ats].complete_feed
    for ats in ("workday", "smartrecruiters", "workable"):
        assert ADAPTERS[ats].complete_feed
    assert ADAPTERS["remotive"].min_interval_minutes == 360
    assert ADAPTERS["workday"].full_scan_every > 1


def test_workday_pinned_offsets_survive_a_restart(tmp_path, monkeypatch):
    """A restart used to forget where each board's pinned block ends, so every board's next scan was a full one."""
    path = tmp_path / "workday_state.json"
    monkeypatch.setattr(WorkdayAdapter, "_state_path", lambda self: path)
    first = WorkdayAdapter()
    first._load_state()
    first._pinned["acme|wd1|External"] = 40
    first._capped.add("big|wd5|Careers")
    first._save_state()
    again = WorkdayAdapter()  # a new process
    again._load_state()
    assert again._pinned == {"acme|wd1|External": 40} and not again.last_fetch_complete("big|wd5|Careers")
