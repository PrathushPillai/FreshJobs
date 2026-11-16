"""respx-mocked tests for the Radancy (TalentBrew) and Avature career-site adapters.

Fixtures are trimmed copies of real pages captured live in Nov 2026 (careers.arm.com,
careers.moodys.com/en, koch.avature.net/careers, bloomberg.avature.net/careers, synopsys.avature.net/careers).
The adapters are instantiated directly, so these tests don't depend on their registration in ADAPTERS.
"""

import json
from datetime import datetime, timezone

import httpx
import pytest
import respx

from app.sources import avature, http_util, radancy
from app.sources.avature import AvatureAdapter
from app.sources.radancy import RadancyAdapter


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    async def instant(_seconds):
        return None

    monkeypatch.setattr(http_util.asyncio, "sleep", instant)


# --- Radancy ------------------------------------------------------------------------------------

ARM = "https://careers.arm.com"


def _url(loc: str, lastmod: str = "2026-11-09T13:54:37.0366706Z") -> str:
    return f"<url><loc>{loc}</loc><lastmod>{lastmod}</lastmod><changefreq>weekly</changefreq></url>"


ARM_SITEMAP = (
    '﻿<?xml version="1.0" encoding="utf-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    + _url(ARM)
    + _url(f"{ARM}/category/software-engineering-jobs/33099/8081696/1")
    + _url(f"{ARM}/location/united-states-jobs/33099/6252001/2")
    + _url(f"{ARM}/location/united-kingdom-jobs/33099/2635167/2")
    + _url(f"{ARM}/location/austin-texas-united-states-jobs/33099/6252001-4736286-4671654/4")
    + _url(f"{ARM}/location/cambridge-cambridgeshire-united-kingdom-jobs/33099/2635167-6269131-2653940/4")
    + _url(f"{ARM}/location/bengaluru-karnataka-india-jobs/33099/1269750-1267701-1277333/4")
    + _url(f"{ARM}/employment/austin-hardware-engineering-jobs/33099/8081664/6252001-4736286-4737316-4671654/4")
    + _url(f"{ARM}/job/austin/principal-technical-program-manager/33099/102874076256")
    + _url(f"{ARM}/job/austin/staff-cpu-verification-engineer/33099/102860632272")
    + _url(f"{ARM}/job/cambridge/graduate-quality-analytics-and-automation-engineer/33099/103071142448")
    + _url(f"{ARM}/job/bengaluru/principal-software-engineer/33099/103069216016")
    + _url(f"{ARM}/job/remote/field-applications-engineer/33099/103000000001")
    + "</urlset>"
)

ARM_POSTING = {
    "@context": "http://schema.org", "@type": "JobPosting",
    "datePosted": "2026-11-04",
    "description": "<p>Arm is looking for an experienced Technical Program Manager&nbsp;to lead projects.</p>"
                   "<p>We are unable to sponsor visas for this role.</p>",
    "title": "Principal Technical Program Manager",
    "url": f"{ARM}/job/austin/principal-technical-program-manager/33099/102874076256",
    "hiringOrganization": {"@type": "Organization", "name": "ARM"},
    "jobLocation": [{"@type": "Place", "address": {
        "@type": "PostalAddress", "addressLocality": "Austin", "addressRegion": "Texas",
        "addressCountry": "United States", "streetAddress": "5707 Southwest Pkwy", "postalCode": ""}}],
}


def _radancy_page(posting: dict | None, h1: str = "") -> str:
    ld = f'<script type="application/ld+json">{json.dumps(posting)}</script>' if posting else ""
    return (f'<html><head><link href="https://tbcdn.talentbrew.com/company/33099/css/site.css" rel="stylesheet">{ld}'
            f'</head><body data-company-site-id="60641"><h1>{h1}</h1></body></html>')


