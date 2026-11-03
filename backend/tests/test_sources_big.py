"""respx-mocked tests for the big-company career-site adapters (Amazon, Microsoft/Eightfold, Google,
Apple, Oracle HCM, iCIMS, SuccessFactors).

Fixtures are trimmed copies of real responses captured live in Nov 2026.
"""

import json
from datetime import datetime, timezone

import httpx
import pytest
import respx

from app.sources import ADAPTERS, http_util
from app.sources.amazon import AmazonAdapter, buckets, parse_posted
from app.sources.amazon import parse_slug as amazon_slug
from app.sources.apple import AppleAdapter
from app.sources.eightfold import EightfoldAdapter, clean_location
from app.sources.eightfold import parse_slug as eightfold_slug
from app.sources.google import parse_page
from app.sources.icims import host_for, parse_listing
from app.sources.oracle_hcm import parse_slug as oracle_slug
from app.sources.successfactors import SuccessFactorsAdapter, page_description, parse_classic, parse_date


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    async def instant(_seconds):
        return None

    monkeypatch.setattr(http_util.asyncio, "sleep", instant)


def test_all_big_adapters_registered_with_sensible_attributes():
    for ats in ("amazon", "microsoft", "google", "apple", "oracle_hcm", "icims", "eightfold", "successfactors"):
        adapter = ADAPTERS[ats]
        assert adapter.ats == ats and adapter.lane == "slow" and adapter.min_interval_minutes >= 15
        assert adapter.complete_feed


@pytest.mark.asyncio
@respx.mock
async def test_http_util_backs_off_on_429():
    route = respx.get("https://example.com/x").mock(side_effect=[
        httpx.Response(429, headers={"Retry-After": "1"}), httpx.Response(429), httpx.Response(200, json={"ok": 1}),
    ])
    async with httpx.AsyncClient() as client:
        resp = await http_util.get(client, "https://example.com/x")
    assert resp.json() == {"ok": 1} and route.call_count == 3


@pytest.mark.asyncio
@respx.mock
async def test_http_util_gives_up_after_retries():
    respx.get("https://example.com/x").mock(return_value=httpx.Response(429))
    async with httpx.AsyncClient() as client:
        with pytest.raises(httpx.HTTPStatusError):
            await http_util.get(client, "https://example.com/x")


def test_json_ld_helpers():
    page = '<script type="application/ld+json">{"@context":"http://schema.org","@type":"JobPosting","title":"X",' \
           '"jobLocation":[{"address":{"addressLocality":"Herndon","addressRegion":"VA","addressCountry":"US",' \
           '"postalCode":"UNAVAILABLE"}}]}</script>'
    posting = http_util.json_ld_job(page)
    assert posting["title"] == "X" and http_util.ld_locations(posting) == ["Herndon, VA, US"]
    assert http_util.json_ld_job("<html></html>") == {}


# --- Amazon ------------------------------------------------------------------------------------

AMZ = "https://www.amazon.jobs/en/search.json"


def amz_job(i: int, **kw) -> dict:
    return {
        "id": f"uuid-{i}", "id_icims": str(10595000 + i), "title": f"Software Development Engineer {i}",
        "job_path": f"/en/jobs/{10595000 + i}/sde", "location": "US, WA, Seattle",
        "normalized_location": "Seattle, Washington, USA", "posted_date": "November  3, 2026",
        "job_category": "Software Development", "description": "<p>Build things.</p>",
        "basic_qualifications": "- 3+ years of non-internship professional software development experience",
        "preferred_qualifications": "- Master's degree<br/>- Experience with AWS", **kw,
    }


AMZ_REAL = {
    "basic_qualifications": "- 1+ years of Microsoft Office products and applications experience<br/>- High school or equivalent",
    "city": "Houston", "company_name": "Prime Now LLC", "country_code": "USA",
    "description": "<br/><span style=\"font-style: italic;\">This role requires open availability seven days a week.</span>",
    "id": "8cd2bb31-8bfb-4c0d-9ae5-243b57647486", "id_icims": "10595752", "job_category": "Fulfillment / Warehouse Associate",
    "job_family": "Fulfillment Center", "job_path": "/en/jobs/10595752/lead-associate", "location": "US, TX, Houston",
    "normalized_location": "Houston, Texas, USA", "posted_date": "November  3, 2026",
    "preferred_qualifications": "- 2+ years of production, warehouse or other leadership", "title": "Lead Associate",
    "updated_time": "14 minutes",
}


def test_amazon_helpers():
    assert amazon_slug("country=USA&category[]=Legal&sort=relevant") == [("country", "USA"), ("category[]", "Legal")]
    assert amazon_slug("") == [] and amazon_slug("all") == []
    assert parse_posted("November  3, 2026") == datetime(2026, 11, 3, tzinfo=timezone.utc)
    assert parse_posted("soon") is None
    groups = buckets([("A", 5000), ("B", 4000), ("C", 3000), ("D", 500)], limit=9000)
    assert sorted(map(sorted, groups)) == [["A", "B"], ["C", "D"]]


