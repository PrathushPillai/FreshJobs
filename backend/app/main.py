import asyncio
import logging
import re

import httpx
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Literal

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import and_, case, exists, func, or_, select, text, true
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.db import SessionLocal, engine, get_session, init_db, is_sqlite
from app.h1b import sponsor_index
from app.models import APPLICATION_STATUSES, Application, Company, H1BSponsor, Job, JobLocation
from app.notify.email import email_configured
from app.pipeline.detect import detect_from_page
from app.pipeline import liveness
from app.pipeline.poller import USER_AGENT, backfill, poll_once, refresh_role_match
from app.pipeline.search import distinct_roles, expand_query, fts_all_words, fts_any_term, fts_company
from app.prefs import get_prefs, sync_companies
from app.recruiters import hunter_configured, hunter_lookup, search_links
from app.sources import ADAPTERS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger(__name__)

# Fast boards (Greenhouse, Lever, Ashby, aggregators) and slow ones (Workday, SmartRecruiters: a request
# per job) poll in separate lanes with their own locks, so a long Workday scan never delays the fast boards.
LANES = ("fast", "slow", "bulk")  # bulk: rate-limited ATSs with thousands of boards (Workable)
_poll_locks = {lane: threading.Lock() for lane in LANES}
_last_poll: dict[str, dict] = {}


COUNT_CAP = 10_000


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
        if lane == "fast":
            _place_counts(force=True)  # location picker counts include this poll's jobs
        return summary
    except Exception:
        log.exception("Poll of %s lane failed", lane)
        return {"error": "poll failed, see server logs"}
    finally:
        lock.release()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    with SessionLocal() as session:
        sync_companies(session, settings.companies_file)
    backfill()
    scheduler = BackgroundScheduler()
    if settings.scheduler_enabled:
        threading.Thread(target=_place_counts, daemon=True).start()  # location picker counts, off the startup path
        with SessionLocal() as s:
            p = get_prefs(s)
            include, exclude = list(p.include_keywords), list(p.exclude_keywords)
        threading.Thread(target=refresh_role_match, args=(include, exclude), daemon=True).start()  # rules may have changed
        first_run = datetime.now() if settings.poll_on_startup else None
        for lane in LANES:
            scheduler.add_job(run_poll, "interval", args=[lane], minutes=settings.poll_minutes,
                              next_run_time=first_run, max_instances=1, coalesce=True)
        scheduler.start()
    yield
    if scheduler.running:
        scheduler.shutdown(wait=False)


