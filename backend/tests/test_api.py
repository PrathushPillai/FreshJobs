from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from tests.conftest import sign_up

from app.main import app
from app.models import Company
from app.pipeline.poller import process_company
from app.prefs import get_prefs
from app.sources import NormalizedJob


def nj(ext_id, title, location="New York, NY", description="", company_name=""):
    return NormalizedJob(external_id=ext_id, title=title, url=f"https://x/{ext_id}", locations=[location],
                         description=description, company_name=company_name)


@pytest.fixture()


def client(session, now):
    acme = Company(name="Acme", ats="greenhouse", slug="acme")
    session.add(acme)
    session.flush()
    prefs = get_prefs(session)
    process_company(session, acme, [], prefs, now - timedelta(hours=1))  # baseline poll
    process_company(session, acme, [
        nj("1", "Data Analyst", "Austin, TX", "Requires 1+ years of SQL experience."),
        nj("2", "Business Intelligence Developer", "Toronto, ON"),
        nj("3", "Senior Software Engineer", "Seattle, WA", "8+ years of professional experience."),
        nj("4", "Registered Nurse", "London, UK"),
        nj("5", "Data Engineer", "Remote", "Must be a US citizen."),
    ], prefs, now)
    session.commit()
    with TestClient(app) as c:
        sign_up(c)
        yield c


def titles(resp):
    assert resp.status_code == 200, resp.text
    return sorted(j["title"] for j in resp.json()["items"])


def test_any_role_searchable(client):
    assert titles(client.get("/api/jobs", params={"q": "nurse"})) == ["Registered Nurse"]


def test_related_role_search(client):
    resp = client.get("/api/jobs", params={"q": "data analyst"})
    assert titles(resp) == ["Business Intelligence Developer", "Data Analyst"]
    assert "business intelligence" in resp.json()["related_terms"]
    assert titles(client.get("/api/jobs", params={"q": "data analyst", "related": False})) == ["Data Analyst"]


def test_no_sponsor_hidden_by_default(client):
    assert "Data Engineer" not in titles(client.get("/api/jobs"))
    assert "Data Engineer" in titles(client.get("/api/jobs", params={"hide_no_sponsor": False}))


def test_location_filters(client):
    assert titles(client.get("/api/jobs", params={"countries": "Canada"})) == ["Business Intelligence Developer"]
    assert titles(client.get("/api/jobs", params={"countries": "United States", "states": "TX"})) == ["Data Analyst"]
    assert titles(client.get("/api/jobs", params={"countries": "United States", "cities": "Seattle"})) == ["Senior Software Engineer"]
    assert titles(client.get("/api/jobs", params=[("countries", "Canada"), ("countries", "United Kingdom")])) == [
        "Business Intelligence Developer", "Registered Nurse"]


def test_location_facets(client):
    facets = client.get("/api/facets/locations", params={"country": "United States"}).json()
    assert {c["name"] for c in facets["countries"]} >= {"United States", "Canada", "United Kingdom"}
    assert {s["name"] for s in facets["states"]} == {"TX", "WA"}


def test_experience_filters(client):
    assert titles(client.get("/api/jobs", params={"exp_levels": "senior"})) == ["Senior Software Engineer"]
    assert titles(client.get("/api/jobs", params={"exp_levels": "entry"})) == ["Data Analyst"]
    jobs = client.get("/api/jobs", params={"max_years": 3}).json()["items"]
    assert "Senior Software Engineer" not in [j["title"] for j in jobs]
    analyst = next(j for j in jobs if j["title"] == "Data Analyst")
    assert analyst["min_years"] == 1 and analyst["exp_level"] == "entry"