@pytest.mark.asyncio
@respx.mock
async def test_amazon_partial_scan_stops_at_a_known_page():
    pages = {0: [AMZ_REAL] + [amz_job(i) for i in range(99)], 100: [amz_job(i) for i in range(100, 200)]}
    route = respx.get(AMZ).mock(side_effect=lambda req: httpx.Response(200, json={
        "error": None, "hits": 10000, "jobs": pages.get(int(req.url.params["offset"]), [])
    }))
    known = {str(10595000 + i) for i in range(200)}
    async with httpx.AsyncClient() as client:
        jobs = await AmazonAdapter().fetch(client, "country=USA", full=False, known_ids=known)
    assert route.call_count == 2  # page 2 is all known: stop
    params = route.calls[0].request.url.params
    assert params["sort"] == "recent" and params["result_limit"] == "100" and params["country"] == "USA"
    assert len(jobs) == 200
    job = next(j for j in jobs if j.external_id == "10595752")
    assert job.url == "https://www.amazon.jobs/en/jobs/10595752/lead-associate"
    assert job.locations == ["Houston, Texas, USA"] and job.department == "Fulfillment / Warehouse Associate"
    assert "open availability" in job.description and "Basic Qualifications\n- 1+ years" in job.description
    assert "<br/>" not in job.description and "Preferred Qualifications" in job.description
    assert job.posted_at == datetime(2026, 11, 3, tzinfo=timezone.utc)


@pytest.mark.asyncio
@respx.mock
async def test_amazon_full_scan_splits_capped_queries_by_category():
    facets = {"category_facet": [{"Software Development": 7000}, {"Legal": 4000}, {"Economics": 100}]}
    by_cat = {
        # first-fit: Economics (100) still fits next to Software Development (7000)
        ("Software Development", "Economics"): [amz_job(i) for i in range(3)] + [amz_job(11, job_category="Economics")],
        ("Legal",): [amz_job(10)],
    }

    def handler(req: httpx.Request) -> httpx.Response:
        params = req.url.params
        if params.get("result_limit") == "1":
            assert params.get_list("facets[]") == ["category"]
            return httpx.Response(200, json={"hits": 10000, "facets": facets, "jobs": [amz_job(0)]})
        cats = tuple(sorted(params.get_list("category[]")))
        jobs = next(v for k, v in by_cat.items() if tuple(sorted(k)) == cats)
        return httpx.Response(200, json={"hits": len(jobs), "jobs": jobs if params["offset"] == "0" else []})

    route = respx.get(AMZ).mock(side_effect=handler)
    adapter = AmazonAdapter()
    async with httpx.AsyncClient() as client:
        jobs = await adapter.fetch(client, "country=USA", full=True)
    assert route.call_count == 3 and len(jobs) == 5
    assert adapter.last_fetch_complete("country=USA")


@pytest.mark.asyncio
@respx.mock
async def test_amazon_full_scan_of_a_small_query_pages_directly():
    respx.get(AMZ).mock(side_effect=lambda req: httpx.Response(200, json={
        "hits": 2, "facets": {"category_facet": [{"Legal": 2}]},
        "jobs": [amz_job(1), amz_job(2)] if req.url.params.get("offset", "0") == "0" else [],
    }))
    async with httpx.AsyncClient() as client:
        jobs = await AmazonAdapter().fetch(client, "country=USA&category[]=Legal")
    assert len(jobs) == 2


@pytest.mark.asyncio
@respx.mock
async def test_amazon_api_error_raises():
    respx.get(AMZ).mock(return_value=httpx.Response(200, json={"error": "Result limit cannot be greater than 100", "jobs": None}))
    async with httpx.AsyncClient() as client:
        with pytest.raises(ValueError):
            await AmazonAdapter().fetch(client, "", full=False)


# --- Microsoft / Eightfold PCSX ------------------------------------------------------------------

MS = "https://apply.careers.microsoft.com/api/pcsx"
MS_POSITION = {
    "id": 1970393557050135, "displayJobId": "200064432", "name": "Senior Product Marketing Manager",
    "locations": ["United States, Washington, Redmond"], "standardizedLocations": ["Redmond, WA, US"],
    "postedTs": 1793730684, "department": "Product Marketing", "creationTs": 1793218594, "isHot": 0,
    "workLocationOption": "onsite", "atsJobId": "200064432", "positionUrl": "/careers/job/1970393557050135",
}
MS_DETAIL = {
    "status": 200,
    "data": {
        **MS_POSITION,
        "publicUrl": "https://apply.careers.microsoft.com/careers/job/1970393557050135",
        "jobDescription": "<b>Overview</b><br><div><p>We're looking for a <strong>Senior Product Marketing Manager</strong>.</p>"
                          "<p>Microsoft will not sponsor visas for this role.</p></div>",
        "efcustomTextWorkSite": ["3 days / week in-office"],
    },
}


