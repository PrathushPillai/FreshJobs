"""respx-mocked tests for the HR/payroll-suite adapters: Oracle Taleo, ADP Workforce Now and Paylocity.

Fixtures are trimmed-down copies of real responses captured in Nov 2026 (D.R. Horton and Textron on
Taleo, Enlightened on ADP, Meyer Distributing on Paylocity).
"""

import json
from datetime import timezone

import httpx
import pytest
import respx

from app.sources import adp, paylocity, taleo
from app.sources.adp import ADPAdapter
from app.sources.hr_common import outside_us, parse_day
from app.sources.paylocity import PaylocityAdapter
from app.sources.taleo import TaleoAdapter


async def run(adapter, slug, **kwargs):
    async with httpx.AsyncClient() as client:
        return await adapter.fetch(client, slug, **kwargs)


# --- shared helpers -------------------------------------------------------------------------------


def test_parse_day_and_outside_us():
    assert parse_day("11/09/2026").isoformat() == "2026-11-09T00:00:00+00:00"
    assert parse_day("Nov 9, 2026").isoformat() == "2026-11-09T00:00:00+00:00"
    assert parse_day("11/09/2026, 2:52:46 PM").day == 9
    assert parse_day("Investment Banking") is None and parse_day("") is None
    assert outside_us(["Germany"]) and outside_us(["Munich, Germany", "London, United Kingdom"])
    assert not outside_us(["Arlington, TX"]) and not outside_us(["Germany", "Wichita, Kansas, US"])
    assert not outside_us([]) and not outside_us(["Remote"])  # unknown counts as possibly US


# --- Taleo ----------------------------------------------------------------------------------------

TALEO = "https://drhorton.taleo.net/careersection"


def _taleo_row(job_id, contest, title, place, date):
    return {"hotJob": False, "jobId": job_id, "contestNo": contest, "column": [title, json.dumps([place]), date],
            "linkedColumn": 0, "locationsColumns": [1]}


def _taleo_page(rows, total, page=1):
    return {"requisitionList": rows, "pagingData": {"currentPageNo": page, "pageSize": 2, "totalCount": total},
            "facetResults": []}


TALEO_DETAIL = """<html><script>
api.fillList('requisitionDescriptionInterface', 'descRequisition', ['270440','true','270440','false',
'Submission for the position: Application Developer I - (Job Number: 2605503)','false','270440','false','true',
'Application Developer I','2605503','!*!%3Cp%3E%3Cstrong%3EAbout:%3C/strong%3E D.R. Horton%26rsquo;s IT team builds apps. Applicants must be authorized to work in the US without sponsorship, now or in the future.%3C/p%3E',
'!*!%3Cp%3E%3Cstrong%3EAbout:%3C/strong%3E D.R. Horton%26rsquo;s IT team builds apps. Applicants must be authorized to work in the US without sponsorship, now or in the future.%3C/p%3E',
'!*!%3Cul%3E%3Cli%3E2+ years of C# and SQL Server%3C/li%3E%3C/ul%3E','!*!%3Cul%3E%3Cli%3E2+ years of C# and SQL Server%3C/li%3E%3C/ul%3E',
'TX-Arlington','TX-Arlington','false']);
</script></html>"""