def test_tracker_flow(client):
    job = client.get("/api/jobs", params={"q": "nurse"}).json()["items"][0]
    created = client.post("/api/applications", json={"job_id": job["id"], "status": "applied"})
    assert created.status_code == 201
    app_id = created.json()["id"]
    assert created.json()["applied_at"] is not None
    assert client.post("/api/applications", json={"job_id": job["id"]}).status_code == 409

    tracked = client.get("/api/jobs", params={"q": "nurse"}).json()["items"][0]
    assert tracked["application"] == {"id": app_id, "status": "applied"}
    assert titles(client.get("/api/jobs", params={"tracked": "untracked", "q": "nurse"})) == []

    patched = client.patch(f"/api/applications/{app_id}", json={"status": "interviewing", "notes": "Phone screen Fri"})
    assert patched.json()["status"] == "interviewing" and patched.json()["notes"] == "Phone screen Fri"
    assert client.patch(f"/api/applications/{app_id}", json={"status": "bogus"}).status_code == 400

    manual = client.post("/api/applications", json={"company": "Elsewhere", "title": "Analyst", "status": "saved"})
    assert manual.status_code == 201 and manual.json()["applied_at"] is None
    assert len(client.get("/api/applications").json()) == 2
    assert client.delete(f"/api/applications/{app_id}").status_code == 204
    assert len(client.get("/api/applications").json()) == 1


def test_recruiter_links_without_hunter(client, monkeypatch):
    monkeypatch.setattr("app.recruiters.settings.hunter_api_key", "")
    job = client.get("/api/jobs", params={"q": "data analyst", "related": False}).json()["items"][0]
    data = client.get(f"/api/jobs/{job['id']}/recruiters").json()
    assert data["hunter_configured"] is False and data["hunter"] is None
    assert any("linkedin.com/search/results/people" in link["url"] and "Acme" in link["url"] for link in data["links"])
    looked_up = client.get(f"/api/jobs/{job['id']}/recruiters", params={"lookup": True}).json()
    assert "HUNTER_API_KEY" in looked_up["hunter"]["error"]


def test_cross_source_duplicate_flagged(session, now):
    prefs = get_prefs(session)
    acme = Company(name="Acme", ats="greenhouse", slug="acme")
    agg = Company(name="Adzuna", ats="themuse", slug="us")
    session.add_all([acme, agg])
    session.flush()
    for c in (acme, agg):
        process_company(session, c, [], prefs, now - timedelta(hours=1))
    [original] = process_company(session, acme, [nj("1", "Data Analyst")], prefs, now)
    [dup] = process_company(session, agg, [nj("z9", "Data Analyst", company_name="Acme, Inc.")], prefs, now,
                            complete=False)
    assert dup.is_repost and dup.repost_of_id == original.id
    assert dup.events[0].kind == "duplicate"


def test_add_company_from_careers_url(client):
    r = client.post("/api/companies/from-url", json={"url": "https://jobs.lever.co/acme-labs/abc-123"})
    assert r.status_code == 200, r.text
    assert r.json()["added"] is True and r.json()["company"]["ats"] == "lever" and r.json()["company"]["slug"] == "acme-labs"
    again = client.post("/api/companies/from-url", json={"url": "jobs.lever.co/acme-labs"})
    assert again.json()["added"] is False


def test_search_ranks_typed_words_before_related_roles(client):
    items = client.get("/api/jobs", params={"q": "analyst data"}).json()["items"]
    assert [j["title"] for j in items] == ["Data Analyst", "Business Intelligence Developer"]


def test_search_related_roles_can_be_switched_off(client):
    params = {"q": "data analyst"}
    assert titles(client.get("/api/jobs", params=params)) == ["Business Intelligence Developer", "Data Analyst"]
    # "Exact title" (related=false) and switching one related role off both drop the related match.
    assert titles(client.get("/api/jobs", params={**params, "related": "false"})) == ["Data Analyst"]
    assert titles(client.get("/api/jobs", params={**params, "skip": ["business intelligence", "bi developer"]})) == ["Data Analyst"]


def test_data_engineer_relates_to_data_platform_roles():
    from app.pipeline.search import distinct_roles, expand_query

    related = distinct_roles(expand_query("data engineer")[1:], ["data engineer"])
    assert "analytics platform" in related and "data platform" in related
    assert "marketing analyst" not in related


