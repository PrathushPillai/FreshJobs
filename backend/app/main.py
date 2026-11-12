import asyncio
import base64
import hashlib
import logging
import re

import httpx
import threading
import time
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Literal

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, model_validator

from app.pipeline.sponsorship import LIKELY_FILINGS, sponsor_likelihood
from sqlalchemy import Integer, and_, case, column, exists, false, func, or_, select, text, true
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, defer, selectinload

from app.config import settings
from app.db import SessionLocal, engine, get_session, init_db, is_sqlite
from app.everify import lookup as everify_lookup
from app.logos import logo_domain
from app.uscis import lookup as uscis_lookup
from app.h1b import role_history, sponsor_index
from app.models import APPLICATION_STATUSES, Application, Company, H1BSponsor, Job, JobEvent, JobLocation, JobMatch, User, UserProfile
from app.notify.email import email_configured, email_mode, send_test
from app.pipeline.detect import detect_from_page
from app.pipeline import liveness
from app import matching
from app.pipeline import scoring
from app.pipeline.alerts import deliver_alerts
from app.pipeline.describe import fetch_description
from app.pipeline.scoring import ensure_scores, get_profile, stored_scores
from app.activity import record
from app.admin import router as admin_router
from app.support import router as support_router
from app.auth import COOKIE, admin_user, current_user, require_login, router as auth_router
from app.pipeline.poller import (
    MIN_DESCRIPTION_CHARS, USER_AGENT, apply_flags, backfill, poll_once, refresh_role_match, refresh_role_match_for,
)
from app.pipeline.search import distinct_roles, expand_query, fts_all_words, fts_any_term, fts_company
from app.prefs import get_prefs, sync_companies
from app.recruiters import hiring_posts_url, hunter_configured, hunter_lookup, search_links
from app.sources import ADAPTERS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger(__name__)

# Fast boards (Greenhouse, Lever, Ashby, aggregators) and slow ones (Workday, SmartRecruiters: a request
# per job) poll in separate lanes with their own locks, so a long Workday scan never delays the fast boards.
# Each lane runs in its own worker process (app/lanes.py, settings.poll_workers); without workers, in a thread
# of this process with its own lock.
from app.lanes import LANES  # noqa: E402

_poll_locks = {lane: threading.Lock() for lane in LANES}
_last_poll: dict[str, dict] = {}


def _lane_busy(lane: str) -> bool:
    """Whether a lane (or "discovery") is polling right now: its worker's status file, or this process's lock."""
    if not settings.poll_workers:
        return _discovery_lock.locked() if lane == "discovery" else _poll_locks[lane].locked()
    from app import lanes

    st = lanes.lane_status(lane)
    try:  # a worker that stopped mid-poll left polling=true behind: trust it only with a recent heartbeat
        fresh = datetime.now(timezone.utc) - datetime.fromisoformat(st["heartbeat"]) < timedelta(minutes=2)
    except (KeyError, ValueError):
        fresh = False
    return bool(st.get("polling")) and fresh


def _after_poll(lane: str, summary: dict) -> None:
    """The API process's share of a finished poll (the worker does scoring and alerts)."""
    _last_poll[lane] = summary
    _jobs_cache.clear()  # cached result pages would miss this poll's jobs
    if lane == "fast":
        _place_counts(force=True)  # location picker counts include this poll's jobs
        threading.Thread(target=_warm_suggestions, daemon=True).start()


# Counting stops here ("2,000+"): counting further meant reading up to 10,000 matching jobs (42 s cold).
COUNT_CAP = 2_000


def _checkpoint() -> None:
    """Fold the WAL back into the database. With the API reading nonstop, SQLite's automatic checkpoints
    never get to reset it, and a WAL that grows to gigabytes slows every read."""
    if not is_sqlite:
        return
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception:
        log.warning("WAL checkpoint failed", exc_info=True)


def run_poll(lane: str) -> dict:
    """Run one poll of a lane in this thread; skip if that lane is already polling."""
    lock = _poll_locks[lane]
    if not lock.acquire(blocking=False):
        return {"skipped": f"the {lane} lane is already polling"}
    try:
        started = datetime.now(timezone.utc)
        summary = asyncio.run(poll_once(lane))
        _last_poll[lane] = {**summary, "started_at": started.isoformat(), "finished_at": datetime.now(timezone.utc).isoformat()}
        _checkpoint()
        _jobs_cache.clear()  # cached result pages would miss this poll's jobs
        if lane == "fast":
            _place_counts(force=True)  # location picker counts include this poll's jobs
            threading.Thread(target=_warm_suggestions, daemon=True).start()
            scoring.refresh_in_background()  # score this poll's new jobs against the resume
            deliver_alerts()  # each user's due alerts (opt-in, per their frequency and daily cap)
        return summary
    except Exception:
        log.exception("Poll of %s lane failed", lane)
        return {"error": "poll failed, see server logs"}
    finally:
        lock.release()


_discovery_lock = threading.Lock()


def run_discovery() -> dict:
    """One run of the always-on board discovery (pipeline/autodiscover.py) in this thread; skipped if one is
    already running. Its own lock, so it never holds up a polling lane."""
    from app.pipeline import autodiscover

    if not _discovery_lock.acquire(blocking=False):
        return {"skipped": "discovery is already running"}
    try:
        summary = asyncio.run(autodiscover.run_once())
        summary["jobdiva_added"] = asyncio.run(autodiscover.jobdiva_portals())  # weekly
        summary["crawl_added"] = asyncio.run(autodiscover.crawl_new_index())  # only when a new index is out
        return summary
    except Exception:
        log.exception("Board discovery failed")
        return {"error": "discovery failed, see server logs"}
    finally:
        _discovery_lock.release()


def _backfill_until_done(wait_seconds: int = 60) -> None:
    while True:
        time.sleep(wait_seconds)
        try:
            backfill()
            log.info("Postponed backfill finished")
            return
        except OperationalError as e:
            log.warning("Backfill still waiting for the database (%s)", e.orig)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    with SessionLocal() as session:
        sync_companies(session, settings.companies_file)
    try:
        backfill()
    except OperationalError as e:
        # Another process held the write lock past the busy timeout (2026-11-10: a visa_signals re-read). That
        # used to end the startup ("Application startup failed. Exiting."), so the site stayed down; the backfill
        # only fills older rows, so retry it in the background instead.
        log.warning("Startup backfill postponed (%s); retrying in the background", e.orig)
        threading.Thread(target=_backfill_until_done, daemon=True).start()
    scheduler = BackgroundScheduler()
    workers = settings.scheduler_enabled and settings.poll_workers
    if settings.scheduler_enabled:
        threading.Thread(target=_place_counts, daemon=True).start()  # location picker counts, off the startup path
        with SessionLocal() as s:
            p = get_prefs(s)
            include, exclude = list(p.include_keywords), list(p.exclude_keywords)
        threading.Thread(target=refresh_role_match, args=(include, exclude), daemon=True).start()  # rules may have changed
        threading.Thread(target=_warm_suggestions, daemon=True).start()  # the "Your roles" bar's counts
        scoring.refresh_in_background()  # jobs found while the backend was off
        _open_counts_lock.acquire()
        threading.Thread(target=_recount_open_jobs, daemon=True).start()  # the Sources page's counts
    if workers:
        from app import lanes

        for lane in lanes.WORKERS:  # what the workers last reported, until their next poll
            if lanes.lane_status(lane).get("last"):
                _last_poll[lane] = lanes.lane_status(lane)["last"]
        lanes.start_workers()
        threading.Thread(target=lanes.watch_lanes, args=(_after_poll,), daemon=True).start()
    elif settings.scheduler_enabled:
        first_run = datetime.now() if settings.poll_on_startup else None
        for lane in LANES:
            scheduler.add_job(run_poll, "interval", args=[lane], minutes=settings.poll_minutes,
                              next_run_time=first_run, max_instances=1, coalesce=True)
        if settings.autodiscover_minutes > 0:  # finds new employers' boards by itself, a batch an hour
            scheduler.add_job(run_discovery, "interval", minutes=settings.autodiscover_minutes,
                              next_run_time=datetime.now() + timedelta(minutes=10), max_instances=1, coalesce=True)
        scheduler.start()
    yield
    if scheduler.running:
        scheduler.shutdown(wait=False)
    if workers:
        lanes.stop_workers()


app = FastAPI(title="Job Portal", lifespan=lifespan, dependencies=[Depends(require_login)],
              # The automatic API pages (/docs, /openapi.json) show every route: off unless API_DOCS=true.
              docs_url="/docs" if settings.api_docs else None, redoc_url=None,
              openapi_url="/openapi.json" if settings.api_docs else None)
app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(support_router)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"])

# GET /api/jobs responses by query string. The Feed asks for the same pages again and again (its
# 60-second refresh, going back to an earlier filter, the freshness counts), and a page takes seconds on
# 1.6M jobs. Cleared after every poll and by any change the user makes (tracking a job, editing prefs).
JOBS_CACHE_SECONDS = 60
_jobs_cache: dict[str, tuple[float, bytes]] = {}