@pytest.mark.asyncio
@respx.mock
async def test_taleo_reads_portal_pages_and_details_for_new_jobs():
    TaleoAdapter._portals.clear()
    page = respx.get(f"{TALEO}/2/jobsearch.ftl").mock(return_value=httpx.Response(
        200, text="<script>var x = { portalNo: '101430233', lang: 'en' };</script>"))
    search = respx.post(f"{TALEO}/rest/jobboard/searchjobs", params={"lang": "en", "portal": "101430233"})
    search.side_effect = lambda request: httpx.Response(200, json=(
        _taleo_page([_taleo_row("270440", "2605503", "Application Developer I", "TX-Arlington", "Nov 9, 2026"),
                     _taleo_row("270924", "2605746", "Business Process Analyst", "US-Florida-Tampa", "Nov 8, 2026")], 3)
        if json.loads(request.content)["pageNo"] == 1 else
        _taleo_page([_taleo_row("1", "2600645", "Investment Banking Analyst", "Germany", "Nov 1, 2026")], 3, 2)
    ))
    detail = respx.get(f"{TALEO}/2/jobdetail.ftl", params={"job": "2605503"}).mock(
        return_value=httpx.Response(200, text=TALEO_DETAIL))
    known = respx.get(f"{TALEO}/2/jobdetail.ftl", params={"job": "2605746"})

    jobs = await run(TaleoAdapter(), "drhorton|2", known_ids={"2605746"})
    assert page.call_count == 1 and search.call_count == 2 and detail.called and not known.called
    assert [j.external_id for j in jobs] == ["2605503", "2605746"]  # the German job is dropped
    dev, analyst = jobs
    assert dev.title == "Application Developer I" and dev.locations == ["Arlington, TX"]
    assert dev.posted_at.isoformat() == "2026-11-09T00:00:00+00:00"
    assert dev.url == f"{TALEO}/2/jobdetail.ftl?job=2605503&lang=en"
    assert dev.description.count("without sponsorship") == 1 and "2+ years of C#" in dev.description
    assert "D.R. Horton’s" in dev.description
    assert analyst.locations == ["Tampa, Florida, US"] and analyst.description == ""

    # The portal number is cached, and a slug can carry it.
    await run(TaleoAdapter(), "drhorton|2", known_ids={"2605503", "2605746"})
    assert page.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_taleo_partial_scan_and_mobile_sections():
    rows = [_taleo_row(str(i), f"C{i}", f"Job {i}", "US-Kansas-Wichita", "11/09/2026") for i in range(2)]
    search = respx.post("https://textron.taleo.net/careersection/rest/jobboard/searchjobs").mock(
        return_value=httpx.Response(200, json=_taleo_page(rows, 40)))
    jobs = await run(TaleoAdapter(), "textron|textron|8140753014", full=False, known_ids={"C0", "C1"})
    assert len(jobs) == 2 and jobs[0].locations == ["Wichita, Kansas, US"]
    # Every page repeats the same rows, so the scan stops after the second page instead of reading 20.
    assert search.call_count == 2

    respx.post("https://zionsbancorp.taleo.net/careersection/rest/jobboard/searchjobs").mock(
        return_value=httpx.Response(200, json=_taleo_page([{"jobId": "881358", "contestNo": "071996", "column": []}], 1)))
    with pytest.raises(ValueError, match="no columns"):
        await run(TaleoAdapter(), "zionsbancorp|joinexternalmobile|40240020184")


def test_taleo_detect_and_page():
    assert taleo.detect("https://textron.taleo.net/careersection/textron/jobdetail.ftl?job=342344") == "textron|textron"
    assert taleo.detect(
        "https://raymondjames.taleo.net/careersection/raymond+james+external+career+section+uk2fgermany/jobsearch.ftl"
        "?lang=en&amp;portal=64200010414"
    ) == "raymondjames|raymond+james+external+career+section+uk2fgermany|64200010414"
    assert taleo.detect("https://careers.stlouis-mo.gov/careersection/cstl_career_portal_ext/jobsearch.ftl?lang=en") \
        == "careers.stlouis-mo.gov|cstl_career_portal_ext"
    assert taleo.detect("https://phf.tbe.taleo.net/phf02/ats/careers/v2/viewRequisition?org=COSTCO&cws=41") is None
    assert taleo.detect("https://x.taleo.net/careersection/2026PRD.1.5.9.3.0/jobsearch.ftl") is None
    assert taleo.detect("https://boards.greenhouse.io/acme") is None
    assert taleo.parse_slug("drhorton|2") == ("drhorton.taleo.net", "2", "")

    html = """<a href="https://zionsbancorp.taleo.net/careersection/joinexternalmobile/jobsearch.ftl?lang=en">m</a>
    <a href="https://zionsbancorp.taleo.net/careersection/joinexternal/jobsearch.ftl?lang=en">Search jobs</a>"""
    assert taleo.PAGE_MARKER.search(html)
    assert taleo.slug_from_page("https://www.zionsbancorporation.com/careers", html) == "zionsbancorp|joinexternal"
    assert taleo.slug_from_page("https://x.com", "<p>no board</p>") is None


# --- ADP ------------------------------------------------------------------------------------------