def ms_position(i: int, ts: int = 1793600000, **kw) -> dict:
    return {**MS_POSITION, "id": 1000 + i, "name": f"Engineer {i}", "postedTs": ts - i, "positionUrl": f"/careers/job/{1000 + i}", **kw}


def pcsx_pages(positions: list[dict]):
    def handler(req: httpx.Request) -> httpx.Response:
        start = int(req.url.params["start"])
        return httpx.Response(200, json={"status": 200, "data": {"count": len(positions), "positions": positions[start:start + 10]}})
    return handler


def test_eightfold_helpers():
    assert eightfold_slug("paypal|paypal.com") == ("paypal.eightfold.ai", "paypal.com", "")
    assert eightfold_slug("qualcomm|qualcomm.com|United States") == ("qualcomm.eightfold.ai", "qualcomm.com", "United States")
    assert eightfold_slug("explore.jobs.netflix.net|netflix.com")[0] == "explore.jobs.netflix.net"
    with pytest.raises(ValueError):
        eightfold_slug("paypal")
    assert clean_location("United States, Multiple Locations, Multiple Locations") == "United States"


@pytest.mark.asyncio
@respx.mock
async def test_microsoft_lists_newest_and_fetches_new_details():
    positions = [MS_POSITION] + [ms_position(i) for i in range(14)]
    search = respx.get(f"{MS}/search").mock(side_effect=pcsx_pages(positions))
    detail = respx.get(f"{MS}/position_details").mock(return_value=httpx.Response(200, json=MS_DETAIL))
    known = {str(p["id"]) for p in positions[1:]}
    async with httpx.AsyncClient() as client:
        jobs = await ADAPTERS["microsoft"].fetch(client, "United States", full=True, known_ids=known)
    assert search.call_count == 2 and detail.call_count == 1
    params = search.calls[0].request.url.params
    assert params["domain"] == "microsoft.com" and params["location"] == "United States" and params["sort_by"] == "timestamp"
    assert detail.calls[0].request.url.params["position_id"] == "1970393557050135"
    assert len(jobs) == 15
    job = next(j for j in jobs if j.external_id == "1970393557050135")
    assert job.title == "Senior Product Marketing Manager" and job.department == "Product Marketing"
    assert job.url == "https://apply.careers.microsoft.com/careers/job/1970393557050135"
    assert job.locations == ["United States, Washington, Redmond"] and not job.remote
    assert "not sponsor visas" in job.description and "<p>" not in job.description
    assert job.posted_at == datetime.fromtimestamp(1793730684, tz=timezone.utc)
    other = next(j for j in jobs if j.external_id == "1003")
    assert other.description == "" and other.url == "https://apply.careers.microsoft.com/careers/job/1003"


@pytest.mark.asyncio
@respx.mock
async def test_eightfold_partial_scan_reads_at_least_two_pages():
    positions = [ms_position(i) for i in range(60)]
    search = respx.get("https://qualcomm.eightfold.ai/api/pcsx/search").mock(side_effect=pcsx_pages(positions))
    known = {str(p["id"]) for p in positions}
    async with httpx.AsyncClient() as client:
        jobs = await EightfoldAdapter().fetch(client, "qualcomm|qualcomm.com", full=False, known_ids=known)
    assert search.call_count == 2 and len(jobs) == 20


@pytest.mark.asyncio
@respx.mock
async def test_eightfold_falls_back_to_v2_api():
    v2_position = {
        "id": 790319235151, "name": "Manager - Measurement Science & Analytics", "locations": ["Remote, United States"],
        "department": "Data & Insights", "t_create": 1793318400, "t_update": 1793318400, "job_description": "",
        "work_location_option": "remote",
        "canonicalPositionUrl": "https://explore.jobs.netflix.net/careers/job/790319235151",
    }
    pcsx = respx.get("https://netflix.eightfold.ai/api/pcsx/search").mock(
        return_value=httpx.Response(403, json={"message": "PCSX is not enabled for this user."})
    )
    v2 = respx.get("https://netflix.eightfold.ai/api/apply/v2/jobs").mock(
        return_value=httpx.Response(200, json={"count": 1, "positions": [v2_position]})
    )
    respx.get("https://netflix.eightfold.ai/api/apply/v2/jobs/790319235151").mock(return_value=httpx.Response(200, json={
        **v2_position, "job_description": "<p>At Netflix, our mission is to entertain the world.</p>",
    }))
    adapter = EightfoldAdapter()
    async with httpx.AsyncClient() as client:
        [job] = await adapter.fetch(client, "netflix|netflix.com|United States")
        await adapter.fetch(client, "netflix|netflix.com|United States", known_ids={"790319235151"})
    assert pcsx.call_count == 1  # the flavor is remembered per host
    assert v2.call_count == 2 and v2.calls[0].request.url.params["location"] == "United States"
    assert job.remote and job.description == "At Netflix, our mission is to entertain the world."
    assert job.posted_at == datetime.fromtimestamp(1793318400, tz=timezone.utc)
    assert job.url == "https://explore.jobs.netflix.net/careers/job/790319235151"


