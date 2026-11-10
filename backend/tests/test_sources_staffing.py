"""respx-mocked tests for the staffing-firm ATS adapters: JobDiva, Jobvite, Zoho Recruit, Manatal and Bullhorn.

Fixtures are trimmed-down copies of real responses captured in Nov 2026. The adapters are instantiated
directly, since registering them in ADAPTERS is a separate step.
"""

import html as htmllib
import json
from datetime import datetime, timezone

import httpx
import pytest
import respx

from app.sources import bullhorn, jobdiva, jobvite, manatal, zoho
from app.sources.bullhorn import BullhornAdapter
from app.sources.jobdiva import JobDivaAdapter
from app.sources.jobvite import JobviteAdapter
from app.sources.manatal import ManatalAdapter
from app.sources.zoho import ZohoRecruitAdapter


async def run(adapter, slug: str, **kwargs):
    async with httpx.AsyncClient() as client:
        return await adapter.fetch(client, slug, **kwargs)


# --- JobDiva ---------------------------------------------------------------------------------------

JD_KEY = "sfjdnwo10b4yndh294ykxqcwq1eg1u0b38rztezound0kwviecrtyjw5qp9pv17v"
JD_FEED = "https://www2.jobdiva.com/candidates/myjobs/getjobsrssfeed.jsp"


def jd_item(job_id: int, title: str, when: str, body: str = "<p>Build data pipelines in Spark and SQL.</p>") -> str:
    link = (f"https://www2.jobdiva.com/candidates/myjobs/openjob_outside.jsp?a={JD_KEY}&amp;from=COMP"
            f"&amp;id={job_id}&amp;SearchString=&amp;StatesString=&amp;t=1794270928032")
    return (f"<item>\n<title><![CDATA[{title}]]></title>\n<link>{link}</link>\n"
            f"<description> <![CDATA[<div>{body}</div>]]>  </description>\n<pubDate>{when}</pubDate>\n</item>")


def jd_feed(*items: str) -> str:
    return ('<?xml version="1.0" encoding="UTF-8" ?>\n<rss version="2.0"><channel>'
            "<title><![CDATA[Revive Business Solutions Jobs]]></title><description>Newly Issued Jobs</description>"
            + "".join(items) + "</channel></rss>")


@pytest.mark.asyncio
@respx.mock
async def test_jobdiva_reads_the_portal_rss_feed():
    route = respx.get(JD_FEED).mock(return_value=httpx.Response(200, text=jd_feed(
        jd_item(29603811, "Operations Analyst (26-00343) - NJ", "November 9, 2026 2:35:10 PM EST"),
        jd_item(29603812, "Release Train Engineer (RTE) (26-12502) - NJ - New Jersey", "July 23, 2026 4:34:30 PM EDT"),
        jd_item(29603813, "L & D Business Analyst (26-00342) - ", "November 3, 2026 10:12:44 AM EST"),
        jd_item(29603814, "Java Developer (26-00001) - Remote", "November 3, 2026 10:12:44 AM EST"),
    ), headers={"content-type": "application/rss+xml"}))
    adapter = JobDivaAdapter()
    jobs = await run(adapter, f"www2/{JD_KEY}")
    assert route.calls[0].request.url.params["a"] == JD_KEY
    assert route.calls[0].request.url.params["noofjobs"] == "200"
    ops, rte, ld, java = jobs
    assert (ops.external_id, ops.title, ops.locations) == ("29603811", "Operations Analyst", ["NJ"])
    assert ops.posted_at == datetime(2026, 11, 9, 19, 35, 10, tzinfo=timezone.utc)
    assert "Spark and SQL" in ops.description
    assert ops.url == f"https://www2.jobdiva.com/candidates/myjobs/openjob_outside.jsp?a={JD_KEY}&from=COMP&id=29603811"
    assert (rte.title, rte.locations) == ("Release Train Engineer (RTE)", ["New Jersey, NJ"])
    assert rte.posted_at == datetime(2026, 7, 23, 20, 34, 30, tzinfo=timezone.utc)
    assert (ld.title, ld.locations) == ("L & D Business Analyst", [])
    assert java.remote
    assert adapter.last_fetch_complete(f"www2/{JD_KEY}")