@app.middleware("http")
async def _rate_limit(request: Request, call_next):
    """At most settings.api_requests_per_minute API requests a minute per visitor (app/ratelimit.py).

    A visitor is their login cookie, else their address (behind Caddy, the real one via --proxy-headers). A
    person clicking around makes a few dozen a minute; a script could keep the 2-vCPU server's CPUs busy and the
    pollers would fall behind. /api/status is exempt: every open tab asks for it every 10 seconds.
    """
    from fastapi.responses import JSONResponse

    from app.ratelimit import limits

    path = request.url.path
    if path.startswith("/api/") and path != "/api/status" and settings.api_requests_per_minute > 0:
        cookie = request.cookies.get(COOKIE)
        if cookie:
            key = "api:s:" + hashlib.sha256(cookie.encode()).hexdigest()[:24]
        else:
            key = "api:ip:" + (request.client.host if request.client else "?")
        if not limits.allow(key, settings.api_requests_per_minute, 60):
            return JSONResponse({"detail": "Too many requests. Please wait a minute and try again."}, status_code=429,
                                headers={"Retry-After": str(limits.retry_after(key, 60))})
    return await call_next(request)


@app.middleware("http")
async def _cache_job_lists(request: Request, call_next):
    if request.method != "GET":
        # Opening a job checks its apply page (POST .../verify) every time: that mustn't empty the cache. A job
        # it finds closed drops out of cached pages at the next poll.
        if request.url.path.startswith("/api/") and not request.url.path.endswith(("/verify", "/describe")):
            _jobs_cache.clear()
        return await call_next(request)
    if request.url.path != "/api/jobs":
        return await call_next(request)
    key = request.cookies.get(COOKIE, "") + "|" + request.url.query  # pages are personal: one cache per login
    hit = _jobs_cache.get(key)
    if hit and time.monotonic() - hit[0] < JOBS_CACHE_SECONDS:
        return Response(content=hit[1], media_type="application/json", headers={"X-Cache": "hit"})
    response = await call_next(request)
    if response.status_code != 200:
        return response
    body = b"".join([chunk async for chunk in response.body_iterator])
    if len(_jobs_cache) > 500:
        _jobs_cache.clear()
    _jobs_cache[key] = (time.monotonic(), body)
    return Response(content=body, status_code=200, media_type="application/json", headers={"X-Cache": "miss"})


# ---------- schemas ----------

class PlaceOut(BaseModel):
    city: str | None
    state: str | None
    country: str | None


class TrackedOut(BaseModel):
    id: int
    status: str


class JobOut(BaseModel):
    id: int
    company: str
    company_id: int
    source: str
    title: str
    location: str
    places: list[PlaceOut]
    remote: bool
    url: str
    department: str
    posted_at: datetime | None
    first_seen_at: datetime
    listed_at: datetime | None
    listed_until: datetime | None
    checked_at: datetime | None
    closed_at: datetime | None
    is_repost: bool
    repost_of_id: int | None
    repost_kind: str | None  # "repost", "duplicate" or "resurfaced" (see process_company); None if not a repeat
    no_sponsor: bool
    student_visa: str | None  # "welcome" / "excluded": what the description says about OPT/CPT (F-1)
    everify_text: bool  # the description says the employer uses E-Verify
    sponsors_text: bool = False  # the description says it sponsors visas (and doesn't rule it out anywhere)
    everify: str | None  # the employer in USCIS's E-Verify list: "yes" (enrolled), "ended", None (not found)
    logo_domain: str | None  # the employer's website, for its logo (app/logos.py); None shows initials
    match: dict | None = None  # resume match (app/matching.py): score, matched/missing skills; None without a resume
    seniority: str
    min_years: int | None
    exp_level: str
    baseline: bool
    h1b_filings: int | None
    employer_kind: str | None = None  # "consultancy", "staffing" or empty (h1b.job_employer_kind): a badge
    application: TrackedOut | None
    # How likely the employer is to sponsor (pipeline/sponsorship.sponsor_likelihood): likely, possible,
    # unlikely or ruled_out. Derived from the fields above, so it never disagrees with them.
    sponsor_likely: str = "unlikely"

    @model_validator(mode="after")
    def _sponsor_likely(self):
        self.sponsor_likely = sponsor_likelihood(self.no_sponsor, self.sponsors_text, self.h1b_filings, self.everify)
        return self


class EventOut(BaseModel):
    kind: str
    at: datetime
    detail: str


class SponsorOut(BaseModel):
    filings: int
    employers: list[str]  # DOL employer names that matched, biggest first
    top_titles: list[str]
    source: str


class OriginalOut(BaseModel):
    id: int
    title: str
    company: str
    url: str
    first_seen_at: datetime
    closed_at: datetime | None


class H1BRoleOut(BaseModel):
    soc_code: str
    soc_title: str
    filings: int
    median_wage: int | None
    low_wage: int | None
    high_wage: int | None
    top_titles: list[str]
    top_cities: list[str]


class H1BMatchOut(H1BRoleOut):
    matched_by: str  # "title" (similar LCA job titles) or "occupation" (the usual occupation for the title)


class H1BHistoryOut(BaseModel):
    total: int
    match: H1BMatchOut | None
    roles: list[H1BRoleOut]


class EVerifyOut(BaseModel):
    status: str  # "yes" or "ended"
    since: date | None  # earliest enrollment
    ended: date | None  # when the last account ended ("ended" only)
    name: str  # the enrolled name that matched
    workforce: str
    accounts: int


class USCISYearOut(BaseModel):
    fiscal_year: int
    new_approvals: int
    new_denials: int
    approvals: int
    denials: int


class USCISOut(BaseModel):
    years: list[USCISYearOut]  # newest first
    names: list[str]  # petitioner names that matched


class JobDetailOut(JobOut):
    description: str
    repost_of: OriginalOut | None
    h1b_roles: H1BHistoryOut | None  # the employer's H-1B filings by occupation, and the one fitting this job
    everify_info: EVerifyOut | None
    uscis: USCISOut | None  # H-1B petitions USCIS approved and denied, per fiscal year
    events: list[EventOut]
    sponsor: SponsorOut | None
    hiring_posts_url: str  # LinkedIn posts saying they're hiring for this role at this company (a link, never fetched)


class JobPage(BaseModel):
    total: int
    total_capped: bool = False  # more than COUNT_CAP matches; total is COUNT_CAP
    # With fresh_counts=true: matches posted in the last 1, 6 and 24 hours ({"1": n, "6": n, "24": n}).
    fresh: dict[str, int] | None = None
    related_terms: list[str]
    items: list[JobOut]


class CompanyOut(BaseModel):
    id: int
    name: str
    ats: str
    slug: str
    enabled: bool
    last_polled_at: datetime | None
    last_status: str | None
    open_jobs: int


class CompanyIn(BaseModel):
    name: str
    ats: str
    slug: str


class CareersUrlIn(BaseModel):
    url: str
    name: str = ""


class CompanyPatch(BaseModel):
    enabled: bool


class PrefsModel(BaseModel):
    include_keywords: list[str]
    exclude_keywords: list[str]
    email_alerts: bool = False  # read-only here: switched on in /api/alerts/settings (needs a confirmed email)
    hide_no_sponsor: bool = True
    exclude_senior: bool = True


class AlertSettingsIn(BaseModel):
    email_alerts: bool | None = None
    alert_frequency: Literal["instant", "hourly", "daily"] | None = None
    alert_min_score: int | None = Field(None, ge=0, le=100)
    alert_daily_cap: int | None = Field(None, ge=1, le=50)
    hide_no_sponsor: bool | None = None
    exclude_senior: bool | None = None


class ApplicationOut(BaseModel):
    id: int
    job_id: int | None
    company: str
    title: str
    url: str
    location: str
    status: str
    applied_at: datetime | None
    notes: str
    contact: str
    created_at: datetime
    updated_at: datetime
    job_closed: bool | None


class ApplicationIn(BaseModel):
    job_id: int | None = None
    company: str | None = None
    title: str | None = None
    url: str = ""
    location: str = ""
    status: str = "applied"
    notes: str = ""
    contact: str = ""


class ApplicationPatch(BaseModel):
    status: str | None = None
    notes: str | None = None
    contact: str | None = None
    applied_at: datetime | None = None


REPEAT_KINDS = ("repost", "duplicate", "resurfaced")


def _repeat_kinds(session: Session, jobs: list[Job]) -> dict[int, str]:
    """Which kind of repeat each flagged job is, from the JobEvent written when it was found."""
    ids = [j.id for j in jobs if j.is_repost]
    if not ids:
        return {}
    rows = session.execute(
        select(JobEvent.job_id, JobEvent.kind).where(JobEvent.job_id.in_(ids), JobEvent.kind.in_(REPEAT_KINDS))
    )
    return {job_id: kind for job_id, kind in rows}