def test_posted_within_filter(client, session, now):
    from app.models import Job

    # The filter compares with the real clock, while the fixture's `now` is a fixed date: date the jobs from today.
    today = datetime.now(timezone.utc)
    old = session.query(Job).filter_by(title="Data Analyst").one()
    old.posted_at = old.listed_at = today - timedelta(days=10)
    session.query(Job).filter_by(title="Business Intelligence Developer").one().listed_at = today - timedelta(hours=1)
    session.commit()
    recent = titles(client.get("/api/jobs", params={"posted_within_hours": 72}))
    assert "Data Analyst" not in recent and "Business Intelligence Developer" in recent
    assert "Data Analyst" in titles(client.get("/api/jobs", params={"posted_within_hours": 24 * 30}))


def test_verify_closes_a_job_whose_apply_page_is_gone(client):
    import httpx
    import respx

    job = client.get("/api/jobs", params={"q": "nurse"}).json()["items"][0]
    with respx.mock:
        respx.get(job["url"]).mock(return_value=httpx.Response(404))
        resp = client.post(f"/api/jobs/{job['id']}/verify").json()
    assert resp["open"] is False and resp["closed_at"]
    assert job["id"] not in [j["id"] for j in client.get("/api/jobs").json()["items"]]


def test_job_list_reports_when_its_count_is_capped(client, monkeypatch):
    import app.main as main

    monkeypatch.setattr(main, "COUNT_CAP", 1)
    page = client.get("/api/jobs").json()
    assert page["total"] == 1 and page["total_capped"] is True


def test_related_roles_are_suggested_and_only_searched_when_picked(client):
    page = client.get("/api/jobs", params={"q": "nurse", "related": "false"}).json()
    plain = {j["id"] for j in page["items"]}
    assert page["related_terms"] is not None  # suggestions are always returned
    picked = client.get("/api/jobs", params={"q": "nurse", "related": "false", "also": ["data analyst"]}).json()
    assert plain <= {j["id"] for j in picked["items"]}
    assert client.get("/api/roles/suggestions", params={"roles": ["data analyst"]}).status_code == 200


def test_repeat_kinds_labelled_and_filterable(client, session, now):
    acme = session.query(Company).filter_by(slug="acme").one()
    agg = Company(name="Adzuna: data", ats="adzuna", slug="data")
    session.add(agg)
    session.flush()
    prefs = get_prefs(session)
    old = nj("6", "Product Manager", "Austin, TX")
    old.posted_at = now - timedelta(days=40)
    process_company(session, acme, [nj("1", "Data Analyst", "Austin, TX", "Requires 1+ years of SQL experience."),
                                    nj("2", "Business Intelligence Developer", "Toronto, ON"),
                                    nj("3", "Senior Software Engineer", "Seattle, WA", "8+ years of professional experience."),
                                    nj("4", "Registered Nurse", "London, UK"),
                                    nj("5", "Data Engineer", "Remote", "Must be a US citizen."), old], prefs, now)
    process_company(session, agg, [], prefs, now)  # the aggregator's baseline poll
    process_company(session, agg, [nj("a1", "Data Analyst", "Austin, TX", "Requires 1+ years of SQL experience.", "Acme")],
                    prefs, now + timedelta(minutes=5))
    session.commit()

    assert "Product Manager" not in titles(client.get("/api/jobs"))  # repeats stay hidden by default
    items = client.get("/api/jobs", params={"show_reposts": True}).json()["items"]
    kinds = {(j["title"], j["source"]): j["repost_kind"] for j in items}
    assert kinds[("Product Manager", "greenhouse")] == "resurfaced"
    assert kinds[("Data Analyst", "adzuna")] == "duplicate"
    assert kinds[("Data Analyst", "greenhouse")] is None

    only = client.get("/api/jobs", params={"show_reposts": True, "repeat_kinds": ["resurfaced"]}).json()["items"]
    assert {(j["title"], j["repost_kind"]) for j in only if j["repost_kind"]} == {("Product Manager", "resurfaced")}
    # Some kinds ticked: the page comes from two index walks (normal jobs, ticked repeats) merged in sort order.
    # It must hold the same jobs, in the same order, as showing every kind and dropping the unticked ones.
    for sort in ["newest", "posted", "company", "sponsor"]:
        every = client.get("/api/jobs", params={"show_reposts": True, "sort": sort, "hide_no_sponsor": False}).json()["items"]
        expected = [j["id"] for j in every if j["repost_kind"] in (None, "resurfaced")]
        got = client.get("/api/jobs", params={"show_reposts": True, "repeat_kinds": ["resurfaced"], "sort": sort,
                                              "hide_no_sponsor": False}).json()["items"]
        if sort in ("newest", "posted"):  # orders with an id tie-break: exactly the same
            assert [j["id"] for j in got] == expected, sort
        else:  # company / H-1B order has no tie-break (ties come in index order): same jobs, valid order
            assert sorted(j["id"] for j in got) == sorted(expected), sort
            key = "company" if sort == "company" else "h1b_filings"
            values = [j[key] for j in got]
            assert values == (sorted(values) if sort == "company" else sorted(values, key=lambda v: -(v or -1))), sort
        page2 = client.get("/api/jobs", params={"show_reposts": True, "repeat_kinds": ["resurfaced"], "sort": sort,
                                                "hide_no_sponsor": False, "limit": 2, "offset": 2}).json()["items"]
        assert [j["id"] for j in page2] == [j["id"] for j in got][2:4], sort  # pages follow the same order

    dup = next(j for j in items if j["repost_kind"] == "duplicate")
    detail = client.get(f"/api/jobs/{dup['id']}").json()
    assert detail["repost_of"]["company"] == "Acme" and detail["repost_of"]["closed_at"] is None