@pytest.mark.asyncio
@respx.mock
async def test_jobdiva_a_full_feed_is_capped_and_closes_nothing():
    items = [jd_item(i, f"Engineer {i} (26-{i}) - TX", "November 9, 2026 2:35:10 PM EST") for i in range(200)]
    respx.get(JD_FEED).mock(return_value=httpx.Response(200, text=jd_feed(*items)))
    adapter = JobDivaAdapter()
    assert len(await run(adapter, JD_KEY)) == 200  # a bare key means www2
    assert not adapter.last_fetch_complete(JD_KEY)


@pytest.mark.asyncio
@respx.mock
async def test_jobdiva_dead_portal_raises():
    respx.get(JD_FEED).mock(return_value=httpx.Response(200, text="This link is no longer active."))
    with pytest.raises(httpx.HTTPError):
        await run(JobDivaAdapter(), f"www2/{JD_KEY}")


def test_jobdiva_detect_and_page():
    portal = f"https://www2.jobdiva.com/portal/?a={JD_KEY}&compid=0#/"
    assert jobdiva.detect(portal) == f"www2/{JD_KEY}"
    assert jobdiva.detect(f"https://www1.jobdiva.com/candidates/myjobs/openjob_outside.jsp?a={JD_KEY}&id=1") == f"www1/{JD_KEY}"
    assert jobdiva.detect("https://www.jobdiva.com/pricing") is None
    page = f'<a href="https://www2.jobdiva.com/portal/?a={JD_KEY}&#038;compid=0#/">Open jobs</a>'
    assert jobdiva.PAGE_MARKER.search(page)
    assert jobdiva.slug_from_page("https://www.eateam.com/careers/", page) == f"www2/{JD_KEY}"
    assert jobdiva.split_title("Sr. Analyst (Inventory Accounting) (26-04581) - ") == ("Sr. Analyst (Inventory Accounting)", "")


# --- Jobvite ---------------------------------------------------------------------------------------

JV_LIST = """<body class="jv-desktop jv-page-jobs"><h1 class="jv-logo"><a href="/saama/jobs">Saama Technologies Inc Careers</a></h1>
<h3 class="h2">Analytical Services</h3>
<table class="jv-job-list"><tbody>
<tr><td class="jv-job-list-name"><a href="/saama/job/okfUAfwN">Architect (Cloud / Data)-Senior</a></td>
<td class="jv-job-list-location">London,\n England</td></tr>
<tr><td class="jv-job-list-name"><a href="/saama/job/oRz0AfwK">Senior DBT Developer</a></td>
<td class="jv-job-list-location">Campbell,\n California</td></tr>
</tbody></table>
<h3 class="h2">Delivery</h3>
<table class="jv-job-list"><tbody>
<tr><td class="jv-job-list-name"><a href="/saama/job/ofnQAfwM">Guidewire BillingCenter Lead</a></td>
<td class="jv-job-list-location"><div class="jv-meta">3 Locations</div></td></tr>
<tr><td class="jv-job-list-name"><a href="/saama/job/oqGMAfwc">UI Developer</a></td>
<td class="jv-job-list-location"><div class="jv-meta">2 Locations</div></td></tr>
</tbody></table></body>"""


def jv_detail(title: str, locations: list[dict], posted: str) -> str:
    ld = {"@context": "http://schema.org", "@type": "JobPosting", "title": title, "datePosted": posted,
          "description": "<div>5+ years of Guidewire. We sponsor H-1B.</div>", "identifier": "x",
          "jobLocation": [{"@type": "Place", "address": {"@type": "PostalAddress", **loc}} for loc in locations]}
    return f'<html><script type="application/ld+json">{json.dumps(ld)}</script></html>'


@pytest.mark.asyncio
@respx.mock
async def test_jobvite_lists_us_jobs_and_reads_details_for_new_ones():
    respx.get("https://jobs.jobvite.com/saama/jobs").mock(return_value=httpx.Response(200, text=JV_LIST))
    london = respx.get("https://jobs.jobvite.com/saama/job/okfUAfwN")
    known = respx.get("https://jobs.jobvite.com/saama/job/oRz0AfwK")
    respx.get("https://jobs.jobvite.com/saama/job/ofnQAfwM").mock(return_value=httpx.Response(200, text=jv_detail(
        "Guidewire BillingCenter Lead",
        [{"addressLocality": "Coimbatore", "addressRegion": "Tamil Nadu", "addressCountry": "India"},
         {"addressCountry": "India"}], "2026-09-23")))
    respx.get("https://jobs.jobvite.com/saama/job/oqGMAfwc").mock(return_value=httpx.Response(200, text=jv_detail(
        "UI Developer", [{"addressLocality": "Phoenix", "addressRegion": "AZ", "addressCountry": "United States"},
                         {"addressLocality": "Chennai", "addressCountry": "India"}], "2026-11-02")))
    jobs = await run(JobviteAdapter(), "saama", known_ids={"oRz0AfwK"})
    assert [j.external_id for j in jobs] == ["oRz0AfwK", "oqGMAfwc"]  # London and India-only dropped
    dbt, ui = jobs
    assert not london.called and not known.called
    assert (dbt.title, dbt.locations, dbt.department) == ("Senior DBT Developer", ["Campbell, California"], "Analytical Services")
    assert dbt.url == "https://jobs.jobvite.com/saama/job/oRz0AfwK"
    assert ui.locations == ["Phoenix, AZ, United States", "Chennai, India"]
    assert ui.posted_at == datetime(2026, 11, 2, tzinfo=timezone.utc)
    assert "sponsor H-1B" in ui.description