def repeats_page(session: Session, parts: list, order: tuple, sort: str, limit: int, offset: int) -> list[int]:
    """One page from several queries that each walk the sort index (the repeats view's normal jobs and its
    repeats): the first offset+limit of each, merged in the page's sort order."""
    n = offset + limit
    ids = [i for part in parts for i in session.scalars(part.order_by(*order).limit(n))]
    if not ids:
        return []
    rows = {r.id: r for r in session.execute(
        select(Job.id, Job.first_seen_at, Job.listed_at, Job.company_name, Job.h1b_filings).where(Job.id.in_(ids)))}

    def desc_nulls_last(col):
        return lambda i: (getattr(rows[i], col) is not None, getattr(rows[i], col) or 0)

    # (key, reverse) from the least to the most significant: Python's sort is stable.
    keys = {
        "newest": [(lambda i: i, True), (lambda i: rows[i].first_seen_at, True)],
        "posted": [(lambda i: i, True), (desc_nulls_last("listed_at"), True)],
        "company": [(lambda i: rows[i].first_seen_at, True), (lambda i: rows[i].company_name or "", False)],
        "sponsor": [(lambda i: rows[i].first_seen_at, True), (desc_nulls_last("h1b_filings"), True)],
    }.get(sort, [(lambda i: i, True), (lambda i: rows[i].first_seen_at, True)])
    for key, reverse in keys:
        ids.sort(key=key, reverse=reverse)
    return ids[offset:offset + limit]


def _match_brief(m: JobMatch | None) -> dict | None:
    """What a job card shows of a match: the score and a few matched and missing skills."""
    if m is None:
        return None
    d = m.detail or {}
    return {"score": m.score, "matched": (d.get("matched") or [])[:4], "missing": (d.get("missing") or [])[:3],
            "skills_asked": d.get("skills_asked", 0), "notes": d.get("notes") or []}


def _job_out(job: Job, repeat_kind: str | None = None, application: Application | None = None) -> dict:
    skip = ("company", "source", "places", "application", "repost_kind", "logo_domain", "match", "sponsor_likely")
    data = {k: getattr(job, k) for k in JobOut.model_fields if k not in skip}
    if job.is_repost:  # jobs flagged before kinds were recorded: no original means resurfaced
        data["repost_kind"] = repeat_kind or ("repost" if job.repost_of_id else "resurfaced")
    else:
        data["repost_kind"] = None
    data["company"] = job.company_name or job.company.name
    data["logo_domain"] = logo_domain(job.url or "", job.company.ats, job.company.slug, data["company"])
    data["source"] = job.company.ats
    data["exp_level"] = job.exp_level or "unknown"
    data["places"] = [{"city": p.city, "state": p.state, "country": p.country} for p in job.places]
    data["application"] = {"id": application.id, "status": application.status} if application else None
    return data


def _applications_for(session: Session, user: User, job_ids: list[int]) -> dict[int, Application]:
    """This user's tracker entries for these jobs: job id -> entry."""
    if not job_ids:
        return {}
    return {a.job_id: a for a in session.scalars(
        select(Application).where(Application.user_id == user.id, Application.job_id.in_(job_ids)))}


def _own_roles(session: Session, user: User):
    """The extra condition for this user's For you, or None when Job.role_match already is exactly their roles.

    Job.role_match is everyone's roles together (prefs.get_prefs); a user's own roles, minus the words only they
    exclude, are a full-text match on titles (an id set, so the sort-index walk stays index-only)."""
    from app.pipeline.search import fts_any_term

    mine, everyone = get_prefs(session, user.id), get_prefs(session)
    if mine is everyone or (sorted(mine.include_keywords or []) == sorted(everyone.include_keywords or [])
                            and set(mine.exclude_keywords or []) <= set(everyone.exclude_keywords or [])):
        return None
    include = fts_any_term(mine.include_keywords or [])
    if include is None:
        return false()
    extra_exclude = [w for w in mine.exclude_keywords or [] if w not in set(everyone.exclude_keywords or [])]
    exclude = fts_any_term(extra_exclude) if extra_exclude else None
    return _fts("fts_own_roles", f"({include}) NOT ({exclude})" if exclude else include)


def _company_out(company: Company, open_jobs: int) -> dict:
    return {**{k: getattr(company, k) for k in CompanyOut.model_fields if k != "open_jobs"}, "open_jobs": open_jobs}


def _title_matches(term: str):
    """Title contains the term; short terms ("hr", "sre", "bi") must be whole words."""
    if len(term) > 4:
        return Job.title.ilike(f"%{term}%")
    patterns = [term, f"{term} %", f"% {term}", f"% {term} %", f"%({term})%", f"% {term},%", f"%/{term}%", f"%{term}/%"]
    return or_(*(Job.title.ilike(p) for p in patterns))


def _has_all_words(q: str):
    """Title contains every word of the query, in any order ("Analyst, Data" matches "data analyst")."""
    words = [w for w in re.findall(r"[\w+#&.-]+", q.lower()) if w not in ("and", "or", "of", "the", "a", "an", "in")]
    return and_(*(_title_matches(w) for w in words)) if words else true()


def _fts(name: str, match: str):
    """jobs whose title/company FTS5 entry matches (db._ensure_title_search keeps jobs_fts in sync)."""
    # A real IN expression (not raw SQL), so a search's ranking tiers can negate it ("typed but not the phrase").
    matches = text(f"SELECT rowid FROM jobs_fts WHERE jobs_fts MATCH :{name}").bindparams(**{name: match})
    return Job.id.in_(matches.columns(column("rowid", Integer)))


FTS_FIRST_MAX = 20_000


def _title_search(q: str, related: list[str]):
    """(exact phrase, what was typed, related roles, combined FTS match or None) for a search. "What was
    typed" is a title with every word of the query, or the company's name. The combined match is one FTS5
    query for "typed or related". Uses the full-text index on SQLite, LIKE scans elsewhere."""
    if is_sqlite and fts_all_words(q):
        typed_match = f"({fts_all_words(q)}) OR ({fts_company(q)})"
        any_related = fts_any_term(related)
        phrase = _fts("fts_phrase", fts_any_term([q]))
        typed = _fts("fts_typed", typed_match)
        combined = f"({typed_match}) OR ({any_related})" if any_related else typed_match
        return phrase, typed, _fts("fts_related", any_related) if any_related else None, combined
    typed = or_(_has_all_words(q), Job.company_name.ilike(f"%{q.strip()}%"))
    return _title_matches(q.strip().lower()), typed, or_(*(_title_matches(t) for t in related)) if related else None, None


# ---------- jobs ----------

