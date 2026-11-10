"""Portal-wide regression check against the running backend and frontend, on the real database.

    .venv/Scripts/python.exe scripts/check_portal.py                 # backend :8000, frontend :5173
    .venv/Scripts/python.exe scripts/check_portal.py --no-frontend

Read-only: besides logging in, GET requests only, so it never changes jobs, the tracker, prefs or the resume.
It logs in as a dedicated check account (an ordinary user, portal-check@freshjobs.local), signed up on the
first run with a random password kept next to the database (check_account.json), never in the repo. Set
FRESHJOBS_CHECK_EMAIL and FRESHJOBS_CHECK_PASSWORD to use another account. It checks that every
page the UI uses answers, and that the answers are *right*: each sort is in order, each filter only returns
jobs that pass it, search ranks the typed title first, pages don't overlap, counts add up. The unit tests
(pytest) cover logic on small made-up data; this covers the real 1.6M-job database, where slow queries and odd
rows show up. Run both before calling a change done.

A request slower than SLOW_SECONDS is reported as SLOW (not a failure: the first read after a restart is cold).
Exit code 1 if anything failed.
"""

import argparse
import json
import os
import random
import secrets
import sys
import time
from datetime import datetime, timedelta, timezone

import httpx

SLOW_SECONDS = 8.0
RESULTS: list[tuple[str, str, float, str]] = []  # (status, name, seconds, detail)


class Check:
    def __init__(self, client: httpx.Client):
        self.c = client

    def get(self, path: str, params: dict | None = None, cache: bool = False) -> tuple[httpx.Response, float]:
        params = dict(params or {})
        if not cache:
            params["_check"] = random.randint(0, 10**9)  # an unknown parameter: skips the 60 s response cache
        t = time.perf_counter()
        r = self.c.get(path, params=params)
        return r, time.perf_counter() - t

    def run(self, name: str, fn) -> None:
        t = time.perf_counter()
        try:
            detail = fn() or ""
            secs = time.perf_counter() - t
            RESULTS.append(("SLOW" if secs > SLOW_SECONDS else "ok", name, secs, detail))
        except AssertionError as e:
            RESULTS.append(("FAIL", name, time.perf_counter() - t, str(e) or "assertion failed"))
        except Exception as e:  # noqa: BLE001 - report every kind of failure
            RESULTS.append(("FAIL", name, time.perf_counter() - t, f"{type(e).__name__}: {e}"))