@pytest.mark.asyncio
@respx.mock
async def test_eightfold_skips_new_job_whose_detail_fails():
    respx.get(f"{MS}/search").mock(side_effect=pcsx_pages([MS_POSITION]))
    respx.get(f"{MS}/position_details").mock(return_value=httpx.Response(500))
    async with httpx.AsyncClient() as client:
        assert await ADAPTERS["microsoft"].fetch(client, "United States") == []


# --- Google ------------------------------------------------------------------------------------

GOOGLE = "https://www.google.com/about/careers/applications/jobs/results/"
G_JOB = [
    "108774861752410822", "Technical Program Manager III, AI Transformation, Google Cloud",
    "https://www.google.com/about/careers/applications/signin?jobId=CiUAL2Fck&loc=US",
    [None, "<ul>\n<li> Lead use-case analysis and author Product Requirements Documents.</li></ul>"],
    [None, "<h3>Minimum qualifications:</h3><ul>\n<li>Bachelor's degree in a technical field.</li>\n<li>5 years of experience in program management.</li></ul>"],
    "projects/gweb-careers-proto/tenants/x/companies/y", None, "Google", "en-US",
    [["Mountain View, CA, USA", ["100 Bay Vw Dr, Mountain View, CA 94043, USA"], "Mountain View", "94043", "CA", "US"]],
    [None, "<p>A problem isn’t truly solved until it’s solved for all.</p>"],
    [2], [1793729373, 320000000], [1793729373, 320000000], [1793729373, 390000000],
]


def google_page(jobs, total: int) -> str:
    data = json.dumps([jobs, None, total, 20])
    return (f"<html><script>AF_initDataCallback({{key: 'ds:0', hash: '1', data:[], sideChannel: {{}}}});</script>"
            f"<script nonce=\"x\">AF_initDataCallback({{key: 'ds:1', hash: '2', data:{data}, sideChannel: {{}}}});</script></html>")


def g_job(i: int) -> list:
    row = list(G_JOB)
    row[0], row[1] = str(100 + i), f"Software Engineer {i}"
    return row


def test_google_parse_page():
    jobs, total = parse_page(google_page([G_JOB], 1756))
    assert total == 1756 and jobs[0][0] == "108774861752410822"
    assert parse_page(google_page(None, 1756)) == ([], 1756)
    with pytest.raises(ValueError):
        parse_page("<html>blocked</html>")


@pytest.mark.asyncio
@respx.mock
async def test_google_pages_newest_first_and_parses_jobs():
    pages = {"1": [G_JOB] + [g_job(i) for i in range(19)], "2": [g_job(i) for i in range(19, 25)]}
    route = respx.get(GOOGLE).mock(side_effect=lambda req: httpx.Response(
        200, text=google_page(pages.get(req.url.params["page"]), 26)
    ))
    async with httpx.AsyncClient() as client:
        jobs = await ADAPTERS["google"].fetch(client, "location=United States", full=True)
    assert route.call_count == 2
    params = route.calls[0].request.url.params
    assert params["sort_by"] == "date" and params["location"] == "United States" and params["page"] == "1"
    assert len(jobs) == 26  # the real one + g_job(0..24)
    job = next(j for j in jobs if j.external_id == "108774861752410822")
    assert job.url == GOOGLE + "108774861752410822-technical-program-manager-iii-ai-transformation-google-cloud"
    assert job.locations == ["Mountain View, CA, USA"] and job.department == ""
    assert job.description.startswith("A problem isn’t truly solved")
    assert "Responsibilities\nLead use-case analysis" in job.description and "5 years of experience" in job.description
    assert job.posted_at == datetime.fromtimestamp(1793729373, tz=timezone.utc)


@pytest.mark.asyncio
@respx.mock
async def test_google_partial_stops_on_known_page():
    route = respx.get(GOOGLE).mock(side_effect=lambda req: httpx.Response(
        200, text=google_page([g_job(int(req.url.params["page"]) * 20 + i) for i in range(20)], 2000)
    ))
    async with httpx.AsyncClient() as client:
        jobs = await ADAPTERS["google"].fetch(client, "", full=False, known_ids={str(100 + i) for i in range(10_000)})
    assert route.call_count == 1 and len(jobs) == 20


# --- Apple -------------------------------------------------------------------------------------

APPLE = "https://jobs.apple.com/api/v1"


def apple_row(row_id: str, pid: str, city: str, title: str = "Manager, State Government Affairs - Midwest") -> dict:
    return {
        "id": row_id, "positionId": pid, "postingTitle": title,
        "transformedPostingTitle": "manager-state-government-affairs-midwest",
        "postDateInGMT": "2026-11-03T13:32:44.377Z", "postingDate": "Nov 03, 2026", "type": "PIPE",
        "team": {"teamName": "Corporate Functions", "teamCode": "CORSV"}, "homeOffice": False,
        "locations": [{"postLocationId": "postLocation-MWI", "countryName": "United States of America", "name": city, "level": 5}],
    }


