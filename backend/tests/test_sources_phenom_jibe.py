"""respx-mocked tests for the Phenom People and iCIMS Jibe career-site adapters.

Fixtures are trimmed copies of real responses captured live in Nov 2026 (careers.pentair.com/us/en,
jobs.cvshealth.com, careers.xoriant.com, careers.ice.com, careers.heb.com/careers-home).
The adapters are instantiated directly, so these tests don't depend on their registration in ADAPTERS.
"""

import json
from datetime import datetime, timezone

import httpx
import pytest
import respx

from app.sources import http_util, jibe, phenom
from app.sources.jibe import JibeAdapter
from app.sources.phenom import PhenomAdapter


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    async def instant(_seconds):
        return None

    monkeypatch.setattr(http_util.asyncio, "sleep", instant)


# --- Phenom -------------------------------------------------------------------------------------

PENTAIR = "https://careers.pentair.com"


def ph_job(i: int, *, country="United States of America", city="Delavan", state="Wisconsin", **kw) -> dict:
    job = {
        "jobId": f"R2{i:04d}", "reqId": f"R2{i:04d}", "title": f"Product Manager {i}", "type": "Full time",
        "category": "Product Management", "city": city, "state": state, "country": country,
        "location": f"{city}, {state}, {country}, 53115-2011",
        "multi_location": [f"{city}, {state}, {country}, 53115-2011"],
        "postedDate": "2026-09-28T00:00:00.000+0000", "dateCreated": "2026-09-20T12:41:01.244+0000",
        "descriptionTeaser": "Short teaser.", "isMultiLocation": False,
        "applyUrl": f"https://pentair.wd5.myworkdayjobs.com/Pentair_Careers/job/Delavan-WI/X_R2{i:04d}/apply",
    }
    job.update(kw)
    return job


def ph_response(jobs: list[dict], total: int, aggs: dict | None = None) -> dict:
    return {"refineSearch": {"status": 200, "hits": len(jobs), "totalHits": total, "data": {
        "jobs": jobs, "aggregations": [{"field": k, "value": v} for k, v in (aggs or {}).items()],
    }}}


def ph_detail_page(description: str) -> str:
    ddo = {"siteConfig": {}, "jobDetail": {"status": 200, "data": {"job": {"description": description}}}}
    return f"<html><script>phApp.ddo = {json.dumps(ddo)}; phApp.experimentData = {{}};</script></html>"


def test_phenom_parse_slug_and_detect():
    assert phenom.parse_slug("careers.pentair.com/us/en") == ("careers.pentair.com", "us", "en")
    assert phenom.parse_slug("https://careers.xoriant.com/in/en/") == ("careers.xoriant.com", "in", "en")
    assert phenom.parse_slug("jobs.cvshealth.com") == ("jobs.cvshealth.com", "us", "en")
    with pytest.raises(ValueError):
        phenom.parse_slug("nohost")
    assert phenom.detect("https://acme.phenompeople.com/us/en/job/R1/x") == "acme.phenompeople.com/us/en"
    assert phenom.detect("https://cdn.phenompeople.com/CareerConnectResources/x.js") is None
    assert phenom.detect("https://careers.pentair.com/us/en/job/R1") is None  # own domain: use the page


def test_phenom_slug_from_page():
    html = ('<script src="https://cdn.phenompeople.com/CareerConnectResources/pcs/x.js"></script>'
            '<script>phApp.ddo = {"siteConfig": {"data": {"country": "in", "locale": "en_in"}}};</script>')
    assert phenom.slug_from_page("https://careers.xoriant.com/in/en/home", html) == "careers.xoriant.com/in/en"
    assert phenom.slug_from_page("https://careers.xoriant.com/", html) == "careers.xoriant.com/in/en"
    assert phenom.slug_from_page("https://example.com/careers", "<html>Workday</html>") is None


def test_phenom_clean_location():
    assert phenom.clean_location("294 HARVARD STREET, Brookline, Massachusetts,United States") == \
        "Brookline, Massachusetts, United States"
    assert phenom.clean_location("Delavan, Wisconsin, United States of America, 53115-2011") == \
        "Delavan, Wisconsin, United States"
    assert phenom.clean_location("Enschede, Netherlands, 7547 TC") == "Enschede, Netherlands"


