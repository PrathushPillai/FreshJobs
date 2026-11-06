from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

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

    dup = next(j for j in items if j["repost_kind"] == "duplicate")
    detail = client.get(f"/api/jobs/{dup['id']}").json()
    assert detail["repost_of"]["company"] == "Acme" and detail["repost_of"]["closed_at"] is None