@pytest.mark.asyncio
@respx.mock
async def test_radancy_lists_us_jobs_from_the_sitemap_and_reads_new_ones():
    respx.get(f"{ARM}/sitemap.xml").mock(return_value=httpx.Response(200, text=ARM_SITEMAP))
    new = respx.get(f"{ARM}/job/austin/principal-technical-program-manager/33099/102874076256").mock(
        return_value=httpx.Response(200, text=_radancy_page(ARM_POSTING)))
    known = respx.get(f"{ARM}/job/austin/staff-cpu-verification-engineer/33099/102860632272")
    uk = respx.get(url__regex=r".*/job/(cambridge|bengaluru)/.*")
    remote = respx.get(f"{ARM}/job/remote/field-applications-engineer/33099/103000000001").mock(
        return_value=httpx.Response(200, text=_radancy_page(None, "Field Applications Engineer")))

    async with httpx.AsyncClient() as client:
        jobs = await RadancyAdapter().fetch(client, "careers.arm.com", known_ids={"102860632272"})

    by_id = {j.external_id: j for j in jobs}
    # Cambridge (UK) and Bengaluru match only non-US places, so they're skipped without a request.
    assert set(by_id) == {"102874076256", "102860632272", "103000000001"}
    assert new.called and remote.called and not known.called and not uk.called

    tpm = by_id["102874076256"]
    assert tpm.title == "Principal Technical Program Manager"
    assert tpm.locations == ["Austin, Texas, United States"]
    assert tpm.posted_at == datetime(2026, 11, 4, tzinfo=timezone.utc)
    assert "unable to sponsor" in tpm.description and "<p>" not in tpm.description
    assert tpm.url.endswith("/33099/102874076256") and tpm.company_name == ""

    known_job = by_id["102860632272"]
    assert known_job.title == "Staff Cpu Verification Engineer" and known_job.locations == ["Austin"]
    # No JSON-LD: the page's <h1> gives the title, and the city slug says remote.
    assert by_id["103000000001"].title == "Field Applications Engineer" and by_id["103000000001"].remote


@pytest.mark.asyncio
@respx.mock
async def test_radancy_language_prefix_and_failed_details():
    base = "https://careers.moodys.com/en"
    sitemap = ('<?xml version="1.0" encoding="utf-8"?><urlset>'
               + _url(f"{base}/location/new-york-new-york-united-states-jobs/49841/6252001-5128638-5128581/4")
               + _url(f"{base}/job/new-york/sr-modeling-services-analyst/49841/102858762192")
               + _url(f"{base}/job/new-york/vp-credit-ratings/49841/100341497648") + "</urlset>")
    respx.get(f"{base}/sitemap.xml").mock(return_value=httpx.Response(200, text=sitemap))
    respx.get(f"{base}/job/new-york/sr-modeling-services-analyst/49841/102858762192").mock(
        return_value=httpx.Response(200, text=_radancy_page({**ARM_POSTING, "title": "Sr Modeling Services Analyst"})))
    respx.get(f"{base}/job/new-york/vp-credit-ratings/49841/100341497648").mock(return_value=httpx.Response(500))

    async with httpx.AsyncClient() as client:
        jobs = await RadancyAdapter().fetch(client, "careers.moodys.com/en")
    # The failed page is left out (still new next poll), not saved half-read.
    assert [j.title for j in jobs] == ["Sr Modeling Services Analyst"]


@pytest.mark.asyncio
@respx.mock
async def test_radancy_error_page_is_a_failed_fetch_not_an_empty_board():
    respx.get(f"{ARM}/sitemap.xml").mock(return_value=httpx.Response(200, text="<html>Access denied</html>"))
    async with httpx.AsyncClient() as client:
        with pytest.raises(ValueError):
            await RadancyAdapter().fetch(client, "careers.arm.com")


def test_radancy_country_from_place_slugs():
    _jobs, places = radancy.parse_sitemap(ARM_SITEMAP)
    assert radancy.maybe_us("austin", places)
    assert not radancy.maybe_us("cambridge", places) and not radancy.maybe_us("bengaluru", places)
    assert radancy.maybe_us("springdale", places)  # no listed place: kept, its page decides
    assert not radancy.maybe_us("united-kingdom", places) and radancy.maybe_us("united-states", places)