@app.get("/api/jobs", response_model=JobPage)
def list_jobs(
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
    q: str = "",
    related: bool = True,
    also: list[str] = Query([]),
    skip: list[str] = Query([]),
    sort: Literal["newest", "posted", "company", "sponsor", "match"] = "newest",
    status: Literal["open", "closed", "all"] = "open",
    countries: list[str] = Query([]),
    states: list[str] = Query([]),
    cities: list[str] = Query([]),
    include_unplaced_remote: bool = True,
    exp_levels: list[str] = Query([]),
    max_years: int | None = None,
    remote_only: bool = False,
    sponsors_only: bool = False,
    sponsor_likely: str | None = None,  # "likely": likely only; "possible": likely or possible (sponsor_likelihood)
    everify_only: bool = False,  # employers in USCIS's E-Verify list, or whose posting says they use it
    hide_consultancies: bool = False,  # opt-in: leave out consultancies and staffing firms (Job.employer_kind)
    hide_no_sponsor: bool = True,
    show_reposts: bool = False,
    repeat_kinds: list[str] = Query([]),  # with show_reposts: which repeats to show (empty = all of REPEAT_KINDS)
    alert_roles_only: bool = False,
    tracked: Literal["any", "tracked", "untracked"] = "any",
    since_hours: float | None = None,
    posted_within_hours: float | None = None,
    company_id: int | None = None,
    limit: int = Query(50, le=200),
    offset: int = 0,
    fresh_counts: bool = False,  # instead of a page: how many matches were posted in the last 1, 6, 24 hours
):
    if q.strip() and not re.search(r"\w", q):
        # Only symbols ("*&^%"): nothing a title can match. The LIKE fallback scanned every job (17 s).
        return {"total": 0, "total_capped": False, "related_terms": [], "items": [], "fresh": {"1": 0, "6": 0, "24": 0} if fresh_counts else None}
    # Title search first: how many jobs it matches decides the query plan (below).
    related_terms: list[str] = []
    search_clause = None
    tiers: list = []  # a search's ranking groups, best first (see the paging below)
    tier_matches: list = []  # each tier's FTS match string (SQLite), to size it
    fts_first = False
    if q.strip():
        # related=true (the Feed's default) searches every related role except the ones the user
        # switched off (`skip`); related=false is "Exact title", plus any roles picked in `also`.
        # Ranking keeps exact-title matches first either way.
        related_terms = distinct_roles(expand_query(q)[1:], [q])
        skipped = {t.strip().lower() for t in skip}
        chosen = [t for t in related_terms if t not in skipped] if related else [t.strip().lower() for t in also if t.strip()]
        phrase, typed, related_match, combined = _title_search(q, chosen)
        search_clause = or_(typed, related_match) if related_match is not None else typed
        if combined is not None:
            # One FTS lookup for "typed or related", and for a search matching few jobs, start from its
            # matches (via ix_jobs_id_feed) instead of walking every open job: "react" 1.5 s -> 0.03 s.
            # A broad one ("software engineer", tens of thousands of titles) is faster the other way.
            search_clause = _fts("fts_all", combined)
            matches = session.scalar(text("SELECT count(*) FROM (SELECT rowid FROM jobs_fts WHERE jobs_fts MATCH :m LIMIT :cap)"),
                                     {"m": combined, "cap": FTS_FIRST_MAX + 1})
            fts_first = matches <= FTS_FIRST_MAX
        # The exact phrase first ("Data Analyst II"), then every word in any order ("Analyst, Data"),
        # then related roles; each group in the chosen sort.
        # Each tier stands alone (it implies the search), so a narrow tier can be its own starting point.
        tiers = [phrase, and_(typed, ~phrase)] + ([and_(related_match, ~typed, ~phrase)] if related_match is not None else [])
        if combined is not None:
            # Each tier's full-text match (a superset of it), to pick the tier's plan: a rare exact phrase
            # ("backend engineer python") walked every open job looking for 50 of them (4-5 s).
            tier_matches = [fts_any_term([q]), f"({fts_all_words(q)}) OR ({fts_company(q)})", fts_any_term(chosen)]

    if fresh_counts:
        posted_within_hours = 24  # the strip's widest window; the 1 h and 6 h counts are taken inside it

    def build(fts_first: bool, with_search: bool = True, repeats_part: str = ""):
        """The filtered query. With fts_first, status filters are written so SQLite starts from the search's
        full-text matches instead of walking the open-jobs index (see FTS_FIRST_MAX)."""

        def flag(column: str, clause):
            """A status filter; written as "+jobs.col" when the plan starts from the search matches, so SQLite
            doesn't pick the open-jobs index for it instead."""
            return text(f"+jobs.{column}") if fts_first else clause

        stmt = select(Job)
        if status == "open":
            stmt = stmt.where(text("+jobs.closed_at IS NULL") if fts_first else Job.closed_at.is_(None))
        elif status == "closed":
            stmt = stmt.where(Job.closed_at.is_not(None))
        if repeats_part == "normal" or not show_reposts:
            stmt = stmt.where(flag("is_repost IS 0", Job.is_repost.is_(False)))
        elif repeats_part == "chosen":
            stmt = stmt.where(Job.is_repost.is_(True), Job.id.in_(select(JobEvent.job_id).where(JobEvent.kind.in_(shown_kinds))))
        elif split_repeats:
            stmt = stmt.where(or_(
                Job.is_repost.is_(False),
                Job.id.in_(select(JobEvent.job_id).where(JobEvent.kind.in_(shown_kinds))),
            ))
        if hide_no_sponsor:
            stmt = stmt.where(flag("no_sponsor IS 0", Job.no_sponsor.is_(False)))
        if alert_roles_only:
            stmt = stmt.where(Job.role_match.is_(True))  # someone's For you (all users' roles together)
            if mine is not None:
                stmt = stmt.where(mine)  # and this user's own roles
        if remote_only:
            stmt = stmt.where(Job.remote.is_(True))
        if hide_consultancies:
            # Opt-in, so it may read job rows (employer_kind isn't in the sort indexes): the walk stops after a page.
            stmt = stmt.where(or_(Job.employer_kind.is_(None), Job.employer_kind == ""))
        if sponsors_only:
            # "+jobs.h1b_filings" except when sorting by filings: as a plain range SQLite started from
            # ix_jobs_by_sponsor, read every sponsored open job and sorted them all by date (5 s warm,
            # 32 s while polls loaded the disk, 2026-11-11) instead of walking the sort index for one page.
            stmt = stmt.where(Job.h1b_filings > 0 if sort == "sponsor" else text("+jobs.h1b_filings > 0"))
        if sponsor_likely in ("likely", "possible"):  # the same rules as sponsor_likelihood(), in SQL
            signs = [Job.sponsors_text.is_(True), Job.h1b_filings >= LIKELY_FILINGS]
            if sponsor_likely == "possible":
                signs += [Job.h1b_filings > 0, Job.everify == "yes"]
            stmt = stmt.where(Job.no_sponsor.is_not(True), or_(*signs))
        if everify_only:
            stmt = stmt.where(or_(Job.everify == "yes", Job.everify_text.is_(True)))
        if since_hours:
            stmt = stmt.where(Job.first_seen_at >= datetime.now(timezone.utc) - timedelta(hours=since_hours))
        if posted_within_hours:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=posted_within_hours)
            stmt = stmt.where(Job.listed_at >= cutoff)  # undated jobs can't qualify
        if company_id:
            stmt = stmt.where(Job.company_id == company_id)

        if search_clause is not None and with_search:
            stmt = stmt.where(search_clause)

        if countries or states or cities:
            place = []
            if countries:
                place.append(JobLocation.country.in_(countries))
            if states:
                place.append(JobLocation.state.in_(states))
            if cities:
                place.append(JobLocation.city.in_(cities))
            # A state or city matches few jobs: look those up first (Boston: 15 s -> 0.5 s). A country matches
            # most, so check each job as the feed's index walks them, and stop after a page.
            in_place = (Job.id.in_(select(JobLocation.job_id).where(*place)) if states or cities
                        else exists().where(JobLocation.job_id == Job.id, *place))
            if include_unplaced_remote and not (states or cities):
                # "Remote" with no stated country: keep it, the country may still fit.
                unplaced_remote = and_(Job.remote.is_(True), ~exists().where(JobLocation.job_id == Job.id))
                stmt = stmt.where(or_(in_place, unplaced_remote))
            else:
                stmt = stmt.where(in_place)

        if exp_levels:
            stmt = stmt.where(Job.exp_level.in_(exp_levels))
        if max_years is not None:
            stmt = stmt.where(or_(Job.min_years <= max_years, Job.min_years.is_(None)))

        if tracked != "any":
            mine_tracked = select(Application.job_id).where(Application.user_id == user.id, Application.job_id.is_not(None))
            if tracked == "tracked":  # the tracker's few jobs: an IN, not an EXISTS on every open job (5.6 s)
                stmt = stmt.where(Job.id.in_(mine_tracked))
            else:
                stmt = stmt.where(Job.id.not_in(mine_tracked))
        return stmt

    # "Show repeated jobs" with some kinds ticked is "not a repeat, or a repeat of a ticked kind". As one OR it
    # can't walk a sort index (no equality on is_repost), so it sorted every open job: 8 s. The page is taken
    # from the two parts instead, each walking the index, and merged (see repeats_page below).
    shown_kinds = [k for k in repeat_kinds if k in REPEAT_KINDS]
    split_repeats = show_reposts and bool(shown_kinds) and len(set(shown_kinds)) < len(REPEAT_KINDS)
    mine = _own_roles(session, user) if alert_roles_only else None
    stmt = build(fts_first)

    # Count and sort on IDs only, so SQLite answers from ix_jobs_feed without reading the wide rows
    # (descriptions), then load just the page's jobs. Selecting full rows made a search take ~6 s.
    ids = stmt.with_only_columns(Job.id)
    profile_version = None
    if sort == "match":
        profile = get_profile(session, user.id)
        profile_version = profile.version if profile and profile.resume_text else -1

    def with_match(q):
        """Best match first (unscored jobs last), for sort=match."""
        if profile_version is None:
            return q
        return q.outerjoin(JobMatch, and_(JobMatch.user_id == user.id, JobMatch.job_id == Job.id,
                                          JobMatch.profile_version == profile_version))

    ids = with_match(ids)
    if fresh_counts:
        # One scan, newest first, so the 1 h and 6 h counts are exact even when the day's count is capped.
        now = datetime.now(timezone.utc)
        newest = stmt.with_only_columns(Job.listed_at).order_by(Job.listed_at.desc()).limit(COUNT_CAP + 1).subquery()
        day, six, hour = session.execute(select(
            func.count(),
            func.coalesce(func.sum(case((newest.c.listed_at >= now - timedelta(hours=6), 1), else_=0)), 0),
            func.coalesce(func.sum(case((newest.c.listed_at >= now - timedelta(hours=1), 1), else_=0)), 0),
        )).one()
        return {"total": min(day, COUNT_CAP), "total_capped": day > COUNT_CAP, "related_terms": related_terms,
                "items": [], "fresh": {"1": min(hour, COUNT_CAP), "6": min(six, COUNT_CAP), "24": min(day, COUNT_CAP)}}
    # Counting every match of a broad query (800k open jobs) takes seconds, so stop at COUNT_CAP ("2,000+").
    total = session.scalar(select(func.count()).select_from(ids.limit(COUNT_CAP + 1).subquery()))
    order = {
        "newest": (Job.first_seen_at.desc(), Job.id.desc()),
        # listed_at is set whenever the source gives a date; undated jobs (NULL) sort last in DESC order.
        "posted": (Job.listed_at.desc(), Job.id.desc()),
        "company": (Job.company_name, Job.first_seen_at.desc()),
        # Plain DESC so ix_jobs_by_sponsor can serve it; SQLite sorts NULL (no filings) last in DESC order.
        "sponsor": (Job.h1b_filings.desc(), Job.first_seen_at.desc()),
        "match": (JobMatch.score.desc().nulls_last(), Job.listed_at.desc(), Job.id.desc()),
    }[sort]
    if not tiers and profile_version is not None:
        # Best match: walk ix_job_matches_version_score from the top and check each job's filters, stopping
        # after a page. Sorting every matching job by a joined score took 12 s for "For you". Jobs not scored
        # yet (only while the background scoring runs) follow, newest first.
        scored = (stmt.with_only_columns(Job.id)
                  .join(JobMatch, and_(JobMatch.user_id == user.id, JobMatch.job_id == Job.id,
                                       JobMatch.profile_version == profile_version))
                  .order_by(JobMatch.score.desc(), JobMatch.job_id.desc()))
        page_ids = list(session.scalars(scored.limit(limit).offset(offset)))
        if len(page_ids) < limit:
            n_scored = len(page_ids) + offset if page_ids else session.scalar(
                select(func.count()).select_from(scored.order_by(None).limit(offset).subquery()))
            unscored = ids.where(JobMatch.job_id.is_(None)).order_by(Job.listed_at.desc(), Job.id.desc())
            page_ids += list(session.scalars(unscored.limit(limit - len(page_ids)).offset(max(0, offset - n_scored))))
    elif not tiers and split_repeats and not fts_first:
        page_ids = repeats_page(session, [build(False, repeats_part=p).with_only_columns(Job.id) for p in ("normal", "chosen")],
                                order, sort, limit, offset)
    elif not tiers:
        page_ids = list(session.scalars(ids.order_by(*order).limit(limit).offset(offset)))
    else:
        # Ranked search: exact phrase, then every word, then related roles, each in the chosen sort. Ranking
        # every match before taking a page sorted tens of thousands of rows ("software engineer" 7-14 s), so
        # the page is filled tier by tier instead, each tier walking the sort index and stopping when full.
        page_ids, to_skip, wanted = [], offset, limit
        for i, tier in enumerate(tiers):
            tier_ids = ids.where(tier)
            if tier_matches and not fts_first and tier_matches[i]:
                few = session.scalar(text("SELECT count(*) FROM (SELECT rowid FROM jobs_fts WHERE jobs_fts MATCH :m LIMIT :cap)"),
                                     {"m": tier_matches[i], "cap": FTS_FIRST_MAX + 1}) <= FTS_FIRST_MAX
                if few:  # start this tier from its own matches, like a narrow search
                    # Without the whole search's (broad) match set, so SQLite starts from this tier's few.
                    tier_ids = with_match(build(True, with_search=False).with_only_columns(Job.id)).where(tier)
            if to_skip:
                in_tier = session.scalar(select(func.count()).select_from(tier_ids.limit(to_skip).subquery()))
                if in_tier < to_skip:
                    to_skip -= in_tier
                    continue
            got = list(session.scalars(tier_ids.order_by(*order).limit(wanted).offset(to_skip)))
            page_ids += got
            wanted -= len(got)
            to_skip = 0
            if not wanted:
                break
    loaded = {
        j.id: j
        for j in session.scalars(
            select(Job).where(Job.id.in_(page_ids))
            # A list shows no descriptions; loading them read 50 multi-KB rows from disk per page.
            .options(defer(Job.description), selectinload(Job.company), selectinload(Job.places))
        )
    }
    tracked_apps = _applications_for(session, user, page_ids)
    kinds = _repeat_kinds(session, list(loaded.values()))
    # Saved scores only: jobs outside "For you" (searches, All jobs) may have none yet, and scoring them here meant
    # embedding up to 50 descriptions before the list could show (3-7 s). The Feed asks /api/matches for those.
    matches = stored_scores(session, [i for i in page_ids if i in loaded], user.id)
    items = [{**_job_out(loaded[i], kinds.get(i), tracked_apps.get(i)), "match": _match_brief(matches.get(i))}
             for i in page_ids if i in loaded]
    return {"total": min(total, COUNT_CAP), "total_capped": total > COUNT_CAP, "related_terms": related_terms, "items": items}