APPLE_DETAIL = {"res": {
    "id": "PIPE-200692753", "jobNumber": "200692753-2395", "postingTitle": "Manager, State Government Affairs - Midwest",
    "jobSummary": "The Government Affairs team helps develop policy and strategy.",
    "description": "As a senior member of our State Government Affairs team, you will develop policy.",
    "responsibilities": "Design and implement public policy initiatives.\n\nTravel extensively.",
    "minimumQualifications": "10+ years experience in government relations.",
    "preferredQualifications": "Knowledge of the policy issues relevant to Apple.",
    "locations": [
        {"id": "postLocation-MWI", "name": "Madison", "city": "Madison", "stateProvince": "Wisconsin", "countryName": "United States"},
        {"id": "postLocation-MIN", "name": "Minneapolis", "city": "Minneapolis", "stateProvince": "Minnesota", "countryName": "United States"},
    ],
    "homeOffice": True, "positionId": "200692753",
}}


@pytest.mark.asyncio
@respx.mock
async def test_apple_groups_rows_by_position_and_fetches_details():
    rows = [
        apple_row("200692753-2395", "200692753", "Madison"), apple_row("200692753-1114", "200692753", "Minneapolis"),
        apple_row("200692043-2459", "200692043", "New York City", "Senior ML Engineer - Ads ML Infrastructure"),
    ]
    search = respx.post(f"{APPLE}/search").mock(side_effect=lambda req: httpx.Response(200, json={"res": {
        "searchResults": rows if json.loads(req.content)["page"] == 1 else [], "totalRecords": 3,
    }}))
    detail = respx.get(f"{APPLE}/jobDetails/200692753-2395").mock(return_value=httpx.Response(200, json=APPLE_DETAIL))
    async with httpx.AsyncClient() as client:
        jobs = await AppleAdapter().fetch(client, "postLocation-USA", known_ids={"200692043"})
    body = json.loads(search.calls[0].request.content)
    assert body["filters"] == {"locations": ["postLocation-USA"]} and body["sort"] == "newest" and "format" in body
    assert search.call_count == 1 and detail.call_count == 1 and len(jobs) == 2
    job = next(j for j in jobs if j.external_id == "200692753")
    assert job.url == "https://jobs.apple.com/en-us/details/200692753/manager-state-government-affairs-midwest"
    assert job.locations == ["Madison, Wisconsin, United States", "Minneapolis, Minnesota, United States"]
    assert job.department == "Corporate Functions"
    assert job.description.startswith("The Government Affairs team")
    assert "Minimum Qualifications\n10+ years" in job.description and "Travel extensively" in job.description
    assert job.posted_at == datetime(2026, 11, 3, 13, 32, 44, 377000, tzinfo=timezone.utc)
    known = next(j for j in jobs if j.external_id == "200692043")
    assert known.locations == ["New York City, United States of America"] and known.description == ""


# --- Oracle HCM --------------------------------------------------------------------------------

ORA_HOST = "https://jpmc.fa.oraclecloud.com/hcmRestApi/resources/latest"
ORA_REQ = {
    "Id": "210742773", "Title": "ServiceNow Lead Software Engineer - HRSD", "PostedDate": "2026-11-03",
    "PrimaryLocation": "Plano, TX, United States", "PrimaryLocationCountry": "US", "JobFamily": "Software Engineering",
    "WorkplaceType": "", "ShortDescriptionStr": "Lead software engineering.",
    "secondaryLocations": [{"Name": "Columbus, OH, United States", "CountryCode": "US"}],
}
ORA_DETAIL = {"items": [{
    "Id": "210742773", "Title": "ServiceNow Lead Software Engineer - HRSD", "Category": "Software Engineering",
    "ExternalPostedStartDate": "2026-11-03T15:39:14+00:00",
    "ExternalDescriptionStr": "<p>We have an opportunity to impact your career.</p><p>JPMorganChase does not sponsor visas for this role.</p>",
    "ExternalResponsibilitiesStr": "<ul><li>Executes software solutions</li></ul>", "ExternalQualificationsStr": "",
    "CorporateDescriptionStr": "<p>JPMorganChase, one of the oldest financial institutions</p>",
    "PrimaryLocation": "Plano, TX, United States", "WorkplaceType": "",
}]}


def ora_req(i: int) -> dict:
    return {**ORA_REQ, "Id": str(1000 + i), "Title": f"Analyst {i}", "secondaryLocations": []}


def ora_list(reqs: list[dict], total: int):
    def handler(req: httpx.Request) -> httpx.Response:
        finder = req.url.params["finder"]
        offset = int(finder.split("offset=")[1].split(",")[0])
        return httpx.Response(200, json={"items": [{"TotalJobsCount": total, "requisitionList": reqs[offset:offset + 200]}]})
    return handler