app = FastAPI(title="Job Portal", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"])


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
    no_sponsor: bool
    seniority: str
    min_years: int | None
    exp_level: str
    baseline: bool
    h1b_filings: int | None
    application: TrackedOut | None


class EventOut(BaseModel):
    kind: str
    at: datetime
    detail: str


class SponsorOut(BaseModel):
    filings: int
    employers: list[str]  # DOL employer names that matched, biggest first
    top_titles: list[str]
    source: str


class JobDetailOut(JobOut):
    description: str
    events: list[EventOut]
    sponsor: SponsorOut | None


class JobPage(BaseModel):
    total: int
    total_capped: bool = False  # more than COUNT_CAP matches; total is COUNT_CAP
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
    email_alerts: bool
    hide_no_sponsor: bool
    exclude_senior: bool


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


def _job_out(job: Job) -> dict:
    data = {k: getattr(job, k) for k in JobOut.model_fields if k not in ("company", "source", "places", "application")}
    data["company"] = job.company_name or job.company.name
    data["source"] = job.company.ats
    data["exp_level"] = job.exp_level or "unknown"
    data["places"] = [{"city": p.city, "state": p.state, "country": p.country} for p in job.places]
    data["application"] = {"id": job.application.id, "status": job.application.status} if job.application else None
    return data


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
    return and_(text(f"jobs.id IN (SELECT rowid FROM jobs_fts WHERE jobs_fts MATCH :{name})").bindparams(**{name: match}))


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
    q: str = "",
    related: bool = True,
    also: list[str] = Query([]),
    skip: list[str] = Query([]),
    sort: Literal["newest", "posted", "company", "sponsor"] = "newest",
    status: Literal["open", "closed", "all"] = "open",
    countries: list[str] = Query([]),
    states: list[str] = Query([]),
    cities: list[str] = Query([]),
    include_unplaced_remote: bool = True,
    exp_levels: list[str] = Query([]),
    max_years: int | None = None,
    remote_only: bool = False,
    sponsors_only: bool = False,
    hide_no_sponsor: bool = True,
    show_reposts: bool = False,
    alert_roles_only: bool = False,
    tracked: Literal["any", "tracked", "untracked"] = "any",
    since_hours: float | None = None,
    posted_within_hours: float | None = None,
    company_id: int | None = None,
    limit: int = Query(50, le=200),
    offset: int = 0,
):
    # Title search first: how many jobs it matches decides the query plan (below).
    related_terms: list[str] = []
    relevance = search_clause = None
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
        relevance = case((phrase, 0), (typed, 1), else_=2)

    def flag(column: str, clause):
        """A status filter; written as "+jobs.col" when the plan starts from the search matches, so SQLite
        doesn't pick the open-jobs index for it instead."""
        return text(f"+jobs.{column}") if fts_first else clause

    stmt = select(Job)
    if status == "open":
        stmt = stmt.where(text("+jobs.closed_at IS NULL") if fts_first else Job.closed_at.is_(None))
    elif status == "closed":
        stmt = stmt.where(Job.closed_at.is_not(None))
    if not show_reposts:
        stmt = stmt.where(flag("is_repost IS 0", Job.is_repost.is_(False)))
    if hide_no_sponsor:
        stmt = stmt.where(flag("no_sponsor IS 0", Job.no_sponsor.is_(False)))
    if alert_roles_only:
        stmt = stmt.where(Job.role_match.is_(True))
    if remote_only:
        stmt = stmt.where(Job.remote.is_(True))
    if sponsors_only:
        stmt = stmt.where(Job.h1b_filings > 0)
    if since_hours:
        stmt = stmt.where(Job.first_seen_at >= datetime.now(timezone.utc) - timedelta(hours=since_hours))
    if posted_within_hours:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=posted_within_hours)
        stmt = stmt.where(Job.listed_at >= cutoff)  # undated jobs can't qualify
    if company_id:
        stmt = stmt.where(Job.company_id == company_id)

    if search_clause is not None:
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
        has_app = exists().where(Application.job_id == Job.id)
        stmt = stmt.where(has_app if tracked == "tracked" else ~has_app)

    # Count and sort on IDs only, so SQLite answers from ix_jobs_feed without reading the wide rows
    # (descriptions), then load just the page's jobs. Selecting full rows made a search take ~6 s.
    ids = stmt.with_only_columns(Job.id)
    # Counting every match of a broad query (800k open jobs) takes seconds, so stop at COUNT_CAP ("10,000+").
    total = session.scalar(select(func.count()).select_from(ids.limit(COUNT_CAP + 1).subquery()))
    order = {
        "newest": (Job.first_seen_at.desc(), Job.id.desc()),
        # listed_at is set whenever the source gives a date; undated jobs (NULL) sort last in DESC order.
        "posted": (Job.listed_at.desc(), Job.id.desc()),
        "company": (Job.company_name, Job.first_seen_at.desc()),
        "sponsor": (func.coalesce(Job.h1b_filings, 0).desc(), Job.first_seen_at.desc()),
    }[sort]
    if relevance is not None:
        order = (relevance, *order)
    page_ids = list(session.scalars(ids.order_by(*order).limit(limit).offset(offset)))
    loaded = {
        j.id: j
        for j in session.scalars(
            select(Job).where(Job.id.in_(page_ids))
            .options(selectinload(Job.company), selectinload(Job.places), selectinload(Job.application))
        )
    }
    items = [_job_out(loaded[i]) for i in page_ids if i in loaded]
    return {"total": min(total, COUNT_CAP), "total_capped": total > COUNT_CAP, "related_terms": related_terms, "items": items}


@app.get("/api/jobs/{job_id}", response_model=JobDetailOut)
def get_job(job_id: int, session: Session = Depends(get_session)):
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    data = _job_out(job)
    data["description"] = job.description
    data["events"] = [{"kind": e.kind, "at": e.at, "detail": e.detail} for e in job.events]
    data["sponsor"] = _sponsor_info(session, job.company_name or job.company.name)
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