def test_student_visa_and_everify_signals(client, session, now):
    acme = session.query(Company).filter_by(slug="acme").one()
    process_company(session, acme, [
        nj("1", "Data Analyst", "Austin, TX", "Requires 1+ years of SQL experience."),
        nj("2", "Business Intelligence Developer", "Toronto, ON"),
        nj("3", "Senior Software Engineer", "Seattle, WA", "8+ years of professional experience."),
        nj("4", "Registered Nurse", "London, UK"),
        nj("5", "Data Engineer", "Remote", "Must be a US citizen."),
        nj("7", "Analytics Engineer", "Boston, MA",
           "We're happy to sponsor international candidates (e.g., STEM OPT, OPT, H1B). We participate in E-Verify."),
        nj("8", "Data Scientist", "Boston, MA", "This role is not eligible for visa sponsorship (H1B, OPT/CPT, TN etc)."),
    ], get_prefs(session), now + timedelta(minutes=5))
    session.commit()
    items = {j["title"]: j for j in client.get("/api/jobs", params={"hide_no_sponsor": False}).json()["items"]}
    assert items["Analytics Engineer"]["student_visa"] == "welcome" and items["Analytics Engineer"]["everify_text"]
    assert items["Data Scientist"]["student_visa"] == "excluded" and items["Data Scientist"]["no_sponsor"]
    assert "Data Scientist" not in titles(client.get("/api/jobs"))  # ruling out OPT/CPT hides it like "no sponsorship"
    detail = client.get(f"/api/jobs/{items['Analytics Engineer']['id']}").json()
    assert detail["h1b_roles"] is None  # no H-1B data imported in this test


def test_fresh_counts_in_one_request(client, session):
    from app.models import Job

    today = datetime.now(timezone.utc)
    for title, hours in (("Data Analyst", 0.5), ("Business Intelligence Developer", 3), ("Registered Nurse", 30)):
        session.query(Job).filter_by(title=title).one().listed_at = today - timedelta(hours=hours)
    session.query(Job).filter_by(title="Senior Software Engineer").one().listed_at = today - timedelta(hours=12)
    session.commit()
    page = client.get("/api/jobs", params={"fresh_counts": True, "countries": ["United States", "Canada", "United Kingdom"]}).json()
    assert page["fresh"] == {"1": 1, "6": 2, "24": 3} and page["items"] == []


def test_job_lists_are_cached_until_something_changes(client, session):
    first = client.get("/api/jobs", params={"q": "analyst"})
    assert first.headers["X-Cache"] == "miss"
    assert client.get("/api/jobs", params={"q": "analyst"}).headers["X-Cache"] == "hit"
    job_id = first.json()["items"][0]["id"]
    client.post("/api/applications", json={"job_id": job_id, "status": "applied"})  # any change clears the cache
    again = client.get("/api/jobs", params={"q": "analyst"})
    assert again.headers["X-Cache"] == "miss" and again.json()["items"][0]["application"]["status"] == "applied"