def test_radancy_detect_and_page_marker():
    assert radancy.detect(f"{ARM}/job/austin/x/33099/102874076256") == "careers.arm.com"
    assert radancy.detect("https://careers.moodys.com/en/search-jobs") == "careers.moodys.com/en"
    assert radancy.detect("https://jobs.spectrum.com/search-jobs/results?ActiveFacetID=0") == "jobs.spectrum.com"
    assert radancy.detect("https://jobs.spectrum.com/") is None
    assert radancy.detect("https://boards.greenhouse.io/acme/jobs/1") is None
    page = _radancy_page(None)
    assert radancy.PAGE_MARKER.search(page) and not radancy.PAGE_MARKER.search("<html>Workday</html>")
    assert radancy.slug_from_page("https://careers.moodys.com/en", page) == "careers.moodys.com/en"
    assert radancy.slug_from_page("https://jobs.spectrum.com/", page) == "jobs.spectrum.com"
    assert radancy.slug_from_page("https://jobs.spectrum.com/", "<html></html>") is None


# --- Avature ------------------------------------------------------------------------------------

KOCH = "https://koch.avature.net"

KOCH_INDEX = (
    '<?xml version="1.0" encoding="UTF-8"?>\n<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    f"<sitemap><loc>{KOCH}/es_ES/careers/sitemap.xml</loc></sitemap>"
    f"<sitemap><loc>{KOCH}/en_US/careers/sitemap.xml</loc></sitemap>"
    f"<sitemap><loc>{KOCH}/de_DE/careers/sitemap.xml</loc></sitemap>\n</sitemapindex>"
)


def _aurl(loc: str, lastmod: str) -> str:
    return (f'<url><loc>{loc}</loc><lastmod>{lastmod}</lastmod>'
            f'<xhtml:link rel="alternate" href="{loc}" hreflang="x-default"/></url>')


KOCH_SITEMAP = (
    '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
    'xmlns:xhtml="http://www.w3.org/1999/xhtml">\n'
    + _aurl(f"{KOCH}/en_US/careers/AgentCreate", "2026-08-22")
    + _aurl(f"{KOCH}/en_US/careers/JobDetail", "2026-08-22")
    + _aurl(f"{KOCH}/en_US/careers/JobDetail/United-States-Sr-Director-Technology-Development/178597", "2026-10-30")
    + _aurl(f"{KOCH}/en_US/careers/JobDetail/Edmonton-Alberta-Canada-Tower-Mechanic-Level-II/154020", "2024-03-18")
    + "\n</urlset>"
)


def _field(label: str | None, value_html: str) -> str:
    lab = f'<div class="article__content__view__field__label">\n {label} </div>' if label else ""
    return (f'<div class="article__content__view__field m--b--m">{lab}'
            f'<div class="article__content__view__field__value">{value_html}</div></div>')


LONG = "Your Job. The Director, Technology Development will evaluate the Datacenter/AI rack market. " * 3

KOCH_JOB = (
    '<html><head><title>Director, Technology Development - United States - 178597 - Koch</title>'
    '<meta property="og:title" content="Director, Technology Development" />'
    '<link href="/portal/4/favicon.ico" rel="shortcut icon"></head><body>'
    '<div class="article__content__view">'
    + _field("Location(s)", "Austin, Texas | Fremont, California")
    + _field("Eligible for remote", "Yes")
    + _field("Company", "Molex")
    + _field("Job Number", "178597 <a>Apply now</a>")
    + _field(None, f"<p>{LONG}</p><p>Molex will not sponsor visas for this role.</p>")
    + "</div></body></html>"
)