@app.get("/api/jobs/{job_id}", response_model=JobDetailOut)
def get_job(job_id: int, session: Session = Depends(get_session), user: User = Depends(current_user)):
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    data = _job_out(job, _repeat_kinds(session, [job]).get(job.id), _applications_for(session, user, [job.id]).get(job.id))
    data["description"] = job.description
    data["events"] = [{"kind": e.kind, "at": e.at, "detail": e.detail} for e in job.events]
    original = session.get(Job, job.repost_of_id) if job.repost_of_id else None
    data["repost_of"] = {
        "id": original.id, "title": original.title, "company": original.company_name or original.company.name,
        "url": original.url, "first_seen_at": original.first_seen_at, "closed_at": original.closed_at,
    } if original else None
    data["sponsor"] = _sponsor_info(session, job.company_name or job.company.name)
    data["h1b_roles"] = role_history(session, job.company_name or job.company.name, job.title)
    data["everify_info"] = everify_lookup(session, job.company_name or job.company.name)
    data["uscis"] = uscis_lookup(session, job.company_name or job.company.name)
    data["hiring_posts_url"] = hiring_posts_url(job.company_name or job.company.name, job.title)
    full = ensure_scores(session, [job.id], user.id).get(job.id)
    data["match"] = full.detail if full else None
    return data


def _sponsor_info(session: Session, company: str) -> dict | None:
    matches = sorted(sponsor_index(session).matches(company), key=lambda m: -m[1])
    if not matches:
        return None
    rows = {s.name_key: s for s in session.scalars(select(H1BSponsor).where(H1BSponsor.name_key.in_([k for k, _ in matches])))}
    biggest = rows.get(matches[0][0])
    return {
        "filings": sum(n for _, n in matches),
        "employers": [rows[k].name for k, _ in matches[:5] if k in rows],
        "top_titles": biggest.top_titles if biggest else [],
        "source": biggest.source if biggest else "",
    }


def _job_or_404(session: Session, job_id: int) -> Job:
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return job


def _job_url(job_id: int) -> str:
    with SessionLocal() as session:
        return _job_or_404(session, job_id).url


def _record_check(job_id: int, is_open: bool | None, why: str) -> dict:
    now = datetime.now(timezone.utc)
    with SessionLocal() as session:
        job = _job_or_404(session, job_id)
        job.checked_at = now
        newly_closed = is_open is False and job.closed_at is None
        if newly_closed:
            liveness.close(session, job, why, now)
        session.commit()
        if newly_closed:
            _jobs_cache.clear()  # cached pages still list it: the next feed request must not
        return {"open": is_open, "detail": why, "checked_at": now, "closed_at": job.closed_at}