def _dt(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def _non_increasing(values: list, label: str) -> None:
    present = [v for v in values if v is not None]
    assert present == sorted(present, reverse=True), f"{label} not in descending order: {present[:6]}"
    first_none = next((i for i, v in enumerate(values) if v is None), len(values))
    assert all(v is None for v in values[first_none:]), f"{label}: empty values not last"


US = {"countries": "United States"}
BASE = {**US, "limit": 50, "hide_no_sponsor": "false"}


def checks(ck: Check, frontend: str | None) -> None:
    now = datetime.now(timezone.utc)
    state: dict = {}

    def jobs(params: dict, expect_items: bool = True) -> list[dict]:
        r, _ = ck.get("/api/jobs", params)
        assert r.status_code == 200, f"HTTP {r.status_code}: {r.text[:200]}"
        body = r.json()
        items = body["items"]
        assert len(items) <= int(params.get("limit", 50)), "more items than the limit"
        if expect_items:
            assert items, f"no jobs for {params}"
        return items

    # --- basics -----------------------------------------------------------------------------------------
    def login_required():
        r = httpx.get(str(ck.c.base_url) + "/api/jobs", timeout=60)  # no cookie
        assert r.status_code == 401, f"job list answered HTTP {r.status_code} without a login"
    ck.run("login required", login_required)

    def status():
        r, _ = ck.get("/api/status")
        assert r.status_code == 200, f"HTTP {r.status_code}"
        s = r.json()
        assert s["open_jobs"] > 100_000, f"only {s['open_jobs']} open jobs"
        state["status"] = s
        return f"{s['open_jobs']:,} open jobs, {s['new_last_24h']:,} new in 24 h, polling={s['polling']}"
    ck.run("status", status)

    def profile():
        r, _ = ck.get("/api/profile")
        assert r.status_code == 200
        p = r.json()
        state["resume"] = p["has_resume"]
        return f"resume={'yes' if p['has_resume'] else 'no'}" + (f", {p.get('scored', 0):,} jobs scored" if p["has_resume"] else "")
    ck.run("profile", profile)

    for path in ["/api/prefs", "/api/applications", "/api/roles/suggestions"]:
        def simple(path=path):
            r, _ = ck.get(path)
            assert r.status_code == 200, f"HTTP {r.status_code}"
        ck.run(path, simple)

    # --- the two feeds ----------------------------------------------------------------------------------
    def in_us(items: list[dict]) -> None:
        bad = [j["id"] for j in items if not (any(p["country"] == "United States" for p in j["places"])
                                                or (j["remote"] and not j["places"]))]
        assert not bad, f"jobs outside the US with the US filter: {bad[:5]}"

    def for_you():
        items = jobs({**BASE, "alert_roles_only": "true", "sort": "posted"})
        in_us(items)
        assert not any(j["is_repost"] for j in items), "reposts shown by default"
        state["job_id"] = items[0]["id"]
        return f"{len(items)} jobs"
    ck.run("For you (US, newest posted)", for_you)

    def all_jobs():
        items = jobs({**BASE, "sort": "newest"})
        in_us(items)
        state["unscored"] = [j["id"] for j in items if not j["match"]]
        return f"{len(items)} jobs"
    ck.run("All jobs (US, newest found)", all_jobs)

    def late_scores():
        # Lists return saved scores only; the Feed asks /api/matches for the rest of the page.
        if not state.get("resume"):
            return "no resume: skipped"
        ids = state.get("unscored") or []
        if not ids:
            return "every job on the page already scored"
        r, _ = ck.get("/api/matches", {"ids": ids})
        assert r.status_code == 200, f"HTTP {r.status_code}"
        got = r.json()
        assert set(got) == {str(i) for i in ids}, f"scores missing for {len(ids) - len(got)} jobs"
        return f"{len(ids)} jobs scored after the list"
    ck.run("scores fill in after a list", late_scores)

    # --- sorts ------------------------------------------------------------------------------------------
    def sort_posted():
        _non_increasing([_dt(j["listed_at"]) for j in jobs({**BASE, "sort": "posted"})], "listed_at")
    def sort_newest():
        _non_increasing([_dt(j["first_seen_at"]) for j in jobs({**BASE, "sort": "newest"})], "first_seen_at")
    def sort_company():
        names = [j["company"] for j in jobs({**BASE, "sort": "company"})]
        assert names == sorted(names), f"company names not A-Z: {names[:5]}"
    def sort_sponsor():
        _non_increasing([j["h1b_filings"] for j in jobs({**BASE, "sort": "sponsor"})], "h1b_filings")
    def sort_match():
        items = jobs({**BASE, "alert_roles_only": "true", "sort": "match"})
        if not state.get("resume"):
            return "no resume: skipped"
        scored = [j["match"]["score"] for j in items if j["match"]]
        assert scored, "no scores with a resume uploaded"
        _non_increasing(scored, "match score")
        return f"top score {scored[0]}"
    for name, fn in [("sort: most recently posted", sort_posted), ("sort: most recently found", sort_newest),
                     ("sort: company A-Z", sort_company), ("sort: most H-1B filings", sort_sponsor),
                     ("sort: best match", sort_match)]:
        ck.run(name, fn)

    # --- filters: every returned job must pass the filter -----------------------------------------------
    def f_remote():
        assert all(j["remote"] for j in jobs({**BASE, "remote_only": "true"})), "non-remote job with remote only"
    def f_posted_24h():
        items = jobs({**BASE, "sort": "posted", "posted_within_hours": 24})
        old = [j["id"] for j in items if not j["listed_at"] or _dt(j["listed_at"]) < now - timedelta(hours=24, minutes=5)]
        assert not old, f"posted over 24 h ago: {old[:5]}"
    def f_found_6h():
        items = jobs({**BASE, "since_hours": 6})
        old = [j["id"] for j in items if _dt(j["first_seen_at"]) < now - timedelta(hours=6, minutes=5)]
        assert not old, f"found over 6 h ago: {old[:5]}"
    def f_hide_no_sponsor():
        assert not any(j["no_sponsor"] for j in jobs({**US, "limit": 50, "hide_no_sponsor": "true"})), "no-sponsor job shown"
    def f_sponsors_only():
        assert all((j["h1b_filings"] or 0) > 0 for j in jobs({**BASE, "sponsors_only": "true"})), "job without H-1B filings"
    def f_everify():
        assert all(j["everify"] == "yes" or j["everify_text"] for j in jobs({**BASE, "everify_only": "true"})), "not E-Verify"
    def f_entry():
        items = jobs({**BASE, "exp_levels": ["entry", "unknown"]})
        bad = [j["exp_level"] for j in items if j["exp_level"] not in ("entry", "unknown", None)]
        assert not bad, f"levels other than entry: {bad[:5]}"
    def f_state():
        items = jobs({**BASE, "states": "CA"})
        assert all(any(p["state"] == "CA" for p in j["places"]) for j in items), "job outside CA"
    def f_city():
        items = jobs({**BASE, "states": "MA", "cities": "Boston"})
        assert all(any(p["city"] == "Boston" for p in j["places"]) for j in items), "job outside Boston"
    def f_reposts():
        # "Show repeated jobs" adds the ticked kinds to the normal jobs; with only Repost ticked, no duplicate or
        # resurfaced job may appear.
        items = jobs({**BASE, "show_reposts": "true", "repeat_kinds": "repost"}, expect_items=False)
        wrong = [j["repost_kind"] for j in items if j["is_repost"] and j["repost_kind"] not in ("repost", None)]
        assert not wrong, f"unticked repeat kinds shown: {wrong[:5]}"
        return f"{sum(j['is_repost'] for j in items)} reposts among {len(items)} jobs"
    for name, fn in [("filter: remote only", f_remote), ("filter: posted in 24 h", f_posted_24h),
                     ("filter: found in 6 h", f_found_6h), ("filter: hide no-sponsorship", f_hide_no_sponsor),
                     ("filter: H-1B sponsors only", f_sponsors_only), ("filter: E-Verify", f_everify),
                     ("filter: entry level", f_entry), ("filter: state CA", f_state), ("filter: city Boston", f_city),
                     ("filter: reposts only", f_reposts)]:
        ck.run(name, fn)

    # --- search -----------------------------------------------------------------------------------------
    def search_ranked():
        r, _ = ck.get("/api/jobs", {**BASE, "q": "data engineer", "sort": "posted"})
        body = r.json()
        first = body["items"][0]["title"].lower()
        assert "data engineer" in first, f"first result isn't the typed title: {first!r}"
        return f"{body['total']}{'+' if body['total_capped'] else ''} results, {len(body['related_terms'])} related roles"
    def search_exact():
        items = jobs({**BASE, "q": "data analyst", "related": "false"})
        bad = [j["title"] for j in items if not ("data" in j["title"].lower() and "analyst" in j["title"].lower())]
        assert not bad, f"exact title search returned: {bad[:3]}"
    def search_company():
        items = jobs({**BASE, "q": "stripe", "related": "false"}, expect_items=False)
        assert all("stripe" in (j["company"] + " " + j["title"]).lower() for j in items), "unrelated company"
        return f"{len(items)} jobs"
    def search_symbols():
        r, secs = ck.get("/api/jobs", {**BASE, "q": "*&^%"})
        assert r.status_code == 200 and r.json()["items"] == [] and secs < 3, f"symbols search: {secs:.1f}s"
    for name, fn in [("search: typed title first", search_ranked), ("search: exact title", search_exact),
                     ("search: company name", search_company), ("search: symbols only", search_symbols)]:
        ck.run(name, fn)

    # --- paging, counts, cache --------------------------------------------------------------------------
    def paging():
        a = {j["id"] for j in jobs({**BASE, "sort": "posted", "offset": 0})}
        b = {j["id"] for j in jobs({**BASE, "sort": "posted", "offset": 50})}
        assert not (a & b), f"pages overlap: {sorted(a & b)[:5]}"
    def fresh():
        r, _ = ck.get("/api/jobs", {**BASE, "alert_roles_only": "true", "fresh_counts": "true", "limit": 1})
        f = r.json()["fresh"]
        assert f["1"] <= f["6"] <= f["24"], f"counts don't nest: {f}"
        return f"last hour {f['1']}, 6 h {f['6']}, 24 h {f['24']}"
    def cache():
        params = {**BASE, "alert_roles_only": "true", "sort": "posted"}
        ck.get("/api/jobs", params, cache=True)
        r, secs = ck.get("/api/jobs", params, cache=True)
        assert r.headers.get("X-Cache") == "hit", f"second identical request not cached ({r.headers.get('X-Cache')})"
        return f"cached reply in {secs:.2f}s"
    for name, fn in [("paging: pages don't overlap", paging), ("freshness counts", fresh), ("response cache", cache)]:
        ck.run(name, fn)

    # --- job detail -------------------------------------------------------------------------------------
    def detail():
        jid = state.get("job_id")
        assert jid, "no job to open"
        r, _ = ck.get(f"/api/jobs/{jid}")
        assert r.status_code == 200, f"HTTP {r.status_code}"
        d = r.json()
        for key in ["description", "events", "sponsor", "h1b_roles", "everify_info", "uscis", "hiring_posts_url"]:
            assert key in d, f"detail lacks {key}"
        assert d["hiring_posts_url"].startswith("https://www.linkedin.com/search/results/content/"), "hiring posts link"
        if state.get("resume"):
            assert d["match"] and set(d["match"]["parts"]) == {"skills", "fit", "role", "experience", "education"}, "match parts"
        return f"job {jid}"
    def missing_job():
        r, _ = ck.get("/api/jobs/999999999")
        assert r.status_code == 404, f"HTTP {r.status_code} for a missing job"
    ck.run("job detail", detail)
    ck.run("missing job is a 404", missing_job)

    # --- pickers and sources ----------------------------------------------------------------------------
    def facets():
        r, _ = ck.get("/api/facets/locations")
        assert r.status_code == 200
        rows = r.json()
        text = str(rows)
        assert "United States" in text, "no United States in the country picker"
        r2, _ = ck.get("/api/facets/locations", {"country": "United States"})
        assert r2.status_code == 200 and "CA" in str(r2.json()), "no states for the US"
    def companies():
        r, _ = ck.get("/api/companies", {"limit": 50})
        assert r.status_code == 200 and r.json(), "no sources"
        r2, _ = ck.get("/api/companies", {"q": "greenhouse", "limit": 20})
        assert r2.status_code == 200
        return f"{len(r.json())} busiest sources"
    ck.run("location pickers", facets)
    ck.run("sources list", companies)

    # --- frontend ---------------------------------------------------------------------------------------
    if frontend:
        def front():
            r = httpx.get(frontend, timeout=30)
            assert r.status_code == 200 and 'id="root"' in r.text, "frontend page"
            r2 = httpx.get(frontend.rstrip("/") + "/api/status", timeout=60)
            assert r2.status_code == 200, f"API through the frontend proxy: HTTP {r2.status_code}"
        ck.run("frontend and its /api proxy", front)


def _log_in(client: httpx.Client) -> None:
    email, password = os.environ.get("FRESHJOBS_CHECK_EMAIL"), os.environ.get("FRESHJOBS_CHECK_PASSWORD")
    if not (email and password):
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from app.matching import _models_dir  # the folder next to the database

        path = os.path.join(os.path.dirname(_models_dir()), "check_account.json")
        if os.path.exists(path):
            saved = json.load(open(path, encoding="utf-8"))
            email, password = saved["email"], saved["password"]
        else:
            email, password = "portal-check@freshjobs.local", secrets.token_urlsafe(18)
            r = client.post("/api/auth/signup", json={"name": "Portal check", "email": email, "password": password})
            if r.status_code not in (200, 409):
                sys.exit(f"Couldn't create the check account: HTTP {r.status_code} {r.text[:200]}")
            json.dump({"email": email, "password": password}, open(path, "w", encoding="utf-8"))
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    if r.status_code != 200:
        sys.exit(f"Couldn't log in as {email}: HTTP {r.status_code} {r.text[:200]}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--frontend", default="http://localhost:5173")
    ap.add_argument("--no-frontend", action="store_true")
    args = ap.parse_args()
    with httpx.Client(base_url=args.base, timeout=120) as client:
        _log_in(client)
        checks(Check(client), None if args.no_frontend else args.frontend)
    width = max(len(n) for _, n, _, _ in RESULTS)
    for status, name, secs, detail in RESULTS:
        print(f"{status:4s}  {name:{width}s}  {secs:6.2f}s  {detail}")
    failed = sum(1 for r in RESULTS if r[0] == "FAIL")
    slow = sum(1 for r in RESULTS if r[0] == "SLOW")
    print(f"\n{len(RESULTS)} checks: {len(RESULTS) - failed - slow} ok, {slow} slow, {failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