@pytest.mark.asyncio
@respx.mock
async def test_avature_reads_the_english_sitemap_and_new_job_pages():
    respx.get(f"{KOCH}/careers/sitemap_index.xml").mock(return_value=httpx.Response(200, text=KOCH_INDEX))
    en = respx.get(f"{KOCH}/en_US/careers/sitemap.xml").mock(return_value=httpx.Response(200, text=KOCH_SITEMAP))
    other = respx.get(url__regex=r".*/(es_ES|de_DE)/careers/sitemap.xml")
    page = respx.get(f"{KOCH}/en_US/careers/JobDetail/United-States-Sr-Director-Technology-Development/178597").mock(
        return_value=httpx.Response(200, text=KOCH_JOB))
    known = respx.get(f"{KOCH}/en_US/careers/JobDetail/Edmonton-Alberta-Canada-Tower-Mechanic-Level-II/154020")

    async with httpx.AsyncClient() as client:
        jobs = await AvatureAdapter().fetch(client, "koch.avature.net/careers", known_ids={"154020"})

    assert en.called and not other.called and page.called and not known.called
    by_id = {j.external_id: j for j in jobs}
    assert set(by_id) == {"178597", "154020"}

    job = by_id["178597"]
    assert job.title == "Director, Technology Development"
    assert job.locations == ["Austin, Texas", "Fremont, California"] and job.remote
    assert job.company_name == "Molex"
    assert "Datacenter/AI rack market" in job.description and "not sponsor visas" in job.description
    assert "Apply now" not in job.description
    assert job.posted_at == datetime(2026, 10, 30, tzinfo=timezone.utc)  # no date field: the sitemap's lastmod
    assert job.url == f"{KOCH}/en_US/careers/JobDetail/United-States-Sr-Director-Technology-Development/178597"

    assert by_id["154020"].title == "Edmonton Alberta Canada Tower Mechanic Level II"


SYNOPSYS_JOB = (
    '<html><head><meta property="og:title" content="Analog Design, Principal Engineer - 14929" /></head><body>'
    + _field("Job Title", "Application Engineer-Staff Engineer-14929")
    + _field("Job ID", "14929")
    + _field("Country", "United States")
    + _field("City", "Austin")
    + _field("Date Posted", "27-Jan-2026")
    + _field("Remote Eligible", "No")
    + _field("Job Description and Requirements", f"<p>We Are: {LONG}</p>")
    + _field(None, "Short note")
    + "</body></html>"
)

BLOOMBERG_JOB = (
    '<html><head><meta property="og:title" content="2027 - Analytics &amp;amp; Sales - New York" /></head><body>'
    + _field(None, "2027 - Analytics &amp; Sales - New York")
    + _field("Location", "New York")
    + _field("Business Area", "Sales")
    + _field(None, f"<p>{LONG}</p>")
    + "</body></html>"
)


def test_avature_job_page_variants():
    syn = avature.parse_job_page(SYNOPSYS_JOB, "14929")
    assert syn["title"] == "Application Engineer-Staff Engineer"
    assert syn["locations"] == ["Austin, United States"] and not syn["remote"]
    assert syn["posted_at"] == datetime(2026, 1, 27, tzinfo=timezone.utc)
    assert syn["description"].startswith("We Are:") and "Short note" not in syn["description"]
    assert syn["company"] == ""

    bb = avature.parse_job_page(BLOOMBERG_JOB, "45729")
    assert bb["title"] == "2027 - Analytics & Sales - New York"
    assert bb["locations"] == ["New York"] and bb["company"] == ""
    assert "Datacenter" in bb["description"] and bb["posted_at"] is None