def test_oracle_slug():
    assert oracle_slug("jpmc.fa.oraclecloud.com|CX_1001") == ("jpmc.fa.oraclecloud.com", "CX_1001", "")
    assert oracle_slug("https://x.fa.us2.oraclecloud.com/|CX|locationId=1")[0] == "x.fa.us2.oraclecloud.com"
    with pytest.raises(ValueError):
        oracle_slug("jpmc.fa.oraclecloud.com")


@pytest.mark.asyncio
@respx.mock
async def test_oracle_hcm_lists_and_fetches_new_details():
    reqs = [ORA_REQ] + [ora_req(i) for i in range(250)]
    lst = respx.get(f"{ORA_HOST}/recruitingCEJobRequisitions").mock(side_effect=ora_list(reqs, len(reqs)))
    detail = respx.get(f"{ORA_HOST}/recruitingCEJobRequisitionDetails").mock(return_value=httpx.Response(200, json=ORA_DETAIL))
    known = {r["Id"] for r in reqs[1:]}
    async with httpx.AsyncClient() as client:
        jobs = await ADAPTERS["oracle_hcm"].fetch(
            client, "jpmc.fa.oraclecloud.com|CX_1001|locationId=300000000289738", full=True, known_ids=known
        )
    assert lst.call_count == 2 and detail.call_count == 1 and len(jobs) == 251
    finder = lst.calls[0].request.url.params["finder"]
    assert finder.startswith("findReqs;siteNumber=CX_1001,") and "sortBy=POSTING_DATES_DESC" in finder
    assert "limit=200" in finder and finder.endswith(",locationId=300000000289738")
    assert detail.calls[0].request.url.params["finder"] == 'ById;Id="210742773",siteNumber=CX_1001'
    job = next(j for j in jobs if j.external_id == "210742773")
    assert job.url == "https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/job/210742773"
    assert job.locations == ["Plano, TX, United States", "Columbus, OH, United States"]
    assert "does not sponsor visas" in job.description and "Responsibilities\nExecutes" in job.description
    assert "oldest financial" not in job.description  # the corporate blurb is left out
    assert job.posted_at == datetime(2026, 11, 3, 15, 39, 14, tzinfo=timezone.utc)
    other = next(j for j in jobs if j.external_id == "1005")
    assert other.posted_at == datetime(2026, 11, 3, tzinfo=timezone.utc) and other.description == ""


@pytest.mark.asyncio
@respx.mock
async def test_oracle_hcm_partial_reads_first_page_and_skips_withdrawn_jobs():
    reqs = [ora_req(i) for i in range(600)]
    lst = respx.get(f"{ORA_HOST}/recruitingCEJobRequisitions").mock(side_effect=ora_list(reqs, 600))
    respx.get(f"{ORA_HOST}/recruitingCEJobRequisitionDetails").mock(return_value=httpx.Response(200, json={"items": []}))
    known = {r["Id"] for r in reqs[1:]}
    async with httpx.AsyncClient() as client:
        jobs = await ADAPTERS["oracle_hcm"].fetch(client, "jpmc.fa.oraclecloud.com|CX_1001", full=False, known_ids=known)
    assert lst.call_count == 2  # page 1 has an unknown job, page 2 doesn't
    assert len(jobs) == 399  # the unknown one had no detail (withdrawn): skipped


# --- iCIMS -------------------------------------------------------------------------------------

ICIMS_LIST = """
<div class="iCIMS_JobsTable"><ul class="container-fluid iCIMS_JobsTable">
<li class="iCIMS_JobCardItem"><div class="row">
<div class="col-xs-6 header left"><span class="sr-only field-label">Job Locations</span>
<span >
US-VA-Herndon</span></div>
<div class="col-xs-6 header right"><span class="sr-only field-label">Requisition Post Information* : Posted Date</span>
<span title="11/3/2026 12:01 PM">2 hours ago</span></div>
<div class="col-xs-12 title">
<a href="https://careers-peraton.icims.com/jobs/173168/senior-manager%2c-contracts-administration/job?in_iframe=1" class="iCIMS_Anchor" title="173168 - Senior Manager, Contracts Administration">
<span class="sr-only field-label">External Job Posting Title</span>
<h3 >Senior Manager, Contracts Administration</h3></a></div>
</div></li>
<li class="iCIMS_JobCardItem"><div class="row">
<div class="col-xs-6 header left"><span class="sr-only field-label">Job Locations</span>
<span >
US-MD-Greenbelt</span></div>
<div class="col-xs-12 title">
<a href="https://careers-peraton.icims.com/jobs/173139/satellite-communications-ground-systems-integration-%26-test-engineer/job?in_iframe=1" class="iCIMS_Anchor" title="173139 - Satellite Communications/Ground Systems Integration &amp; Test Engineer">
<h3 >Satellite Communications/Ground Systems Integration &amp; Test Engineer</h3></a></div>
</div></li>
</ul></div>
<div class="iCIMS_Paging"><span class="srHelp">Page 1 of 2</span></div>
"""
ICIMS_DETAIL = """<html><head>
<script type="application/ld+json">{"hiringOrganization":{"@type":"Organization","name":"Peraton"},
"jobLocation":[{"address":{"addressCountry":"US","streetAddress":"UNAVAILABLE","@type":"PostalAddress",
"postalCode":"UNAVAILABLE","addressLocality":"Herndon","addressRegion":"VA"},"@type":"Place"}],
"employmentType":"FULL_TIME","@type":"JobPosting",
"description":"&lt;h2&gt;Peraton Overview&lt;/h2&gt;\\n&lt;p&gt;Peraton is a next-generation national security company.&lt;/p&gt;&lt;p&gt;U.S. citizenship required.&lt;/p&gt;",
"datePosted":"2026-11-03T05:00:00.000Z","title":"Senior Manager, Contracts Administration","occupationalCategory":"Contracts",
"@context":"http://schema.org","url":"https://careers-peraton.icims.com/jobs/173168/senior-manager%2c-contracts-administration/job"}</script>
</head><body></body></html>"""