@app.post("/api/jobs/{job_id}/verify")
async def verify_job(job_id: int):
    """Open the job's apply page now and close the job if it's gone (pipeline/liveness.py).

    The database work runs in worker threads: on the event loop, its commit waited for the write lock
    (up to the 120 s busy timeout while a poll saved) and froze every other request, the whole site with it.
    """
    url = await asyncio.to_thread(_job_url, job_id)
    async with httpx.AsyncClient(timeout=15, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
        is_open, why = await liveness.check(client, url)
    return await asyncio.to_thread(_record_check, job_id, is_open, why)


def _company_and_title(job_id: int) -> tuple[str, str]:
    with SessionLocal() as session:
        job = _job_or_404(session, job_id)
        return job.company_name or job.company.name, job.title


def _description_state(job_id: int) -> tuple[str, bool]:
    with SessionLocal() as session:
        job = _job_or_404(session, job_id)
        return job.url or "", len((job.description or "").strip()) >= MIN_DESCRIPTION_CHARS


def _save_description(job_id: int, text: str) -> dict:
    with SessionLocal() as session:
        job = _job_or_404(session, job_id)
        if len(text.strip()) > len((job.description or "").strip()):
            job.description = text
            with session.no_autoflush:
                apply_flags(session, job, get_prefs(session))  # sponsorship, OPT, E-Verify, experience
            session.commit()
        return {"description": job.description, "fetched": True}


@app.post("/api/jobs/{job_id}/describe")
async def describe_job(job_id: int):
    """Fetch a job's description from its source now, when it was saved without one (pipeline/describe.py).
    The Feed calls it on opening such a job; database work runs in threads (see verify_job)."""
    url, has_one = await asyncio.to_thread(_description_state, job_id)
    if has_one:
        return {"description": None, "fetched": False}
    async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
        text = await fetch_description(client, url)
    if not text:
        return {"description": None, "fetched": False}
    return await asyncio.to_thread(_save_description, job_id, text)


HUNTER_LOOKUPS_PER_DAY = 10  # per user (the admin: no limit); each spends the admin's Hunter.io credits


def _hunter_lookups_today(user_id: int) -> int:
    from app.models import Activity

    since = datetime.now(timezone.utc) - timedelta(hours=24)
    with SessionLocal() as session:
        return session.scalar(select(func.count()).select_from(Activity).where(
            Activity.user_id == user_id, Activity.kind == "recruiter_lookup", Activity.at >= since)) or 0


@app.get("/api/jobs/{job_id}/recruiters")
async def job_recruiters(job_id: int, lookup: bool = False, refresh: bool = False, user: User = Depends(current_user)):
    """Search links for the job's recruiters; with lookup=true, also Hunter.io emails (uses quota, cached)."""
    company, title = await asyncio.to_thread(_company_and_title, job_id)  # off the event loop (see verify_job)
    hunter = None
    if lookup:  # a click on "Look up", rare: its cache reads and writes stay inline
        if not user.is_admin and await asyncio.to_thread(_hunter_lookups_today, user.id) >= HUNTER_LOOKUPS_PER_DAY:
            raise HTTPException(429, f"You've used today's {HUNTER_LOOKUPS_PER_DAY} recruiter email lookups. "
                                     "The search links above still work.")
        with SessionLocal() as session:
            hunter = await hunter_lookup(session, company, refresh)
        if hunter and not hunter.get("cached") and not hunter.get("error"):  # only real, paid lookups
            await asyncio.to_thread(record, "recruiter_lookup", user.id, company[:200])
    return {
        "company": company,
        "links": search_links(company, title),
        "hunter_configured": hunter_configured(),
        "hunter": hunter,
    }


FACETS_MAX_AGE = timedelta(minutes=10)
_facets: dict = {"at": None, "rows": []}
_facets_lock = threading.Lock()


def _place_counts(force: bool = False) -> list[tuple]:
    """(country, state, city, open jobs) for every place, from a snapshot at most FACETS_MAX_AGE old.

    One grouped pass over the covering indexes (~3.5 s for 800k jobs) instead of a COUNT(DISTINCT) per
    request, which took 65 s and left the State and City pickers empty. Refreshed at startup and after
    each fast poll, so requests normally don't wait; while a refresh runs they get the previous snapshot.
    """
    at = _facets["at"]
    if at and not force and datetime.now(timezone.utc) - at < FACETS_MAX_AGE:
        return _facets["rows"]
    if not _facets_lock.acquire(blocking=at is None):  # only wait when there's nothing to serve yet
        return _facets["rows"]
    try:
        if _facets["at"] is at:  # nobody refreshed it while we waited for the lock
            with SessionLocal() as session:
                _facets["rows"] = session.execute(
                    select(JobLocation.country, JobLocation.state, JobLocation.city, func.count())
                    .join(Job, Job.id == JobLocation.job_id)
                    .where(Job.closed_at.is_(None), Job.is_repost.is_(False))
                    .group_by(JobLocation.country, JobLocation.state, JobLocation.city)
                ).all()
            _facets["at"] = datetime.now(timezone.utc)
    finally:
        _facets_lock.release()
    return _facets["rows"]


@app.get("/api/matches")
def job_matches(ids: list[int] = Query([]), session: Session = Depends(get_session), user: User = Depends(current_user)):
    """Match scores for jobs a list showed without one, scored now (at most 100): {job id: brief}. The Feed asks
    right after a page loads, so the jobs show at once and their scores fill in a moment later."""
    return {str(i): _match_brief(m) for i, m in ensure_scores(session, list(dict.fromkeys(ids))[:100], user.id).items()}


@app.get("/api/facets/locations")
def location_facets(country: str | None = None, state: str | None = None):
    """Countries, and the states/cities within a chosen country/state, with open-job counts.

    A job listing two cities counts once in each, and twice in their country.
    """
    countries: dict[str, int] = {}
    states: dict[str, int] = {}
    cities: dict[str, int] = {}
    for c, s, city, n in _place_counts():
        if c:
            countries[c] = countries.get(c, 0) + n
        if country and c == country:
            if s:
                states[s] = states.get(s, 0) + n
            if city and (not state or s == state):
                cities[city] = cities.get(city, 0) + n

    def ranked(counts: dict[str, int], limit: int | None = None) -> list[dict]:
        return [{"name": k, "count": v} for k, v in sorted(counts.items(), key=lambda kv: -kv[1])[:limit]]

    return {"countries": ranked(countries), "states": ranked(states), "cities": ranked(cities, 500)}


# ---------- application tracker ----------

def _application_out(a: Application) -> dict:
    data = {k: getattr(a, k) for k in ApplicationOut.model_fields if k != "job_closed"}
    data["job_closed"] = (a.job.closed_at is not None) if a.job else None
    return data


def _check_status(status: str | None) -> None:
    if status is not None and status not in APPLICATION_STATUSES:
        raise HTTPException(400, f"status must be one of {APPLICATION_STATUSES}")


@app.get("/api/applications", response_model=list[ApplicationOut])
def list_applications(session: Session = Depends(get_session), user: User = Depends(current_user)):
    apps = session.scalars(
        select(Application).where(Application.user_id == user.id).options(selectinload(Application.job))
        .order_by(func.coalesce(Application.applied_at, Application.created_at).desc())
    )
    return [_application_out(a) for a in apps]


@app.post("/api/applications", response_model=ApplicationOut, status_code=201)
def create_application(body: ApplicationIn, session: Session = Depends(get_session), user: User = Depends(current_user)):
    _check_status(body.status)
    now = datetime.now(timezone.utc)
    if body.job_id is not None:
        job = session.get(Job, body.job_id)
        if job is None:
            raise HTTPException(404, "job not found")
        if session.scalar(select(Application).where(Application.user_id == user.id, Application.job_id == job.id)):
            raise HTTPException(409, "already tracking this job")
        app_ = Application(job=job, company=job.company_name or job.company.name, title=job.title, url=job.url,
                           location=job.location)
    else:
        if not (body.company and body.title):
            raise HTTPException(400, "company and title are required for a job added by hand")
        app_ = Application(company=body.company, title=body.title, url=body.url, location=body.location)
    app_.user_id = user.id
    app_.status, app_.notes, app_.contact = body.status, body.notes, body.contact
    app_.applied_at = now if body.status != "saved" else None
    session.add(app_)
    session.commit()
    return _application_out(app_)


@app.patch("/api/applications/{app_id}", response_model=ApplicationOut)
def update_application(app_id: int, body: ApplicationPatch, session: Session = Depends(get_session),
                       user: User = Depends(current_user)):
    app_ = session.get(Application, app_id)
    if app_ is None or app_.user_id != user.id:
        raise HTTPException(404, "application not found")
    _check_status(body.status)
    changes = body.model_dump(exclude_unset=True)
    if changes.get("status") not in (None, "saved") and app_.applied_at is None and "applied_at" not in changes:
        changes["applied_at"] = datetime.now(timezone.utc)
    for k, v in changes.items():
        setattr(app_, k, v)
    session.commit()
    return _application_out(app_)


@app.delete("/api/applications/{app_id}", status_code=204)
def delete_application(app_id: int, session: Session = Depends(get_session), user: User = Depends(current_user)):
    app_ = session.get(Application, app_id)
    if app_ is None or app_.user_id != user.id:
        raise HTTPException(404, "application not found")
    session.delete(app_)
    session.commit()


# ---------- sources, prefs, status ----------

_open_counts: dict = {}  # "at" (monotonic seconds) and "counts" (company id -> open jobs)


def _open_jobs_per_company(session: Session) -> dict[int, int]:
    """Open jobs per source: counted at startup, then recounted in the background when 5 minutes old.

    Forced onto ix_jobs_company_described (company_id, described, closed_at), which covers it: left to
    itself SQLite took the open-jobs index, read company_id from every job's row and took 97 s, so the
    Sources page never loaded. With the covering index it's ~5 s cold and 0.2 s warm.
    """
    if _open_counts:
        if time.monotonic() - _open_counts["at"] >= 300 and _open_counts_lock.acquire(blocking=False):
            # Stale: answer with the old counts and recount in the background (5 s cold).
            threading.Thread(target=_recount_open_jobs, daemon=True).start()
        return _open_counts["counts"]
    return _count_open_jobs(session)


_open_counts_lock = threading.Lock()


def _recount_open_jobs() -> None:
    try:
        with SessionLocal() as session:
            _count_open_jobs(session)
    except Exception:
        log.warning("Counting open jobs per source failed", exc_info=True)
    finally:
        if _open_counts_lock.locked():
            _open_counts_lock.release()


def _count_open_jobs(session: Session) -> dict[int, int]:
    sql = ("SELECT company_id, count(*) FROM jobs INDEXED BY ix_jobs_company_described WHERE closed_at IS NULL GROUP BY company_id"
           if is_sqlite else "SELECT company_id, count(*) FROM jobs WHERE closed_at IS NULL GROUP BY company_id")
    _open_counts.update(at=time.monotonic(), counts=dict(session.execute(text(sql)).all()))
    return _open_counts["counts"]


@app.get("/api/companies", response_model=list[CompanyOut])
def list_companies(q: str = "", limit: int = Query(200, ge=1, le=50_000), session: Session = Depends(get_session)):
    """Sources with the most open jobs first; `q` filters by name, slug or ATS. All 25k sources at once was a
    4.7 MB answer and a table too long for the browser to draw quickly, so the Sources page asks for 200."""
    counts = _open_jobs_per_company(session)
    stmt = select(Company)
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Company.name.ilike(like), Company.slug.ilike(like), Company.ats == q.strip().lower()))
    rows = sorted(session.scalars(stmt), key=lambda c: (-counts.get(c.id, 0), c.name.lower()))
    return [_company_out(c, counts.get(c.id, 0)) for c in rows[:limit]]


@app.post("/api/companies", response_model=CompanyOut, status_code=201)
def add_company(body: CompanyIn, session: Session = Depends(get_session), _: User = Depends(admin_user)):
    if body.ats not in ADAPTERS:
        raise HTTPException(400, f"ats must be one of {sorted(ADAPTERS)}")
    slug = body.slug.strip()
    if session.scalar(select(Company).where(Company.ats == body.ats, Company.slug == slug)):
        raise HTTPException(409, "company already added")
    company = Company(name=body.name.strip(), ats=body.ats, slug=slug)
    session.add(company)
    session.commit()
    return _company_out(company, 0)