def test_adding_or_removing_a_role_updates_for_you_at_once(client):
    for_you = lambda: titles(client.get("/api/jobs", params={"alert_roles_only": True, "hide_no_sponsor": False}))  # noqa: E731
    prefs = client.get("/api/prefs").json()
    assert "Registered Nurse" not in for_you() and "Data Analyst" in for_you()
    client.put("/api/prefs", json={**prefs, "include_keywords": [*prefs["include_keywords"], "nurse"]})
    assert "Registered Nurse" in for_you()  # no waiting for a background recheck
    client.put("/api/prefs", json={**prefs, "include_keywords": [k for k in prefs["include_keywords"] if k != "data analyst"]})
    assert "Registered Nurse" not in for_you() and "Data Analyst" not in for_you()


def test_ranked_search_pages_in_the_same_order_as_one_page(client):
    params = {"q": "data analyst", "countries": ["United States", "Canada", "United Kingdom"], "hide_no_sponsor": False}
    whole = [j["id"] for j in client.get("/api/jobs", params={**params, "limit": 50}).json()["items"]]
    paged = [j["id"] for off in range(len(whole)) for j in client.get("/api/jobs", params={**params, "limit": 1, "offset": off}).json()["items"]]
    assert len(whole) >= 2 and paged == whole
    titles_in_order = [j["title"] for j in client.get("/api/jobs", params={**params, "limit": 50}).json()["items"]]
    assert titles_in_order[0] == "Data Analyst"  # the exact title ranks before related roles


def test_symbols_only_search_returns_nothing_quickly(client):
    page = client.get("/api/jobs", params={"q": "*&^%$"}).json()
    assert page["total"] == 0 and page["items"] == []


def test_tracked_only_filter(client):
    job_id = client.get("/api/jobs", params={"q": "nurse", "countries": "United Kingdom"}).json()["items"][0]["id"]
    client.post("/api/applications", json={"job_id": job_id, "status": "saved"})
    tracked = client.get("/api/jobs", params={"tracked": "tracked", "countries": "United Kingdom"}).json()["items"]
    assert [j["id"] for j in tracked] == [job_id]


def test_companies_list_busiest_first_and_searchable(client, session):
    other = Company(name="Zqglobex", ats="lever", slug="zqglobex")
    session.add(other)
    session.commit()
    names = [c["name"] for c in client.get("/api/companies", params={"limit": 50_000}).json()]
    assert names[0] == "Acme" and "Zqglobex" in names  # Acme has the open jobs; the YAML sources have none

    assert [c["name"] for c in client.get("/api/companies", params={"q": "zqglob"}).json()] == ["Zqglobex"]
    assert "Zqglobex" in [c["name"] for c in client.get("/api/companies", params={"q": "lever", "limit": 50_000}).json()]
    assert len(client.get("/api/companies", params={"limit": 1}).json()) == 1


def test_a_job_found_closed_leaves_cached_feed_pages_at_once(client, monkeypatch):
    """Opening a job checks its apply page; when that closes it, the next feed request mustn't show it from the
    60-second page cache (it did, until the cache was cleared on close)."""
    from app.pipeline import liveness

    async def gone(client_, url):
        return False, "the posting is gone (404)"

    params = {"hide_no_sponsor": False}
    before = client.get("/api/jobs", params=params).json()["items"]
    assert client.get("/api/jobs", params=params).headers["X-Cache"] == "hit"  # the page is cached
    target = before[0]
    monkeypatch.setattr(liveness, "check", gone)
    assert client.post(f"/api/jobs/{target['id']}/verify").json()["closed_at"] is not None
    after = client.get("/api/jobs", params=params)
    assert target["id"] not in [j["id"] for j in after.json()["items"]]