@pytest.mark.asyncio
@respx.mock
async def test_phenom_fetch_us_jobs_with_descriptions_for_new_ids_only():
    bodies = []

    def widgets(request):
        body = json.loads(request.content)
        bodies.append(body)
        assert body["ddoKey"] == "refineSearch" and body["lang"] == "en_us" and body["country"] == "us"
        if not body["selected_fields"]:
            return httpx.Response(200, json=ph_response([ph_job(0)], 3, {"country": {
                "United States of America": 2, "Netherlands": 1}}))
        assert body["selected_fields"] == {"country": ["United States of America"]}
        jobs = [ph_job(1), ph_job(2, city="Charlotte", state="North Carolina", title="Data Engineer")]
        return httpx.Response(200, json=ph_response(jobs, 2, {"state": {"Wisconsin": 1, "North Carolina": 1}}))

    respx.post(f"{PENTAIR}/widgets").mock(side_effect=widgets)
    detail = respx.get(f"{PENTAIR}/us/en/job/R20002").mock(
        return_value=httpx.Response(200, text=ph_detail_page("<p>Build <b>pipelines</b> in Python.</p>")))

    adapter = PhenomAdapter()
    async with httpx.AsyncClient() as client:
        jobs = await adapter.fetch(client, "careers.pentair.com/us/en", known_ids={"R20001"})

    assert len(bodies) == 2 and detail.call_count == 1
    by_id = {j.external_id: j for j in jobs}
    assert set(by_id) == {"R20001", "R20002"}
    new = by_id["R20002"]
    assert new.title == "Data Engineer" and new.url == f"{PENTAIR}/us/en/job/R20002"
    assert new.locations == ["Charlotte, North Carolina, United States"]
    assert "Build pipelines in Python." in new.description
    assert new.posted_at == datetime(2026, 9, 28, tzinfo=timezone.utc)
    assert by_id["R20001"].description == ""  # known: no detail request
    assert adapter.last_fetch_complete("careers.pentair.com/us/en")


@pytest.mark.asyncio
@respx.mock
async def test_phenom_no_us_jobs_returns_nothing():
    respx.post("https://careers.xoriant.com/widgets").mock(return_value=httpx.Response(
        200, json=ph_response([ph_job(0, country="India")], 460, {"country": {"India": 460}})))
    async with httpx.AsyncClient() as client:
        assert await PhenomAdapter().fetch(client, "careers.xoriant.com/in/en") == []


@pytest.mark.asyncio
@respx.mock
async def test_phenom_over_cap_pages_per_state(monkeypatch):
    monkeypatch.setattr(phenom, "RESULT_CAP", 3)
    monkeypatch.setattr(phenom, "PAGE_SIZE", 2)
    pool = {"Ohio": [ph_job(i, state="Ohio") for i in range(3)],
            "Texas": [ph_job(10 + i, state="Texas") for i in range(2)]}

    def widgets(request):
        body = json.loads(request.content)
        sel, start, size = body["selected_fields"], body["from"], body["size"]
        if not sel:
            return httpx.Response(200, json=ph_response([], 6, {"country": {"United States": 6}}))
        if "state" not in sel:  # first US page: 6 jobs, over the cap of 3
            return httpx.Response(200, json=ph_response(pool["Ohio"][:size], 6, {"state": {
                "Ohio": 3, "Texas": 2, "Unspecified": 1}}))
        rows = pool.get(sel["state"][0], [])
        return httpx.Response(200, json=ph_response(rows[start:start + size], len(rows)))

    respx.post(f"{PENTAIR}/widgets").mock(side_effect=widgets)
    respx.get(url__regex=rf"{PENTAIR}/us/en/job/.*").mock(return_value=httpx.Response(200, text=ph_detail_page("x")))
    adapter = PhenomAdapter()
    async with httpx.AsyncClient() as client:
        jobs = await adapter.fetch(client, "careers.pentair.com/us/en", known_ids=set())
    assert len(jobs) == 5
    assert not adapter.last_fetch_complete("careers.pentair.com/us/en")  # the "Unspecified" job was out of reach


@pytest.mark.asyncio
@respx.mock
async def test_phenom_first_scan_limits_detail_requests(monkeypatch):
    monkeypatch.setattr(phenom, "FIRST_SCAN_DETAILS", 1)

    def widgets(request):
        body = json.loads(request.content)
        if not body["selected_fields"]:
            return httpx.Response(200, json=ph_response([], 2, {"country": {"United States": 2}}))
        return httpx.Response(200, json=ph_response(
            [ph_job(1, country="United States", postedDate="2026-11-01T00:00:00.000+0000"),
             ph_job(2, country="United States", postedDate="2026-11-08T00:00:00.000+0000")], 2))

    respx.post(f"{PENTAIR}/widgets").mock(side_effect=widgets)
    detail = respx.get(url__regex=rf"{PENTAIR}/us/en/job/.*").mock(
        return_value=httpx.Response(200, text=ph_detail_page("<p>" + "word " * 40 + "</p>")))
    async with httpx.AsyncClient() as client:
        jobs = await PhenomAdapter().fetch(client, "careers.pentair.com", first_scan=True)
    assert detail.call_count == 1 and str(detail.calls[0].request.url).endswith("/R20002")  # newest first
    assert sum(bool(j.description) for j in jobs) == 1


# --- Jibe ---------------------------------------------------------------------------------------

ICE = "https://careers.ice.com"


def jb_job(slug: str, *, posted="2026-11-10T15:12:00+0000", country_code="US", **kw) -> dict:
    data = {
        "slug": slug, "req_id": slug, "language": "en-us", "title": "Growth Marketing Associate",
        "description": "<p>ICE supports the <b>mortgage</b> finance industry.</p>",
        "location_name": "OFF USA: FL Jacksonville (4800 Deer Lake Drive)", "city": "Jacksonville",
        "state": "Florida", "country": "United States" if country_code == "US" else "Ireland",
        "country_code": country_code, "full_location": "Jacksonville, Florida", "location_type": "LAT_LNG",
        "posted_date": posted, "create_date": posted, "category": ["Marketing"], "internal": False, "external": False,
        "apply_url": f"https://careers-ice.icims.com/jobs/{slug}/login", "multipleLocations": False, "tags3": ["In-Office"],
    }
    data.update(kw)
    return {"data": data}