CID = "755a7d59-7228-4f58-8652-0928bf9894cc"
ADP_API = "https://workforcenow.adp.com/mascsr/default/careercenter/public/events/staffing/v1/job-requisitions"


def _adp_req(item, title, city, state, ext_id, posted):
    return {
        "itemID": item, "clientRequisitionID": "1249", "requisitionTitle": title,
        "customFieldGroup": {
            "dateFields": [{"dateValue": posted, "nameCode": {"codeValue": "PostingDate"}},
                           {"dateValue": "2026-11-09T19:42Z", "nameCode": {"codeValue": "CurrentServerDateTime"}}],
            "stringFields": [{"nameCode": {"codeValue": "ExternalJobID"}, "stringValue": ext_id}],
        },
        "organizationalUnits": [], "postDate": "2026-10-30T00:06:00",
        "requisitionLocations": [{"address": {"cityName": city, "countrySubdivisionLevel1": {"codeValue": state},
                                              "postalCode": "20020"},
                                  "nameCode": {"shortName": f"HQ, {city}, {state}, US"}}],
        "workLevelCode": {"shortName": "Regular Full-Time"},
    }


@pytest.mark.asyncio
@respx.mock
async def test_adp_pages_and_fetches_details_only_for_new_jobs():
    reqs = [_adp_req("9205249664982_40", "Community Health Worker", "Washington", "DC", "667027", "2026-10-29T14:36Z"),
            _adp_req("9205231945643_1", "Accounting Manager", "Washington", "DC", "636928", "2026-09-01T10:20Z"),
            _adp_req("9205231935193_1", "Quality Assurance Tester", "Jersey City", "NJ", "636925", "2026-08-31T21:03Z")]

    def listing(request):
        skip = int(request.url.params["$skip"])
        assert request.url.params["cid"] == CID and request.url.params["ccId"] == "19000101_000001"
        return httpx.Response(200, json={"jobRequisitions": reqs[skip - 1: skip - 1 + 2], "meta": {"totalNumber": 3}})

    adp.PAGE_SIZE, old = 2, adp.PAGE_SIZE
    try:
        respx.get(ADP_API).mock(side_effect=listing)
        detail = respx.get(f"{ADP_API}/9205249664982_40").mock(return_value=httpx.Response(200, json={
            **reqs[0], "requisitionDescription": "<div><p><strong>Company Overview:</strong></p><p>We sponsor H-1B visas.</p></div>"}))
        known = respx.get(f"{ADP_API}/9205231945643_1")
        other = respx.get(f"{ADP_API}/9205231935193_1").mock(return_value=httpx.Response(404))
        jobs = await run(ADPAdapter(), CID, known_ids={"9205231945643_1"})
    finally:
        adp.PAGE_SIZE = old
    assert detail.called and not known.called and other.called
    assert [j.title for j in jobs] == ["Community Health Worker", "Accounting Manager", "Quality Assurance Tester"]
    chw = jobs[0]
    assert chw.locations == ["Washington, DC"] and "sponsor H-1B" in chw.description
    assert chw.posted_at.astimezone(timezone.utc).isoformat() == "2026-10-29T14:36:00+00:00"
    assert chw.url.endswith(f"recruitment.html?cid={CID}&ccId=19000101_000001&jobId=667027&lang=en_US")
    assert jobs[2].description == ""  # detail failed: listed anyway, enrichment retries


@pytest.mark.asyncio
@respx.mock
async def test_adp_switched_off_center_fails():
    respx.get(ADP_API).mock(return_value=httpx.Response(500, json={"developerMessage": "An internal server error occurred."}))
    with pytest.raises(httpx.HTTPStatusError):
        await run(ADPAdapter(), CID)


def test_adp_detect_and_page():
    url = f"https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?cid={CID}&ccId=19000101_000001&type=JS&lang=en_US"
    assert adp.detect(url) == CID
    assert adp.detect(url.replace("19000101_000001", "9200123_77")) == f"{CID}|9200123_77"
    assert adp.detect("https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?cid=nope") is None
    html = f'<a href="https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?cid={CID}&#038;ccId=19000101_000001&#038;type=JS&#038;lang=en_US#new_tab">Jobs</a>'
    assert adp.PAGE_MARKER.search(html)
    assert adp.slug_from_page("https://www.enlightened.com/careers/", html) == CID
    with pytest.raises(ValueError):
        adp.parse_slug("enlightened")