@pytest.mark.asyncio
@respx.mock
async def test_jobvite_follows_page_links():
    page0 = JV_LIST.replace("</body>", '<a href="/saama/jobs?p=1">Next</a></body>')
    page1 = ('<table class="jv-job-list"><tr><td class="jv-job-list-name"><a href="/saama/job/zz9">Data Engineer</a></td>'
             '<td class="jv-job-list-location">Austin, Texas</td></tr></table>')
    route = respx.get("https://jobs.jobvite.com/saama/jobs").mock(side_effect=lambda req: httpx.Response(
        200, text=page1 if req.url.params.get("p") == "1" else page0))
    jobs = await run(JobviteAdapter(), "saama", known_ids={"oRz0AfwK", "ofnQAfwM", "oqGMAfwc", "zz9"})
    assert route.call_count == 2
    assert "zz9" in {j.external_id for j in jobs}


@pytest.mark.asyncio
@respx.mock
async def test_jobvite_page_without_json_ld_uses_the_description_block():
    respx.get("https://jobs.jobvite.com/nutanix/jobs").mock(return_value=httpx.Response(200, text=(
        '<h3>Finance</h3><table class="jv-job-list"><tr><td class="jv-job-list-name">'
        '<a href="/nutanix/job/oUQKAfwO">Finance Manager</a></td>'
        '<td class="jv-job-list-location">San Jose,\n California</td></tr></table>')))
    respx.get("https://jobs.jobvite.com/nutanix/job/oUQKAfwO").mock(return_value=httpx.Response(200, text=(
        '<h2 class="jv-header">Finance Manager</h2><div class="jv-job-detail-description" ng-non-bindable>'
        "<!-- removed ===== <p>old text</p> --><p>Own the quarterly close. 7+ years of FP&amp;A.</p></div>"
        '<div class="jv-job-detail-bottom-actions"><a href="/nutanix/job/oUQKAfwO/apply">Apply</a></div>')))
    [job] = await run(JobviteAdapter(), "nutanix")
    assert "7+ years of FP&A" in job.description and "old text" not in job.description
    assert job.posted_at is None and job.department == "Finance"


def test_jobvite_detect_and_page():
    assert jobvite.detect("https://jobs.jobvite.com/saama/jobs") == "saama"
    assert jobvite.detect("https://jobs.jobvite.com/Saama/job/okfUAfwN") == "saama"
    assert jobvite.detect("https://jobs.jobvite.com/__assets__/scripts/careersite/public/iframe.js") is None
    page = ("<script>Jobvite = { careersite: 'saama' };</script><div class=\"jv-careersite\" data-careersite=\"saama\">"
            '</div><script src="https://jobs.jobvite.com/__assets__/scripts/careersite/public/iframe.js"></script>')
    assert jobvite.PAGE_MARKER.search(page)
    assert jobvite.slug_from_page("https://www.saama.com/careers/", page) == "saama"


# --- Zoho Recruit ----------------------------------------------------------------------------------

ZOHO_JOBS = [
    {"Remote_Job": True, "Posting_Title": "Zoho Consultant", "City": None, "Country": None,
     "Job_Description": "In this consulting role you will interface directly with clients...",
     "Job_Type": "Full time", "Job_Opening_Name": "Zoho Consultant", "id": "421829000001328001",
     "Date_Opened": "2025-10-22", "Industry": "IT Services"},
    {"Remote_Job": False, "Posting_Title": "Java Developer", "City": "Dallas", "State": "Texas",
     "Country": "United States", "Job_Description": "Java and Spring...", "id": "421829000001328002",
     "Date_Opened": "2026-11-08"},
    {"Remote_Job": False, "Posting_Title": "Finance Associate", "City": "Pune", "Country": "India",
     "Job_Description": "Accounts...", "id": "421829000001328003", "Date_Opened": "2026-11-01"},
]