def test_icims_parse_listing():
    assert host_for("peraton") == "careers-peraton.icims.com"
    assert host_for("uscareers-msci.icims.com") == "uscareers-msci.icims.com"
    jobs, pages = parse_listing(ICIMS_LIST)
    assert pages == 2
    assert jobs == [
        {"id": "173168", "title": "Senior Manager, Contracts Administration", "location": "US-VA-Herndon"},
        {"id": "173139", "title": "Satellite Communications/Ground Systems Integration & Test Engineer", "location": "US-MD-Greenbelt"},
    ]


@pytest.mark.asyncio
@respx.mock
async def test_icims_pages_and_reads_json_ld():
    page2 = ICIMS_LIST.replace("173168", "173001").replace("173139", "173002").replace("Page 1 of 2", "Page 2 of 2")
    lst = respx.get("https://careers-peraton.icims.com/jobs/search").mock(side_effect=lambda req: httpx.Response(
        200, text=ICIMS_LIST if req.url.params["pr"] == "0" else page2
    ))
    respx.get("https://careers-peraton.icims.com/jobs/173168/job").mock(return_value=httpx.Response(200, text=ICIMS_DETAIL))
    known = {"173139", "173001", "173002"}
    async with httpx.AsyncClient() as client:
        jobs = await ADAPTERS["icims"].fetch(client, "peraton", known_ids=known)
    assert lst.call_count == 2 and lst.calls[0].request.url.params["in_iframe"] == "1"
    assert len(jobs) == 4
    job = next(j for j in jobs if j.external_id == "173168")
    assert job.title == "Senior Manager, Contracts Administration" and job.department == "Contracts"
    assert job.locations == ["Herndon, VA, US"]
    assert job.description.startswith("Peraton Overview") and "citizenship required" in job.description
    assert "<p>" not in job.description and "&lt;" not in job.description
    assert job.posted_at == datetime(2026, 11, 3, 5, tzinfo=timezone.utc)
    assert job.url == "https://careers-peraton.icims.com/jobs/173168/senior-manager%2c-contracts-administration/job"
    other = next(j for j in jobs if j.external_id == "173139")
    assert other.locations == ["US-MD-Greenbelt"] and other.url == "https://careers-peraton.icims.com/jobs/173139/job"


# --- SuccessFactors ----------------------------------------------------------------------------

SF_CLASSIC = """
<span class="paginationLabel" aria-label="Results 1 – 25">Results <b>1 – 25</b> of <b>761</b></span>
<table><tbody>
<tr class="data-row">
<td class="colTitle" headers="hdrTitle"><span class="jobTitle hidden-phone">
<a href="/ey/job/Charlotte-Consulting-Full-Stack-Manager-NC-28202/1406190533/" class="jobTitle-link">Consulting - TC - Full Stack Eng - Manager</a>
</span><div class="jobdetail-phone visible-phone"><span class="jobTitle visible-phone">
<a class="jobTitle-link" href="/ey/job/Charlotte-Consulting-Full-Stack-Manager-NC-28202/1406190533/">Consulting - TC - Full Stack Eng - Manager</a></span></div></td>
<td class="colLocation hidden-phone" headers="hdrLocation"><span class="jobLocation">
            Charlotte, NC, US, 28202
        </span></td>
</tr>
<tr class="data-row">
<td class="colTitle"><span class="jobTitle hidden-phone">
<a href="/ey/job/New-York-EY-Parthenon-Director-NY-10001-8604/1453912133/" class="jobTitle-link">EY-Parthenon - Tech PE Value Creation - Director</a></span></td>
<td class="colLocation hidden-phone"><span class="jobLocation">
            New York, NY, US, 10001-8604

 +3 more…
        </span></td>
</tr>
</tbody></table>"""
SF_DETAIL = """<html><body><div itemscope itemtype="http://schema.org/JobPosting">
<meta itemprop="datePosted" content="Tue Nov 03 02:00:00 UTC 2026">
<span itemprop="title">Consulting - TC - Full Stack Eng - Manager</span>
<span itemprop="description" class="rtltextaligneligible"><span class="jobdescription"><div>
<p>At EY, we&rsquo;re all in to shape your future with confidence.</p><p>Requires 8+ years of experience.</p>
<br></div></span></span>
<span class="other">Not part of it</span></div></body></html>"""
SF_UNIFY = {"totalJobs": 12, "jobSearchResult": [
    {"response": {
        "jobLocationShort": ["San Jose, USA-CA, USA, 62682<br/>"], "jobLocationCountry": ["United States"],
        "unifiedUrlTitle": "APPLICATION-ARCHITECT-L1%28CONTRACT%29", "unifiedStandardStart": "11/3/26",
        "id": "211631", "unifiedStandardTitle": "APPLICATION ARCHITECT L1(CONTRACT)",
        "urlTitle": "APPLICATION-ARCHITECT-L1%28CONTRACT%29",
    }},
    {"response": {
        "custprimecity": "Dallas", "unifiedStandardStart": "11/2/26", "id": "167546", "custCountryRegion": ["United States"],
        "unifiedStandardTitle": "SME - Mainframe SysOps & Batch Scheduling Tools", "urlTitle": "SME-Mainframe-SysOps",
    }},
]}