@app.post("/api/companies/from-url")
async def add_company_from_url(body: CareersUrlIn, session: Session = Depends(get_session), _: User = Depends(admin_user)):
    """Detect the job board behind any careers URL (or a company's own careers page) and start tracking it."""
    url = body.url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
        try:
            found = await detect_from_page(client, url)
            if found is None:
                # No ATS behind it: read the page itself, if it lists jobs we can parse (sources/website.py).
                try:
                    if await ADAPTERS["website"].fetch(client, url):
                        found = ("website", url)
                except (httpx.HTTPError, ValueError):
                    pass
        except httpx.HTTPError as e:
            raise HTTPException(400, f"Couldn't open that page ({type(e).__name__}).")
    if found is None:
        raise HTTPException(
            404, "No job board or job listings found on that page. Try the page that lists the jobs, or a single job's link."
        )
    ats, slug = found
    existing = session.scalar(select(Company).where(Company.ats == ats, Company.slug == slug))
    if existing:
        return {"added": False, "company": _company_out(existing, _open_jobs_per_company(session).get(existing.id, 0))}
    name = body.name.strip() or _guess_name(url, slug)
    company = Company(name=name, ats=ats, slug=slug)
    session.add(company)
    session.commit()
    return {"added": True, "company": _company_out(company, 0)}


def _guess_name(url: str, slug: str) -> str:
    host = httpx.URL(url).host.removeprefix("www.").removeprefix("careers.").removeprefix("jobs.")
    base = host.split(".")[0] if host and not any(a in host for a in ("greenhouse", "lever", "ashby", "workday", "workable",
                                                                       "smartrecruiters", "icims", "oraclecloud", "eightfold"))        else slug.split("|")[0]
    return base.replace("-", " ").title()


@app.patch("/api/companies/{company_id}", response_model=CompanyOut)
def update_company(company_id: int, body: CompanyPatch, session: Session = Depends(get_session), _: User = Depends(admin_user)):
    company = session.get(Company, company_id)
    if company is None:
        raise HTTPException(404, "company not found")
    company.enabled = body.enabled
    session.commit()
    open_jobs = session.scalar(select(func.count()).where(Job.company_id == company_id, Job.closed_at.is_(None)))
    return _company_out(company, open_jobs)


@app.get("/api/prefs", response_model=PrefsModel)
def read_prefs(session: Session = Depends(get_session), user: User = Depends(current_user)):
    return get_prefs(session, user.id)


@app.put("/api/prefs", response_model=PrefsModel)
def write_prefs(body: PrefsModel, session: Session = Depends(get_session), user: User = Depends(current_user)):
    prefs = get_prefs(session, user.id)
    # Job.role_match follows everyone's roles together (prefs.get_prefs with no user): compare that view.
    before = get_prefs(session)
    old_include, old_exclude = list(before.include_keywords), list(before.exclude_keywords)
    changes = body.model_dump()
    changes.pop("email_alerts", None)  # alerts are switched on in alert settings, which checks the email first
    for k, v in changes.items():
        setattr(prefs, k, v)
    session.commit()
    after = get_prefs(session)
    include, exclude = list(after.include_keywords), list(after.exclude_keywords)
    if (include, exclude) != (old_include, old_exclude):
        # Adding or removing a role (the "Your roles" bar) only changes jobs whose title mentions it: recheck
        # just those, right now, so "For you" is up to date when this returns. A changed exclude list can
        # change any job: recheck all ~1.7M titles in the background (about a minute).
        added_or_removed = sorted(set(include) ^ set(old_include))
        changed = refresh_role_match_for(added_or_removed, include, exclude) if exclude == old_exclude else None
        if changed is None:
            threading.Thread(target=refresh_role_match, args=(include, exclude), daemon=True).start()
        _jobs_cache.clear()  # pages cached while the roles were being rechecked
        _status_counts.clear()
    return prefs


_suggestion_cache: dict[tuple, tuple[datetime, list[dict]]] = {}
SUGGESTION_CAP = 2_000


@app.get("/api/roles/suggestions")
def role_suggestions(roles: list[str] = Query([]), session: Session = Depends(get_session)):
    """Related roles to offer next to the user's own (search's role families), with open US jobs for each.

    The user adds the ones they want; nothing is added for them. Cached 10 minutes per role list.
    """
    picked = {r.strip().lower() for r in roles if r.strip()}
    key = tuple(sorted(picked))
    hit = _suggestion_cache.get(key)
    if hit:
        if datetime.now(timezone.utc) - hit[0] >= timedelta(minutes=10) and key not in _suggestions_refreshing:
            # Stale: answer now with the old counts and recount in the background (a recount takes ~20 s).
            _suggestions_refreshing.add(key)
            threading.Thread(target=_refresh_suggestions, args=(list(picked),), daemon=True).start()
        return hit[1]
    return _compute_suggestions(session, picked)


_suggestions_refreshing: set[tuple] = set()


def _refresh_suggestions(roles: list[str]) -> None:
    key = tuple(sorted(roles))
    try:
        with SessionLocal() as session:
            _compute_suggestions(session, set(roles))
    except Exception:
        log.warning("Recounting role suggestions failed", exc_info=True)
    finally:
        _suggestions_refreshing.discard(key)


def _warm_suggestions() -> None:
    """Count the suggestions for the user's own roles ahead of time (startup, after each fast poll)."""
    with SessionLocal() as s:
        roles = [r.strip().lower() for r in get_prefs(s).include_keywords if r.strip()]
    if roles:
        _refresh_suggestions(roles)


def _compute_suggestions(session: Session, picked: set[str]) -> list[dict]:
    key = tuple(sorted(picked))
    candidates = distinct_roles([t for r in picked for t in expand_query(r)[1:]], list(picked))
    suggestions = []
    for term in list(dict.fromkeys(candidates))[:40]:
        match = fts_any_term([term]) if is_sqlite else None
        if not match:
            continue
        # Starts from the term's full-text matches (ix_jobs_id_feed) and stops at SUGGESTION_CAP ("2k+"):
        # 40 exact counts over the open-jobs index took 25 s.
        ids = (select(Job.id).where(text("+jobs.closed_at IS NULL"), text("+jobs.is_repost IS 0"), _fts("fts_s", match),
                                    exists().where(JobLocation.job_id == Job.id, JobLocation.country == "United States"))
               .limit(SUGGESTION_CAP + 1))
        n = session.scalar(select(func.count()).select_from(ids.subquery())) or 0
        if n:
            suggestions.append({"role": term, "open_jobs": n})
    suggestions.sort(key=lambda s: -s["open_jobs"])
    _suggestion_cache[key] = (datetime.now(timezone.utc), suggestions)
    return suggestions


class ResumeIn(BaseModel):
    filename: str
    data_base64: str  # the file's bytes (PDF, .docx or .txt), base64-encoded by the browser


class ProfileIn(BaseModel):
    skills: list[str] | None = None
    years: float | None = None
    degree: str | None = None
    target_roles: list[str] | None = None
    needs_sponsorship: bool | None = None
    visa_status: str | None = None
    opt_end: date | None = None
    locations: list[str] | None = None


def _profile_out(session: Session, profile: UserProfile | None, user: User) -> dict:
    prefs = get_prefs(session, user.id)
    if not profile or not profile.resume_text:
        return {"has_resume": False, "scoring": scoring.status(), "your_roles": prefs.include_keywords,
                "vocabulary": list(matching.SKILLS)}
    roles = {r.lower() for r in prefs.include_keywords}
    return {
        "has_resume": True, "filename": profile.filename, "uploaded_at": profile.uploaded_at,
        "skills": profile.skills, "years": profile.years, "degree": profile.degree,
        "target_roles": profile.target_roles, "needs_sponsorship": profile.needs_sponsorship,
        "visa_status": profile.visa_status, "opt_end": profile.opt_end, "locations": profile.locations,
        "suggested_roles": [r for r in profile.target_roles if r.lower() not in roles],
        "your_roles": prefs.include_keywords, "resume_preview": profile.resume_text[:1500],
        "scoring": scoring.status(), "version": profile.version, "vocabulary": list(matching.SKILLS),
        "text_warning": matching.spacing_warning(profile.resume_text),  # words run together in the file
        "scored": session.scalar(select(func.count()).select_from(JobMatch).where(
            JobMatch.user_id == user.id, JobMatch.profile_version == profile.version)),
    }


@app.get("/api/profile")
def read_profile(request: Request, session: Session = Depends(get_session), user: User = Depends(current_user)):
    from app.auth import viewing_as

    out = _profile_out(session, get_profile(session, user.id), user)
    if viewing_as(request) and "resume_preview" in out:
        out["resume_preview"] = ""  # the admin viewing as the user never sees the resume text
        out["viewing_as"] = True
    return out