def test_jibe_slug_detect_and_page():
    assert jibe.parse_slug("careers.heb.com/careers-home") == ("careers.heb.com", "/careers-home")
    assert jibe.parse_slug("https://careers.ice.com/") == ("careers.ice.com", "")
    assert jibe.detect("https://careers.heb.com/careers-home/jobs/253772?lang=en-us") == "careers.heb.com/careers-home"
    assert jibe.detect("https://acme.jibeapply.com/jobs/123") == "acme.jibeapply.com"
    assert jibe.detect("https://acme.jibeapply.com/careers/jobs") == "acme.jibeapply.com/careers"
    assert jibe.detect("https://boards.greenhouse.io/acme") is None
    page = '<base href="/careers-home"><script src="https://assets.jibecdn.com/prod/x.js"></script>'
    assert jibe.slug_from_page("https://careers.heb.com/careers-home", page) == "careers.heb.com/careers-home"
    assert jibe.slug_from_page("https://careers.footlocker.com/", '<base href=""><script>window._jibe = {}</script>') \
        == "careers.footlocker.com"
    assert jibe.slug_from_page("https://example.com/", "<html></html>") is None


@pytest.mark.asyncio
@respx.mock
async def test_jibe_full_fetch_pages_and_filters_us(monkeypatch):
    monkeypatch.setattr(jibe, "PAGE_SIZE", 2)
    pages = {1: [jb_job("14050"), jb_job("14049", city="Atlanta", state="Georgia", title="Data Analyst",
                                     multipleLocations=True, tags3=["Remote"], additional_locations=[
                                         {"city": "New York", "state": "New York", "country_code": "US"}])],
             2: [jb_job("17637", country_code="IE"), jb_job("14001", posted="2026-10-01T09:00:00+0000")]}

    def api(request):
        params = request.url.params
        assert params["limit"] == "2" and params["sortBy"] == "posted_date" and params["country"] == "United States"
        return httpx.Response(200, json={"jobs": pages.get(int(params["page"]), []), "totalCount": 4, "count": 4})

    route = respx.get(f"{ICE}/api/jobs").mock(side_effect=api)
    adapter = JibeAdapter()
    async with httpx.AsyncClient() as client:
        jobs = await adapter.fetch(client, "careers.ice.com", full=True)
    assert route.call_count == 2
    by_id = {j.external_id: j for j in jobs}
    assert set(by_id) == {"14050", "14049", "14001"}  # the Irish job is dropped
    j = by_id["14050"]
    assert j.url == f"{ICE}/jobs/14050?lang=en-us" and j.locations == ["Jacksonville, Florida, US"]
    assert j.description == "ICE supports the mortgage finance industry." and j.department == "Marketing"
    assert j.posted_at == datetime(2026, 11, 10, 15, 12, tzinfo=timezone.utc)
    assert by_id["14049"].title == "Data Analyst" and by_id["14049"].remote and not j.remote
    assert by_id["14049"].locations == ["Atlanta, Georgia, US", "New York, New York, US"]
    assert adapter.last_fetch_complete("careers.ice.com")


@pytest.mark.asyncio
@respx.mock
async def test_jibe_partial_scan_stops_when_nothing_new(monkeypatch):
    monkeypatch.setattr(jibe, "PAGE_SIZE", 1)
    route = respx.get("https://careers.heb.com/api/jobs").mock(side_effect=lambda r: httpx.Response(200, json={
        "jobs": [jb_job(f"{300 - int(r.url.params['page'])}")], "totalCount": 50}))
    async with httpx.AsyncClient() as client:
        jobs = await JibeAdapter().fetch(client, "careers.heb.com/careers-home", full=False, known_ids={"298"})
    assert route.call_count == 2  # page 1 new (299), page 2 known (298): stop
    assert [j.external_id for j in jobs] == ["299", "298"]
    assert jobs[0].url == "https://careers.heb.com/careers-home/jobs/299?lang=en-us"


@pytest.mark.asyncio
@respx.mock
async def test_jibe_full_scan_hitting_page_limit_is_incomplete(monkeypatch):
    monkeypatch.setattr(jibe, "PAGE_SIZE", 1)
    monkeypatch.setattr(jibe, "MAX_PAGES", 2)
    respx.get(f"{ICE}/api/jobs").mock(side_effect=lambda r: httpx.Response(200, json={
        "jobs": [jb_job(r.url.params["page"])], "totalCount": 10}))
    adapter = JibeAdapter()
    async with httpx.AsyncClient() as client:
        jobs = await adapter.fetch(client, "careers.ice.com")
    assert len(jobs) == 2 and not adapter.last_fetch_complete("careers.ice.com")