def test_sponsor_likely_verdict_and_filter(client, session):
    from app.models import Job

    jobs = {j.title: j for j in session.query(Job)}
    jobs["Data Analyst"].h1b_filings = 250  # likely: 10+ filings
    jobs["Senior Software Engineer"].h1b_filings = 3  # possible: a few filings
    jobs["Business Intelligence Developer"].everify = "yes"  # possible: E-Verify only (STEM OPT)
    jobs["Registered Nurse"].sponsors_text = True  # likely: the posting says so
    jobs["Data Engineer"].h1b_filings = 900  # ruled out by its text, whatever the filings
    session.commit()
    from app import main

    main._jobs_cache.clear()
    all_jobs = client.get("/api/jobs", params={"hide_no_sponsor": False}).json()["items"]
    assert {j["title"]: j["sponsor_likely"] for j in all_jobs} == {
        "Data Analyst": "likely", "Senior Software Engineer": "possible",
        "Business Intelligence Developer": "possible", "Registered Nurse": "likely", "Data Engineer": "ruled_out"}
    likely = {"hide_no_sponsor": False, "sponsor_likely": "likely"}
    assert titles(client.get("/api/jobs", params=likely)) == ["Data Analyst", "Registered Nurse"]
    assert titles(client.get("/api/jobs", params={**likely, "sponsor_likely": "possible"})) == [
        "Business Intelligence Developer", "Data Analyst", "Registered Nurse", "Senior Software Engineer"]


def test_startup_survives_a_locked_database(monkeypatch):
    """A locked database during the startup backfill used to end the startup; now it's retried later."""
    from fastapi.testclient import TestClient
    from sqlalchemy.exc import OperationalError

    from app import main
    started = []

    def locked():
        raise OperationalError("UPDATE jobs ...", {}, Exception("database is locked"))

    monkeypatch.setattr(main, "backfill", locked)
    monkeypatch.setattr(main, "_backfill_until_done", lambda: started.append(True))
    with TestClient(main.app) as client:
        assert client.get("/api/status").status_code == 200
    assert started == [True]


def test_status_counts_are_served_at_once_and_recounted_in_the_background(session, monkeypatch):
    import threading
    import time as _time

    from app import main

    main._status_counts.clear()
    first = main._counts(session)  # nothing counted yet: this one waits
    assert "open_jobs" in first
    main._status_counts["at"] = _time.monotonic() - 120  # stale
    started = threading.Event()
    real = main._recount
    monkeypatch.setattr(main, "_recount", lambda s: (started.set(), _time.sleep(0.3), real(s)))
    t = _time.monotonic()
    again = main._counts(session)
    assert _time.monotonic() - t < 0.1 and again is main._status_counts  # served stale, at once
    assert started.wait(2)  # and recounted in the background
    for _ in range(100):
        if _time.monotonic() - main._status_counts["at"] < 60:
            break
        _time.sleep(0.05)
    assert _time.monotonic() - main._status_counts["at"] < 60
    main._status_counts.clear()


def test_h1b_sponsors_filter_in_every_sort(client, session):
    from app.models import Job

    for job in session.query(Job):
        job.h1b_filings = 25 if job.title in ("Data Analyst", "Senior Software Engineer") else 0
    session.commit()
    for sort in ("newest", "posted", "company", "sponsor"):
        got = titles(client.get("/api/jobs", params={"sponsors_only": True, "sort": sort, "countries": []}))
        assert got == ["Data Analyst", "Senior Software Engineer"], sort


def test_h1b_sponsors_filter_does_not_start_from_the_sponsor_index(session):
    # As a plain range, SQLite read every sponsored open job through ix_jobs_by_sponsor and sorted them by
    # date (5-32 s on 1.8M jobs). Written as +jobs.h1b_filings it walks the date index for one page.
    from sqlalchemy import text

    # The statement list_jobs sends for "All jobs, newest found, H-1B sponsors only" (traced 2026-11-11).
    sql = ("SELECT jobs.id FROM jobs WHERE jobs.closed_at IS NULL AND jobs.is_repost IS 0 AND +jobs.h1b_filings > 0 "
           "ORDER BY jobs.first_seen_at DESC, jobs.id DESC LIMIT 50")
    plan = " ".join(str(r[-1]) for r in session.execute(text("EXPLAIN QUERY PLAN " + sql)))
    assert "ix_jobs_by_found_v2" in plan and "TEMP B-TREE FOR ORDER BY" not in plan, plan