# --- Paylocity ------------------------------------------------------------------------------------

GUID = "07da01d3-e918-4687-9c6b-b7ae0125d86d"
PL = "https://recruiting.paylocity.com/Recruiting"


def _pl_job(job_id, title, city, state, country="USA", internal=False, remote=False):
    return {"JobId": job_id, "JobTitle": title, "LocationName": f"{state} - {city}", "PublishedDate": "2026-11-06T09:57:05-06:00",
            "Description": "Join the Fast-Paced World of Ecommerce at Meyer Distributing!", "IsInternal": internal,
            "HiringDepartment": "Ecommerce", "IsRemote": remote,
            "JobLocation": {"Name": f"{state} - {city}", "City": city, "State": state, "Country": country}}


PL_BOARD = """<script>
      window.dataLayer = [{"recruiting_module_number":"50921"}];
    window.pageData = %s;
    window.atsPublic = window.atsPublic || {};
</script>""" % json.dumps({"Departments": ["All Departments"], "Jobs": [
    _pl_job(4646164, "Ecommerce Operations Support", "Jasper", "IN"),
    _pl_job(4121413, "Junior Full Stack Developer", "Indianapolis", "IN", remote=True),
    _pl_job(4000001, "Warehouse Lead (internal)", "Kingman", "AZ", internal=True),
    _pl_job(4000002, "Sales Rep", "Toronto", "ON", country="Canada"),
]})

PL_DETAIL = """<script type="application/ld+json">{"@context":"https://schema.org","@type":"JobPosting",
"title":"Ecommerce Operations Support","datePosted":"2026-11-06T09:57:05-06:00",
"description":"<p>Are you a problem solver? We are unable to sponsor work visas for this role.</p>",
"jobLocation":{"@type":"Place","address":{"@type":"PostalAddress","addressLocality":"Jasper","addressRegion":"IN","addressCountry":"US"}}}</script>"""


@pytest.mark.asyncio
@respx.mock
async def test_paylocity_reads_embedded_board():
    respx.get(f"{PL}/Jobs/All/{GUID}").mock(return_value=httpx.Response(200, text=PL_BOARD))
    detail = respx.get(f"{PL}/Jobs/Details/4646164").mock(return_value=httpx.Response(200, text=PL_DETAIL))
    known = respx.get(f"{PL}/Jobs/Details/4121413")
    jobs = await run(PaylocityAdapter(), GUID.upper(), known_ids={"4121413"})
    assert detail.called and not known.called
    assert [j.external_id for j in jobs] == ["4646164", "4121413"]  # internal and Canadian jobs dropped
    ecom, dev = jobs
    assert ecom.locations == ["Jasper, IN, US"] and "unable to sponsor" in ecom.description
    assert ecom.posted_at.astimezone(timezone.utc).isoformat() == "2026-11-06T15:57:05+00:00"
    assert ecom.department == "Ecommerce" and ecom.url == f"{PL}/Jobs/Details/4646164"
    assert dev.remote and dev.locations == ["Indianapolis, IN, USA"] and dev.description == ""


@pytest.mark.asyncio
@respx.mock
async def test_paylocity_page_without_board_data_fails():
    respx.get(f"{PL}/Jobs/All/{GUID}").mock(return_value=httpx.Response(200, text="<html>maintenance</html>"))
    with pytest.raises(ValueError):
        await run(PaylocityAdapter(), GUID)


def test_paylocity_detect_and_page():
    assert paylocity.detect(f"https://recruiting.paylocity.com/recruiting/jobs/All/{GUID}/Meyer-Distributing") == GUID
    assert paylocity.detect(f"https://recruiting.paylocity.com/Recruiting/Jobs/All/{GUID.upper()}") == GUID
    assert paylocity.detect("https://recruiting.paylocity.com/Recruiting/Jobs/Details/4121413") is None
    html = f'<iframe src="https://recruiting.paylocity.com/recruiting/jobs/All/{GUID}/Meyer-Distributing"></iframe>'
    assert paylocity.PAGE_MARKER.search(html)
    assert paylocity.slug_from_page("https://meyerdistributing.com/careers", html) == GUID