def test_successfactors_parsers():
    assert parse_date("Tue Nov 03 02:00:00 UTC 2026") == datetime(2026, 11, 3, tzinfo=timezone.utc)
    assert parse_date("11/3/26") == datetime(2026, 11, 3, tzinfo=timezone.utc)
    assert parse_date("") is None
    jobs, total = parse_classic(SF_CLASSIC)
    assert total == 761
    assert [(j["id"], j["title"], j["location"]) for j in jobs] == [
        ("1406190533", "Consulting - TC - Full Stack Eng - Manager", "Charlotte, NC, US, 28202"),
        ("1453912133", "EY-Parthenon - Tech PE Value Creation - Director", "New York, NY, US, 10001-8604"),
    ]
    text = page_description(SF_DETAIL)
    assert text.startswith("At EY, we’re all in") and "8+ years" in text and "Not part of it" not in text


@pytest.mark.asyncio
@respx.mock
async def test_successfactors_classic_site():
    respx.post("https://careers.ey.com/services/recruiting/v1/jobs").mock(return_value=httpx.Response(404, text="<html>"))
    search = respx.get("https://careers.ey.com/search/").mock(return_value=httpx.Response(200, text=SF_CLASSIC))
    respx.get("https://careers.ey.com/ey/job/Charlotte-Consulting-Full-Stack-Manager-NC-28202/1406190533/").mock(
        return_value=httpx.Response(200, text=SF_DETAIL)
    )
    async with httpx.AsyncClient() as client:
        jobs = await SuccessFactorsAdapter().fetch(
            client, "careers.ey.com|optionsFacetsDD_country=US", full=False, known_ids={"1453912133"}
        )
    params = search.calls[0].request.url.params
    assert params["sortColumn"] == "referencedate" and params["sortDirection"] == "desc"
    assert params["optionsFacetsDD_country"] == "US" and params["startrow"] == "0"
    job = next(j for j in jobs if j.external_id == "1406190533")
    assert job.url == "https://careers.ey.com/ey/job/Charlotte-Consulting-Full-Stack-Manager-NC-28202/1406190533/"
    assert job.locations == ["Charlotte, NC, US, 28202"] and "8+ years" in job.description
    assert job.posted_at == datetime(2026, 11, 3, tzinfo=timezone.utc)


@pytest.mark.asyncio
@respx.mock
async def test_successfactors_unify_site():
    route = respx.post("https://careers.wipro.com/services/recruiting/v1/jobs").mock(side_effect=lambda req: httpx.Response(
        200, json=SF_UNIFY if json.loads(req.content)["pageNumber"] == 0 else {"totalJobs": 12, "jobSearchResult": []}
    ))
    respx.get("https://careers.wipro.com/job/APPLICATION-ARCHITECT-L1%28CONTRACT%29/211631-en_US/").mock(
        return_value=httpx.Response(200, text=SF_DETAIL.replace("At EY", "At Wipro"))
    )
    async with httpx.AsyncClient() as client:
        jobs = await SuccessFactorsAdapter().fetch(
            client, "careers.wipro.com|jobLocationCountry=United States", known_ids={"167546"}
        )
    body = json.loads(route.calls[0].request.content)
    assert body["facetFilters"] == {"jobLocationCountry": ["United States"]} and body["sortBy"] == "date"
    assert route.call_count == 2 and len(jobs) == 2
    job = next(j for j in jobs if j.external_id == "211631")
    assert job.title == "APPLICATION ARCHITECT L1(CONTRACT)" and job.locations == ["San Jose, USA-CA, USA, 62682"]
    assert job.description.startswith("At Wipro")
    other = next(j for j in jobs if j.external_id == "167546")
    assert other.locations == ["Dallas, United States"] and other.posted_at == datetime(2026, 11, 2, tzinfo=timezone.utc)