def zoho_list_page(jobs: list[dict]) -> str:
    value = htmllib.escape(json.dumps(jobs), quote=True).replace("&quot;", "&#34;")
    return f'<html><body><input type="hidden" value="{value}" id="jobs"><input type="hidden" value="{{}}" id="meta"></body></html>'


def zoho_detail_page(job: dict) -> str:
    js = json.dumps([job]).replace("\\", "\\\\").replace('"', "\\x22").replace("/", "\\/").replace("-", "\\-")
    return f"<script>var csrfToken = \"x\";\nvar jobs = JSON.parse('{js}');\nvar other = 1;</script>"


@pytest.mark.asyncio
@respx.mock
async def test_zoho_lists_jobs_and_reads_full_descriptions():
    base = "https://zenatta.zohorecruit.com/jobs/Careers"
    respx.get(base).mock(return_value=httpx.Response(200, text=zoho_list_page(ZOHO_JOBS)))
    full = dict(ZOHO_JOBS[0], Job_Description="<html><body><p>This is a remote position.</p>"
                "<p>2-3 years of Zoho CRM \u2014 Deluge, APIs.</p></body></html>", State="NJ", Country="United States")
    respx.get(f"{base}/421829000001328001/Zoho-Consultant").mock(return_value=httpx.Response(200, text=zoho_detail_page(full)))
    known = respx.get(f"{base}/421829000001328002/Java-Developer")
    india = respx.get(f"{base}/421829000001328003/Finance-Associate")
    consultant, java = await run(ZohoRecruitAdapter(), "zenatta", known_ids={"421829000001328002"})
    assert not known.called and not india.called
    assert consultant.remote and consultant.locations == ["NJ, United States"]
    assert "Zoho CRM \u2014 Deluge, APIs." in consultant.description
    assert consultant.posted_at == datetime(2025, 10, 22, tzinfo=timezone.utc)
    assert consultant.url == f"{base}/421829000001328001/Zoho-Consultant"
    assert consultant.department == "IT Services"
    assert java.locations == ["Dallas, Texas, United States"]


@pytest.mark.asyncio
@respx.mock
async def test_zoho_missing_portal_is_a_dead_board():
    respx.get("https://nope.zohorecruit.com/jobs/Careers").mock(return_value=httpx.Response(
        200, text="<title>Page does not exist</title><h2>nope.zohorecruit.com <span>does not exist.</span></h2>"))
    with pytest.raises(httpx.HTTPStatusError) as err:
        await run(ZohoRecruitAdapter(), "nope")
    assert err.value.response.status_code == 404


def test_zoho_slugs_and_js_strings():
    assert zoho.detect("https://zenatta.zohorecruit.com/jobs/Careers") == "zenatta"
    assert zoho.detect("https://zenatta.zohorecruit.com/jobs/Careers/421829000001328001/Zoho-Consultant") == "zenatta"
    assert zoho.detect("https://acme.zohorecruit.in/jobs/Openings") == "acme.zohorecruit.in/Openings"
    assert zoho.detect("https://acme.zohorecruit.com/careers") == "acme"
    assert zoho.detect("https://www.zohorecruit.com/") is None
    assert zoho.parse_slug("acme.zohorecruit.in/Openings") == ("acme.zohorecruit.in", "Openings")
    assert zoho.parse_slug("zenatta") == ("zenatta.zohorecruit.com", "Careers")
    assert zoho.js_unescape(r"\x22a\x22:\x22b\-c\\\/d\\n\u00e9") == '"a":"b-c\\/d\\n\u00e9'
    page = '<iframe src="https://zenatta.zohorecruit.com/jobs/Careers?embed=1"></iframe>'
    assert zoho.PAGE_MARKER.search(page)
    assert zoho.slug_from_page("https://zenatta.com/careers", page) == "zenatta"


# --- Manatal ---------------------------------------------------------------------------------------

MANATAL_API = "https://core.api.manatal.com/open/v3/career-page/acmestaff/jobs/"