@app.post("/api/profile/resume")
def upload_resume(body: ResumeIn, session: Session = Depends(get_session), user: User = Depends(current_user)):
    """Read a resume, extract its skills, years, degree and roles, embed it, and rescore the feed."""
    try:
        data = base64.b64decode(body.data_base64, validate=True)
    except ValueError:
        raise HTTPException(400, "The file couldn't be read.")
    if len(data) > 5_000_000:
        raise HTTPException(400, "The file is over 5 MB.")
    try:
        text_ = matching.respace(matching.resume_text(body.filename, data))  # some PDFs run words together
    except Exception:
        raise HTTPException(400, "The file couldn't be read. Try a PDF, a .docx or plain text.")
    if len(text_.strip()) < 200:
        raise HTTPException(400, "Very little text came out of that file (a scanned image?). Try another PDF or paste the text as a .txt file.")
    profile = get_profile(session, user.id) or UserProfile(user_id=user.id, version=0)
    session.add(profile)
    profile.filename = body.filename[:300]
    profile.uploaded_at = datetime.now(timezone.utc)
    profile.resume_text = text_
    profile.resume_file = data
    profile.skills = matching.extract_skills(text_)
    profile.years = matching.resume_years(text_)
    profile.degree = matching.resume_degree(text_)
    profile.target_roles = matching.resume_roles(text_)
    profile.vector = matching.embed([matching.resume_for_embedding(text_)])[0].tobytes()
    profile.version = (profile.version or 0) + 1
    session.commit()
    record("resume_uploaded", user.id, f"{body.filename[-80:]} ({len(data) // 1024} KB, {len(profile.skills)} skills)")
    scoring.refresh_in_background()
    return _profile_out(session, profile, user)


@app.put("/api/profile")
def update_profile(body: ProfileIn, session: Session = Depends(get_session), user: User = Depends(current_user)):
    """Correct what was read from the resume, or set visa status and places; the feed is rescored."""
    profile = get_profile(session, user.id)
    if not profile or not profile.resume_text:
        raise HTTPException(400, "Upload a resume first.")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(profile, field, value)
    profile.version += 1
    session.commit()
    scoring.refresh_in_background()
    return _profile_out(session, profile, user)


@app.delete("/api/profile/resume")
def delete_resume(session: Session = Depends(get_session), user: User = Depends(current_user)):
    """Remove the resume and everything read from it (the scores go with it)."""
    profile = get_profile(session, user.id)
    if profile:
        profile.resume_text, profile.filename, profile.vector, profile.resume_file = "", "", None, None
        profile.skills, profile.target_roles, profile.years, profile.degree = [], [], None, ""
        profile.version += 1
        session.execute(text("DELETE FROM user_job_matches WHERE user_id = :u"), {"u": user.id})
        session.commit()
        record("resume_deleted", user.id)
    return _profile_out(session, profile, user)


# ---------- alerts ----------

def _alert_settings_out(user: User, prefs) -> dict:
    return {
        "email": user.email, "email_verified": user.email_verified_at is not None, "email_mode": email_mode(),
        "email_alerts": prefs.email_alerts, "alert_frequency": prefs.alert_frequency or "instant",
        "alert_min_score": prefs.alert_min_score, "alert_daily_cap": prefs.alert_daily_cap or 10,
        "hide_no_sponsor": prefs.hide_no_sponsor, "exclude_senior": prefs.exclude_senior,
        "roles": prefs.include_keywords, "last_alert_at": prefs.last_alert_at,
    }


@app.get("/api/alerts/settings")
def read_alert_settings(session: Session = Depends(get_session), user: User = Depends(current_user)):
    return _alert_settings_out(user, get_prefs(session, user.id))


@app.put("/api/alerts/settings")
def write_alert_settings(body: AlertSettingsIn, session: Session = Depends(get_session), user: User = Depends(current_user)):
    """Alert settings. Switching alerts on needs a confirmed email; it starts from now (no backlog) and sends a
    confirmation email saying what the user will get."""
    from app.notify.email import send_alerts_on

    prefs = get_prefs(session, user.id)
    changes = body.model_dump(exclude_unset=True)
    switching_on = changes.get("email_alerts") is True and not prefs.email_alerts
    if switching_on and user.email_verified_at is None:
        raise HTTPException(400, "Confirm your email address first: we sent you a link when you signed up.")
    if "alert_min_score" in changes and changes["alert_min_score"] == 0:
        changes["alert_min_score"] = None
    for k, v in changes.items():
        if v is not None or k == "alert_min_score":
            setattr(prefs, k, v)
    if switching_on:
        prefs.alert_watermark = datetime.now(timezone.utc)
        prefs.last_alert_at = None
    switching_off = changes.get("email_alerts") is False
    session.commit()
    if switching_on:
        record("alerts_on", user.id, f"{prefs.alert_frequency}, cap {prefs.alert_daily_cap}"
               + (f", min match {prefs.alert_min_score}" if prefs.alert_min_score else ""))
    elif switching_off:
        record("alerts_off", user.id, "in My profile")
    out = _alert_settings_out(user, prefs)
    if switching_on:
        out["confirmation_sent"] = send_alerts_on(user, prefs)
    return out


@app.get("/api/alerts/preview", response_class=Response)
def preview_alert(session: Session = Depends(get_session), user: User = Depends(current_user)):
    """The alert email this user would get now, built from their matching jobs of the last 24 hours (HTML)."""
    from app.notify.email import build_alert
    from app.pipeline.alerts import jobs_for

    prefs = get_prefs(session, user.id)
    now = datetime.now(timezone.utc)
    real = prefs.alert_watermark
    prefs.alert_watermark = now - timedelta(hours=24)  # just for this preview; not saved
    jobs = jobs_for(session, user, prefs, now)[:12]
    prefs.alert_watermark = real
    session.expire(prefs)
    if not jobs:
        return Response("<p style='font-family:sans-serif'>No new matching jobs in the last 24 hours to preview.</p>",
                        media_type="text/html")
    matches = {i: _match_brief(m) for i, m in ensure_scores(session, [j.id for j in jobs], user.id).items()}
    return Response(build_alert(user, jobs, matches, now)[2], media_type="text/html")


@app.post("/api/alerts/test")
def test_alert_email(user: User = Depends(current_user)):
    """Send a test email to the user's own address, so they can confirm email reaches their inbox."""
    sent, detail = send_test(user.email)
    return {"sent": sent, "detail": detail, "mode": email_mode()}


@app.post("/api/poll")
def trigger_poll(_: User = Depends(admin_user)):
    # "Check now" refreshes the fast lane; the slow lane keeps its own schedule.
    if settings.poll_workers and settings.scheduler_enabled:
        from app import lanes

        lanes.request_poll("fast")  # the fast worker starts it within a few seconds
    else:
        threading.Thread(target=run_poll, args=["fast"], daemon=True).start()
    return {"started": True}


_status_counts: dict = {}  # "at" (monotonic seconds) and the counts


_counts_lock = threading.Lock()


def _recount(session: Session) -> None:
    day_ago = datetime.now(timezone.utc) - timedelta(hours=24)
    fresh = dict(
        at=time.monotonic(),
        companies=session.scalar(select(func.count()).select_from(Company)),
        open_jobs=session.scalar(select(func.count()).where(Job.closed_at.is_(None), Job.is_repost.is_(False))),
        new_last_24h=session.scalar(
            select(func.count()).where(Job.baseline.is_(False), Job.is_repost.is_(False), Job.first_seen_at >= day_ago)
        ),
        h1b_sponsors=session.scalar(select(func.count()).select_from(H1BSponsor)),
    )
    _status_counts.update(fresh)


def _recount_in_background() -> None:
    try:
        with SessionLocal() as s:
            _recount(s)
    except Exception:
        log.warning("status recount failed", exc_info=True)
    finally:
        _counts_lock.release()


def _counts(session: Session) -> dict:
    """The header's totals, recounted at most once a minute. Counting 1.8M jobs took over 10 s while polls
    loaded the disk (2026-11-10), and the header asks every 10 s: so the last counts are served at once and
    recounted in the background. Only the very first request (nothing counted yet) waits."""
    if not _status_counts:
        with _counts_lock:
            if not _status_counts:
                _recount(session)
        return _status_counts
    if time.monotonic() - _status_counts["at"] >= 60 and _counts_lock.acquire(blocking=False):
        threading.Thread(target=_recount_in_background, daemon=True).start()
    return _status_counts


@app.get("/api/status")
def status(request: Request, session: Session = Depends(get_session)):
    from app.auth import user_from_request

    counts = _counts(session)
    user = user_from_request(request, session)
    return {
        "polling": _lane_busy("fast"),
        "slow_polling": _lane_busy("slow"),
        "poll_minutes": settings.poll_minutes,
        "last_poll": _last_poll.get("fast"),
        "last_slow_poll": _last_poll.get("slow"),
        "email_configured": email_configured(),
        "email_mode": email_mode(),  # "off", "test" (local test server, nothing delivered) or "on"
        "hunter_configured": hunter_configured(),
        "adzuna_configured": "adzuna" in ADAPTERS and ADAPTERS["adzuna"].is_configured(),
        "companies": counts["companies"],
        "open_jobs": counts["open_jobs"],
        "new_last_24h": counts["new_last_24h"],
        "tracked": session.scalar(select(func.count()).select_from(Application).where(Application.user_id == user.id)) if user else 0,
        "h1b_sponsors": counts["h1b_sponsors"],
        "jsearch_configured": ADAPTERS["jsearch"].is_configured() if "jsearch" in ADAPTERS else False,
        "discovery": {"running": _lane_busy("discovery"), **_discovery_status()},
    }


def _discovery_status() -> dict:
    from app.pipeline import autodiscover

    s = autodiscover.status()
    return {k: s.get(k) for k in ("last_run", "total_added", "total_looked_for")}