@app.post("/api/jobs/{job_id}/verify")
async def verify_job(job_id: int, session: Session = Depends(get_session)):
    """Open the job's apply page now and close the job if it's gone (pipeline/liveness.py)."""
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    async with httpx.AsyncClient(timeout=15, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
        is_open, why = await liveness.check(client, job.url)
    now = datetime.now(timezone.utc)
    job.checked_at = now
    if is_open is False and job.closed_at is None:
        liveness.close(session, job, why, now)
    session.commit()
    return {"open": is_open, "detail": why, "checked_at": now, "closed_at": job.closed_at}


@app.get("/api/jobs/{job_id}/recruiters")
async def job_recruiters(job_id: int, lookup: bool = False, refresh: bool = False, session: Session = Depends(get_session)):
    """Search links for the job's recruiters; with lookup=true, also Hunter.io emails (uses quota, cached)."""
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    company = job.company_name or job.company.name
    return {
        "company": company,
        "links": search_links(company, job.title),
        "hunter_configured": hunter_configured(),
        "hunter": await hunter_lookup(session, company, refresh) if lookup else None,
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
def list_applications(session: Session = Depends(get_session)):
    apps = session.scalars(
        select(Application).options(selectinload(Application.job))
        .order_by(func.coalesce(Application.applied_at, Application.created_at).desc())
    )
    return [_application_out(a) for a in apps]


@app.post("/api/applications", response_model=ApplicationOut, status_code=201)
def create_application(body: ApplicationIn, session: Session = Depends(get_session)):
    _check_status(body.status)
    now = datetime.now(timezone.utc)
    if body.job_id is not None:
        job = session.get(Job, body.job_id)
        if job is None:
            raise HTTPException(404, "job not found")
        if session.scalar(select(Application).where(Application.job_id == job.id)):
            raise HTTPException(409, "already tracking this job")
        app_ = Application(job=job, company=job.company_name or job.company.name, title=job.title, url=job.url,
                           location=job.location)
    else:
        if not (body.company and body.title):
            raise HTTPException(400, "company and title are required for a job added by hand")
        app_ = Application(company=body.company, title=body.title, url=body.url, location=body.location)
    app_.status, app_.notes, app_.contact = body.status, body.notes, body.contact
    app_.applied_at = now if body.status != "saved" else None
    session.add(app_)
    session.commit()
    return _application_out(app_)


@app.patch("/api/applications/{app_id}", response_model=ApplicationOut)
def update_application(app_id: int, body: ApplicationPatch, session: Session = Depends(get_session)):
    app_ = session.get(Application, app_id)
    if app_ is None:
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
def delete_application(app_id: int, session: Session = Depends(get_session)):
    app_ = session.get(Application, app_id)
    if app_ is None:
        raise HTTPException(404, "application not found")
    session.delete(app_)
    session.commit()


# ---------- sources, prefs, status ----------

@app.get("/api/companies", response_model=list[CompanyOut])
def list_companies(session: Session = Depends(get_session)):
    counts = dict(
        session.execute(select(Job.company_id, func.count()).where(Job.closed_at.is_(None)).group_by(Job.company_id)).all()
    )
    return [_company_out(c, counts.get(c.id, 0)) for c in session.scalars(select(Company).order_by(Company.name))]


@app.post("/api/companies", response_model=CompanyOut, status_code=201)
def add_company(body: CompanyIn, session: Session = Depends(get_session)):
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
async def add_company_from_url(body: CareersUrlIn, session: Session = Depends(get_session)):
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
        return {"added": False, "company": _company_out(existing, 0)}
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
def update_company(company_id: int, body: CompanyPatch, session: Session = Depends(get_session)):
    company = session.get(Company, company_id)
    if company is None:
        raise HTTPException(404, "company not found")
    company.enabled = body.enabled
    session.commit()
    open_jobs = session.scalar(select(func.count()).where(Job.company_id == company_id, Job.closed_at.is_(None)))
    return _company_out(company, open_jobs)


@app.get("/api/prefs", response_model=PrefsModel)
def read_prefs(session: Session = Depends(get_session)):
    return get_prefs(session)


@app.put("/api/prefs", response_model=PrefsModel)
def write_prefs(body: PrefsModel, session: Session = Depends(get_session)):
    prefs = get_prefs(session)
    keywords_changed = (body.include_keywords, body.exclude_keywords) != (prefs.include_keywords, prefs.exclude_keywords)
    for k, v in body.model_dump().items():
        setattr(prefs, k, v)
    session.commit()
    if keywords_changed:
        # Rechecking ~1.2M titles takes a minute or more: in the background, so saving is instant.
        # Until it finishes, "For you" shows the old matches.
        threading.Thread(target=refresh_role_match, args=(list(prefs.include_keywords), list(prefs.exclude_keywords)),
                         daemon=True).start()
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
    if hit and datetime.now(timezone.utc) - hit[0] < timedelta(minutes=10):
        return hit[1]
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


@app.post("/api/poll")
def trigger_poll():
    # "Check now" refreshes the fast lane; the slow lane keeps its own schedule.
    threading.Thread(target=run_poll, args=["fast"], daemon=True).start()
    return {"started": True}


@app.get("/api/status")
def status(session: Session = Depends(get_session)):
    day_ago = datetime.now(timezone.utc) - timedelta(hours=24)
    return {
        "polling": _poll_locks["fast"].locked(),
        "slow_polling": _poll_locks["slow"].locked(),
        "poll_minutes": settings.poll_minutes,
        "last_poll": _last_poll.get("fast"),
        "last_slow_poll": _last_poll.get("slow"),
        "email_configured": email_configured(),
        "hunter_configured": hunter_configured(),
        "adzuna_configured": "adzuna" in ADAPTERS and ADAPTERS["adzuna"].is_configured(),
        "companies": session.scalar(select(func.count()).select_from(Company)),
        "open_jobs": session.scalar(select(func.count()).where(Job.closed_at.is_(None), Job.is_repost.is_(False))),
        "new_last_24h": session.scalar(
            select(func.count()).where(Job.baseline.is_(False), Job.is_repost.is_(False), Job.first_seen_at >= day_ago)
        ),
        "tracked": session.scalar(select(func.count()).select_from(Application)),
        "h1b_sponsors": session.scalar(select(func.count()).select_from(H1BSponsor)),
        "jsearch_configured": ADAPTERS["jsearch"].is_configured() if "jsearch" in ADAPTERS else False,
    }