@pytest.mark.asyncio
@respx.mock
async def test_manatal_pages_through_the_open_api():
    page2 = f"{MANATAL_API}?page=2&page_size=100"

    def answer(req):
        if req.url.params.get("page") == "2":
            return httpx.Response(200, json={"count": 3, "next": None, "results": [
                {"id": "3", "hash": "C3", "position_name": "Data Analyst", "description": "<p>SQL</p>",
                 "city": "", "state": "", "country": "", "location_display": "", "is_remote": "True"}]})
        return httpx.Response(200, json={"count": 3, "next": page2, "results": [
            {"id": "1", "hash": "L9Y69V66", "organization_name": "IT", "position_name": "Java Developer",
             "description": "<p>Spring Boot, AWS.</p>", "country": "United States", "state": "Texas", "city": "Austin",
             "location_display": "Austin, Texas, United States", "is_remote": "None"},
            {"id": "2", "hash": "B2", "position_name": "Content Intern", "description": "<p>Write.</p>",
             "country": "Thailand", "state": "Bangkok", "city": "Bangkok", "location_display": "Bangkok, Bangkok, Thailand"},
        ]})

    route = respx.get(url__startswith=MANATAL_API).mock(side_effect=answer)
    java, analyst = await run(ManatalAdapter(), "acmestaff")
    assert route.call_count == 2
    assert (java.external_id, java.title, java.department) == ("1", "Java Developer", "IT")
    assert java.url == "https://www.careers-page.com/acmestaff/job/L9Y69V66"
    assert java.locations == ["Austin, Texas, United States"] and not java.remote
    assert "Spring Boot" in java.description
    assert analyst.remote and analyst.locations == []


def test_manatal_detect():
    assert manatal.detect("https://www.careers-page.com/acmestaff") == "acmestaff"
    assert manatal.detect("https://careers-page.com/acmestaff/job/L9Y69V66") == "acmestaff"
    assert manatal.detect(MANATAL_API) == "acmestaff"
    page = '<a href="https://www.careers-page.com/acmestaff">Jobs</a>'
    assert manatal.PAGE_MARKER.search(page) and manatal.slug_from_page("https://acme.com", page) == "acmestaff"


# --- Bullhorn --------------------------------------------------------------------------------------

BH_API = "https://public-rest91.bullhornstaffing.com/rest-services/1VCNF4/search/JobOrder"


@pytest.mark.asyncio
@respx.mock
async def test_bullhorn_reads_the_public_rest_search():
    rows = [
        {"id": 21342, "title": "Senior Software Engineer (Contract)", "employmentType": "Contract",
         "dateLastPublished": 1793008540490, "address": {"city": "Chicago", "state": "Illinois", "countryName": "United States"},
         "publicDescription": "<p>5+ years of Python. No C2C.</p>", "publishedCategory": {"id": 1, "name": "Information Technology"}},
        {"id": 21335, "title": "Data Analyst", "dateLastPublished": 1792847804797,
         "address": {"city": "Toronto", "state": "ON", "countryName": "Canada"}, "publicDescription": "<p>x</p>"},
        {"id": 19244, "title": "Remote Data Engineer", "dateAdded": 1792004600333, "address": {},
         "publicDescription": "<p>Spark</p>"},
    ]
    route = respx.get(BH_API).mock(return_value=httpx.Response(200, json={"total": 3, "start": 0, "count": 3, "data": rows}))
    swe, de = await run(BullhornAdapter(), "91:1VCNF4:jobs.acme.com")
    params = route.calls[0].request.url.params
    assert params["query"] == "(isOpen:1) AND (isDeleted:0)" and params["count"] == "500"
    assert (swe.external_id, swe.locations, swe.department) == ("21342", ["Chicago, Illinois, United States"], "Information Technology")
    assert swe.url == "https://jobs.acme.com/#/jobs/21342"
    assert swe.posted_at == datetime.fromtimestamp(1793008540.490, tz=timezone.utc)
    assert "5+ years of Python" in swe.description
    assert de.remote and de.locations == []
    assert route.call_count == 1


def test_bullhorn_slugs():
    assert bullhorn.detect(f"{BH_API}?query=x") == "91:1VCNF4"
    app_json = '{"service": {"batchSize": 500, "corpToken": "1VCNF4", "port": null, "swimlane": "91"}}'
    assert bullhorn.slug_from_page("https://jobs.acme.com/app.json", app_json) == "91:1VCNF4:jobs.acme.com"
    assert bullhorn.slug_from_page("https://x.com", '{"corpToken": "[ CORP TOKEN ]", "swimlane": "1"}') is None
    assert bullhorn.parse_slug("91:1VCNF4") == ("91", "1VCNF4", "")