@pytest.mark.asyncio
@respx.mock
async def test_avature_explicit_locale_and_single_sitemap_index():
    bb = "https://bloomberg.avature.net"
    respx.get(f"{bb}/careers/sitemap_index.xml").mock(return_value=httpx.Response(200, text=(
        f'<?xml version="1.0"?><sitemapindex><sitemap><loc>{bb}/careers/sitemap.xml</loc></sitemap></sitemapindex>')))
    respx.get(f"{bb}/careers/sitemap.xml").mock(return_value=httpx.Response(200, text=(
        '<?xml version="1.0"?><urlset>'
        + _aurl(f"{bb}/careers/JobDetail/2027-Analytics-Sales-New-York/45729", "2026-10-19") + "</urlset>")))
    respx.get(f"{bb}/careers/JobDetail/2027-Analytics-Sales-New-York/45729").mock(
        return_value=httpx.Response(200, text=BLOOMBERG_JOB))
    async with httpx.AsyncClient() as client:
        [job] = await AvatureAdapter().fetch(client, "bloomberg.avature.net/careers")
    assert job.title == "2027 - Analytics & Sales - New York" and job.locations == ["New York"]

    respx.get(f"{KOCH}/en_US/careers/sitemap.xml").mock(return_value=httpx.Response(200, text=KOCH_SITEMAP))
    async with httpx.AsyncClient() as client:
        jobs = await AvatureAdapter().fetch(client, "koch.avature.net/en_US/careers", known_ids={"178597", "154020"})
    assert {j.external_id for j in jobs} == {"178597", "154020"}


@pytest.mark.asyncio
@respx.mock
async def test_avature_error_page_is_a_failed_fetch():
    respx.get(f"{KOCH}/careers/sitemap_index.xml").mock(
        return_value=httpx.Response(200, text="<html>Request not processed</html>"))
    async with httpx.AsyncClient() as client:
        with pytest.raises(ValueError):
            await AvatureAdapter().fetch(client, "koch.avature.net/careers")


DELOITTE = "https://apply.deloitte.com"


def _search_page(*ids: int) -> str:
    links = "".join(f'<a href="{DELOITTE}/en_US/careers/JobDetail/Azure-Cloud-Engineer-{i}/{i}">x</a>' for i in ids)
    return f"<html><body>{links}<a href='{DELOITTE}/en_US/careers/SearchJobs/?jobOffset=10'>next</a></body></html>"


@pytest.mark.asyncio
@respx.mock
async def test_avature_empty_sitemap_falls_back_to_search_pages(monkeypatch):
    monkeypatch.setattr(avature, "SEARCH_PAGE_DELAY", 0)
    # Deloitte's en_US sitemap has answered 200 with an empty body since 2026-11-10.
    respx.get(f"{DELOITTE}/careers/sitemap_index.xml").mock(return_value=httpx.Response(200, text=(
        f'<?xml version="1.0"?><sitemapindex><sitemap><loc>{DELOITTE}/en_US/careers/sitemap.xml</loc></sitemap>'
        "</sitemapindex>")))
    respx.get(f"{DELOITTE}/en_US/careers/sitemap.xml").mock(return_value=httpx.Response(200, text=""))
    pages = {0: _search_page(101, 102), 10: _search_page(102, 103), 20: _search_page()}
    search = respx.get(url__startswith=f"{DELOITTE}/en_US/careers/SearchJobs/").mock(
        side_effect=lambda req: httpx.Response(200, text=pages[int(req.url.params["jobOffset"])]))
    detail = respx.get(f"{DELOITTE}/en_US/careers/JobDetail/Azure-Cloud-Engineer-103/103").mock(
        return_value=httpx.Response(200, text=KOCH_JOB))
    adapter = AvatureAdapter()
    async with httpx.AsyncClient() as client:
        jobs = await adapter.fetch(client, "apply.deloitte.com/careers", known_ids={"101", "102"})
    assert search.call_count == 3 and detail.called
    assert {j.external_id for j in jobs} == {"101", "102", "103"}
    assert adapter.last_fetch_complete("apply.deloitte.com/careers")

    pages[20] = _search_page()
    pages[0] = _search_page()  # empty sitemap and no search results: a failed fetch, not "no jobs"
    async with httpx.AsyncClient() as client:
        with pytest.raises(ValueError):
            await adapter.fetch(client, "apply.deloitte.com/careers")


@pytest.mark.asyncio
@respx.mock
async def test_avature_search_fallback_partial_scan_stops_at_known_jobs(monkeypatch):
    monkeypatch.setattr(avature, "SEARCH_PAGE_DELAY", 0)
    respx.get(f"{DELOITTE}/careers/sitemap_index.xml").mock(return_value=httpx.Response(200, text=(
        f'<?xml version="1.0"?><sitemapindex><sitemap><loc>{DELOITTE}/en_US/careers/sitemap.xml</loc></sitemap>'
        "</sitemapindex>")))
    respx.get(f"{DELOITTE}/en_US/careers/sitemap.xml").mock(return_value=httpx.Response(200, text=""))
    pages = {0: _search_page(105, 104), 10: _search_page(103, 102), 20: _search_page(101), 30: _search_page()}
    search = respx.get(url__startswith=f"{DELOITTE}/en_US/careers/SearchJobs/").mock(
        side_effect=lambda req: httpx.Response(200, text=pages[int(req.url.params["jobOffset"])]))
    respx.get(url__regex=r".*/JobDetail/.*/105$").mock(return_value=httpx.Response(200, text=KOCH_JOB))
    adapter = AvatureAdapter()
    adapter._full_search_at["apply.deloitte.com/careers"] = avature.time.monotonic()  # read in full just now
    async with httpx.AsyncClient() as client:
        jobs = await adapter.fetch(client, "apply.deloitte.com/careers", known_ids={"104", "103", "102", "101"})
    assert search.call_count == 2  # page 2 had only known jobs
    assert {j.external_id for j in jobs} == {"105", "104", "103", "102"}
    assert not adapter.last_fetch_complete("apply.deloitte.com/careers")  # a partial list closes nothing


def test_avature_slugs_detect_and_page_marker():
    assert avature.parse_slug("koch.avature.net/careers") == ("koch.avature.net", "", "careers")
    assert avature.parse_slug("koch.avature.net/en_US/careers") == ("koch.avature.net", "en_US", "careers")
    with pytest.raises(ValueError):
        avature.parse_slug("koch.avature.net")

    assert avature.detect(f"{KOCH}/en_US/careers/JobDetail/X/178597") == "koch.avature.net/careers"
    assert avature.detect(f"{KOCH}/en_US/careers/SearchJobs/?731=6039&732=6299") == "koch.avature.net/careers"
    assert avature.detect("https://bloomberg.avature.net/careers") == "bloomberg.avature.net/careers"
    assert avature.detect("https://apply.deloittece.com/careers/SearchJobs") == "apply.deloittece.com/careers"
    assert avature.detect(f"{KOCH}/portal/4/favicon.ico") is None
    assert avature.detect("https://example.com/careers") is None

    gp = ('<a href="https://koch.avature.net/en_US/careers/SearchJobs/?731=6039&amp;732=6299">Jobs</a>'
          '<a href="https://koch.avature.net/en_US/careers/SearchJobs/?731=6040&amp;732=6299">More</a>')
    assert avature.PAGE_MARKER.search(gp)
    assert avature.slug_from_page("https://www.gp.com/careers", gp) == "koch.avature.net/careers"
    assert avature.slug_from_page("https://jobs.example.com/careers/Home", KOCH_JOB) == "jobs.example.com/careers"
    assert avature.slug_from_page("https://www.example.com/careers", "<html>Greenhouse</html>") is None


def test_avature_dates():
    assert avature.parse_date("27-Jan-2026") == datetime(2026, 1, 27, tzinfo=timezone.utc)
    assert avature.parse_date("2026-10-30") == datetime(2026, 10, 30, tzinfo=timezone.utc)
    assert avature.parse_date("January 5, 2026") == datetime(2026, 1, 5, tzinfo=timezone.utc)
    assert avature.parse_date("soon") is None and avature.parse_date("") is None
