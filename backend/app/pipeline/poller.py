"""Fetch every source, record new/closed/reposted jobs, and send alerts.

Run one poll by hand:  python -m app.pipeline.poller --once
"""

import argparse
import asyncio
import logging
import re
import socket
import time
from collections import deque
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import or_, select, text, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.orm.util import identity_key

from app.config import settings
from app.db import SessionLocal, init_db
from app.h1b import sponsor_index
from app.models import Company, Job, JobEvent, JobLocation, UserPrefs
from app.pipeline.dedupe import find_original, fingerprint
from app.pipeline.discovery import discover
from app.pipeline.harvest import harvest
from app.pipeline.liveness import verify_due
from app.pipeline.startup_lists import maybe_sync_lists
from app.pipeline.startups import maybe_sync_yc
from app.pipeline.filters import is_us_location, role_matches
from app.pipeline.locations import parse_locations
from app.pipeline.normalize import company_key, experience_level, experience_years, listed_window, seniority
from app.everify import status_for as everify_status
from app.pipeline.sponsorship import find_no_sponsor_phrase, mentions_everify, says_sponsors, student_visa_signal
from app.prefs import get_prefs, sync_companies
from app.sources import ADAPTERS, NormalizedJob

log = logging.getLogger(__name__)

USER_AGENT = "job-portal-personal/0.1 (personal job search tool)"
# How stale a still-listed job's last_seen_at may get before it is rewritten (see process_company).
LAST_SEEN_RESOLUTION = timedelta(hours=6)


def apply_flags(session: Session, job: Job, prefs: UserPrefs) -> None:
    """Compute every derived field of a job from its raw data."""
    # A blank line (Lever) or a stub ("Category: … Terms: …" from the SimplifyJobs list) isn't a description.
    job.described = len((job.description or "").strip()) >= MIN_DESCRIPTION_CHARS
    job.company_key = company_key(job.company_name or "")
    job.fingerprint = fingerprint(job.company_key, job.title, job.location)
    job.seniority = seniority(job.title)
    job.min_years = experience_years(job.description)
    job.exp_level = experience_level(job.min_years, job.seniority)
    job.role_match = role_matches(job.title, prefs.include_keywords, prefs.exclude_keywords)
    job.us_eligible = is_us_location(job.location, job.remote)
    job.student_visa = student_visa_signal(job.description)
    # Ruling out OPT/CPT ("not eligible for visa sponsorship (H1B, OPT/CPT)") rules out an F-1 student too.
    job.no_sponsor = find_no_sponsor_phrase(job.description) is not None or job.student_visa == "excluded"
    job.everify_text = mentions_everify(job.description)
    job.sponsors_text = not job.no_sponsor and says_sponsors(job.description)
    job.everify = everify_status(session, job.company_name or "")
    job.h1b_filings = sponsor_index(session).filings(job.company_name or "")
    job.places = [JobLocation(city=p.city, state=p.state, country=p.country) for p in parse_locations(job.location)]
    job.listed_at, job.listed_until = listed_window(job.posted_at, job.first_seen_at, job.baseline, job.listed_earliest)


def refresh_role_match(include: list[str], exclude: list[str], batch: int = 50_000) -> int:
    """Recompute role_match for every job from its title; returns how many changed.

    Reads only (id, title, role_match) and writes only the rows whose answer changes, in batches, so it
    takes seconds over a million jobs. Loading whole Job rows (with descriptions) took minutes and held
    the write lock. Runs at startup (the matching rules may have changed) and when the keywords do.
    """
    changed, last_id = 0, 0
    verdicts: dict[str, bool] = {}  # many jobs share a title: match each distinct title once
    while True:
        with SessionLocal() as session:
            rows = session.execute(
                select(Job.id, Job.title, Job.role_match).where(Job.id > last_id).order_by(Job.id).limit(batch)).all()
            if not rows:
                return changed
            last_id = rows[-1][0]
            updates = []
            for i, t, old in rows:
                m = verdicts.get(t or "")
                if m is None:
                    m = verdicts[t or ""] = role_matches(t or "", include, exclude)
                if m != bool(old):
                    updates.append({"i": i, "m": m})
            if updates:
                session.execute(text("UPDATE jobs SET role_match = :m WHERE id = :i"), updates)
                session.commit()
                changed += len(updates)


def refresh_role_match_for(terms: list[str], include: list[str], exclude: list[str], batch: int = 5_000) -> int | None:
    """Recheck role_match only for jobs whose title contains one of `terms` (role keywords just added or removed).

    Every other job's answer can't change, so this finds the candidates through the title index (jobs_fts)
    and takes a second or two where refresh_role_match reads all 1.7M titles (about a minute). Returns how
    many changed, or None when there's no title index (not SQLite) and the full refresh is needed instead.
    """
    from app.db import is_sqlite
    from app.pipeline.search import fts_any_term

    match = fts_any_term([t for t in terms if t.strip()])
    if not is_sqlite or not match:
        return None
    with SessionLocal() as session:
        rows = session.execute(
            text("SELECT id, title, role_match FROM jobs WHERE id IN (SELECT rowid FROM jobs_fts WHERE jobs_fts MATCH :m)"),
            {"m": match},
        ).all()
        updates = [{"i": i, "m": m} for i, t, old in rows if (m := role_matches(t or "", include, exclude)) != bool(old)]
        for start in range(0, len(updates), batch):
            session.execute(text("UPDATE jobs SET role_match = :m WHERE id = :i"), updates[start : start + batch])
            session.commit()
    return len(updates)


# Shorter than this, a job counts as having no description (enrichment and the UI fetch it again).
MIN_DESCRIPTION_CHARS = 100

# Failed polls in a row after which a never-working board is switched off, by its answer: "not found" (404,
# 410) and "no such site" (422: Workday's unknown tenant or site, Polymer) are dead links; 403 may be a
# temporary block, so it takes longer. On 2026-11-10, 160 Workday 422s, 54 Workday 403s and 10 Polymer 422s
# from discovery had never worked and were retried every 3 hours. A board that ever worked is never switched off.
DEAD_BOARD_FAILS = 3
DEAD_BOARD_RULES = {"HTTP 404": DEAD_BOARD_FAILS, "HTTP 410": DEAD_BOARD_FAILS, "HTTP 422": DEAD_BOARD_FAILS,
                    "HTTP 403": 10}


def is_dead_board(company: Company, error: str) -> bool:
    if company.last_ok_at is not None:
        return False
    code = " ".join(str(error).split()[:2])  # "HTTP 404"
    return company.fail_streak >= DEAD_BOARD_RULES.get(code, 10**9)

# A failed name lookup ("getaddrinfo failed") is either a dead host or this machine being offline (Wi-Fi not
# back after sleep). On 2026-11-10 one poll right after waking failed 15,973 boards that way, and the idle
# rule then left them unchecked for 3 hours. So a lookup failure is first checked against a few hosts that
# are always up: if those fail too, the machine is offline and the board is simply tried again next poll.
_LOOKUP_FAILED = re.compile(r"getaddrinfo failed|Name or service not known|nodename nor servname|"
                            r"Temporary failure in name resolution|No address associated with hostname", re.I)
ONLINE_PROBE_HOSTS = ("www.google.com", "boards-api.greenhouse.io", "www.cloudflare.com")
_online_cache: tuple[float, bool] | None = None


def _resolves(host: str) -> bool:
    try:
        socket.getaddrinfo(host, 443)
        return True
    except OSError:
        return False


async def is_offline(error: str) -> bool:
    """True if `error` is a failed name lookup and this machine can't resolve well-known hosts either.
    Answers are cached for 30 s, so a poll with thousands of such errors checks only a few times."""
    global _online_cache
    if not _LOOKUP_FAILED.search(error or ""):
        return False
    now = time.monotonic()
    if _online_cache is None or now - _online_cache[0] > 30:
        results = await asyncio.gather(*(asyncio.to_thread(_resolves, h) for h in ONLINE_PROBE_HOSTS))
        _online_cache = (now, any(results))
    return not _online_cache[1]


def is_alert_worthy(job: Job, prefs: UserPrefs) -> bool:
    return (
        job.role_match
        and job.us_eligible
        and not job.is_repost
        and not job.baseline
        and job.closed_at is None
        and not (prefs.hide_no_sponsor and job.no_sponsor)
        and not (prefs.exclude_senior and job.exp_level == "senior")
    )


def process_company(
    session: Session, company: Company, fetched: list[NormalizedJob], prefs: UserPrefs, now: datetime,
    *, complete: bool = True, commit_every: int | None = None,
) -> list[Job]:
    """Reconcile one source's current listing with the database. Returns the newly added jobs.

    complete: the listing contains every open job, so jobs missing from it can be closed.
    """
    first_poll = company.poll_count == 0
    # After a gap in checking (server off, source polled rarely), new jobs went live some time since the
    # previous successful check, not just now (see normalize.listed_window).
    gap_start = company.last_ok_at or (company.last_polled_at if (company.last_status or "").startswith("ok") else None)
    if gap_start is not None and now - gap_start <= timedelta(minutes=settings.listed_exact_gap_minutes):
        gap_start = None
    # The few columns the reconcile needs, read from the covering index ix_jobs_company_sync: loading every job
    # as a full row (even with the description deferred) read past each description's overflow pages and was
    # 95% of a save. Changes are written as UPDATEs by id; a full Job is loaded only to fill in a description.
    existing = {r.external_id: r for r in session.execute(
        select(Job.external_id, Job.id, Job.last_seen_at, Job.missed_polls, Job.closed_at, Job.described)
        .where(Job.company_id == company.id))}
    refresh_seen: list[int] = []  # last_seen_at older than LAST_SEEN_RESOLUTION
    reset_missed: list[int] = []
    reopened: list[int] = []
    seen_ids = set()
    new_jobs: list[Job] = []
    same_poll_ids: set[int] = set()
    pending: deque[Job] = deque()  # new jobs whose ids haven't been moved into same_poll_ids yet

    for nj in fetched:
        if nj.external_id in seen_ids:
            continue
        seen_ids.add(nj.external_id)
        row = existing.get(nj.external_id)
        if row is not None:
            # Only write when something changed. Rewriting last_seen_at on every job every poll rewrote
            # ~900k rows (with their descriptions) per pass and held the write lock for tens of seconds.
            # last_seen_at only has to be accurate to the day (aggregators close after stale_days).
            if row.last_seen_at is None or now - row.last_seen_at > LAST_SEEN_RESOLUTION:
                refresh_seen.append(row.id)
            if row.missed_polls:
                reset_missed.append(row.id)
            if not row.described and nj.description:
                # Enrichment: saved without its details (e.g. a Workday first scan), fetched now.
                job = session.get(Job, row.id)
                job.description = nj.description
                job.posted_at = job.posted_at or nj.posted_at
                job.location = nj.location[:400] or job.location
                job.remote = job.remote or nj.remote
                with session.no_autoflush:
                    apply_flags(session, job, prefs)
            if row.closed_at is not None:
                reopened.append(row.id)
                session.add(JobEvent(job_id=row.id, kind="reopened", at=now))
            continue

        job = Job(
            company=company,
            external_id=nj.external_id,
            title=nj.title[:400],
            company_name=(nj.company_name or company.name)[:200],
            location=nj.location[:400],
            remote=nj.remote,
            url=nj.url,
            description=nj.description,
            department=nj.department[:200],
            posted_at=nj.posted_at,
            # On a source's first scan we can't tell when jobs appeared, so fall back to the posted date.
            first_seen_at=min(nj.posted_at, now) if first_poll and nj.posted_at else now,
            last_seen_at=now,
            listed_earliest=None if first_poll else gap_start,
            baseline=first_poll,
            alerted=first_poll,
            is_repost=False,  # set here: column defaults only apply at the (batched) flush
            missed_polls=0,
        )
        # Jobs added earlier in this poll that are in the database by now (a batch commit, or an autoflush)
        # must not count as originals. Unflushed ones aren't visible to find_original's queries anyway.
        while pending and pending[0].id is not None:
            same_poll_ids.add(pending.popleft().id)
        with session.no_autoflush:  # the half-built job is already attached via job.company
            apply_flags(session, job, prefs)
            original = find_original(
                session, job.company_key, nj.title, nj.location, nj.description, now,
                settings.repost_window_days, same_poll_ids, company.id,
            )
        kind, detail = "seen", ""
        if original is not None:
            job.is_repost = True
            job.repost_of_id = original.id
            if original.closed_at is None and original.company_id != company.id:
                kind, detail = "duplicate", f"also listed via {original.company.name} (job {original.id})"
            else:
                kind, detail = "repost", f"repost of job {original.id}"
        elif not first_poll and nj.posted_at and nj.posted_at < now - timedelta(days=settings.resurfaced_days):
            # Newly listed, but the source says it was posted long ago: an old job re-listed or refreshed.
            job.is_repost = True
            kind, detail = "resurfaced", f"originally posted {nj.posted_at:%b %d, %Y}"
        # No flush per job: flushing each insert made a big first scan's save take minutes.
        session.add(job)
        session.add(JobEvent(job=job, kind=kind, at=now, detail=detail))
        new_jobs.append(job)
        pending.append(job)
        if commit_every and len(new_jobs) % commit_every == 0:
            # A big board's first scan adds thousands of jobs: commit in batches so the
            # SQLite write lock is never held long enough to block the other lane or the API.
            session.commit()

    missed: dict[int, list[int]] = {}  # new missed_polls -> job ids still short of closing
    to_close: list[int] = []
    for ext_id, row in existing.items():
        if ext_id in seen_ids or row.closed_at is not None:
            continue
        if complete:
            # Gone from a complete listing: close after enough misses in a row.
            misses = (row.missed_polls or 0) + 1
            if misses >= settings.missed_polls_to_close:
                to_close.append(row.id)
                session.add(JobEvent(job_id=row.id, kind="closed", at=now))
            missed.setdefault(misses, []).append(row.id)
        elif ((not ADAPTERS[company.ats].complete_feed or ADAPTERS[company.ats].capped_ages_out)
              and row.last_seen_at < now - timedelta(days=settings.stale_days)):
            # Aggregators (and capped newest-N feeds) only list recent jobs, so age jobs out instead.
            to_close.append(row.id)
            session.add(JobEvent(job_id=row.id, kind="closed", at=now, detail=f"not seen for {settings.stale_days} days"))

    touched: set[int] = set()

    def bulk(ids: list[int], **values) -> None:
        touched.update(ids)
        for i in range(0, len(ids), 500):  # SQLite's limit on bound parameters
            session.execute(update(Job).where(Job.id.in_(ids[i:i + 500])).values(**values)
                            .execution_options(synchronize_session=False))

    bulk(refresh_seen, last_seen_at=now)
    bulk(reset_missed, missed_polls=0)
    bulk(reopened, closed_at=None)
    for misses, ids in missed.items():
        bulk(ids, missed_polls=misses)
    bulk(to_close, closed_at=now)
    # Jobs this session already holds (added in an earlier poll of the same session, as in tests, or loaded
    # for enrichment) would keep their old values: let them reload on next access.
    for job_id in touched:
        obj = session.identity_map.get(identity_key(Job, job_id))
        if obj is not None:
            session.expire(obj, ["last_seen_at", "missed_polls", "closed_at"])

    company.poll_count += 1
    company.last_polled_at = now
    company.last_ok_at = now
    company.last_status = f"ok ({len(seen_ids)} {'open' if complete else 'fetched'})"
    return new_jobs


RETRY_FAILS = 2  # failures in a row a previously working board is retried at its normal pace


def _due(company: Company, now: datetime) -> bool:
    adapter = ADAPTERS.get(company.ats)
    if adapter is None or not adapter.is_configured():
        return False
    if company.last_polled_at is None:
        return True
    interval = adapter.min_interval_minutes
    # Boards with no open jobs, or whose last fetch failed, are checked less often: with thousands of
    # discovered boards, most are small companies that post rarely. But a board that has worked before gets
    # RETRY_FAILS tries at its normal pace first: one timeout or 502 used to park Accenture, Micron and
    # Prime Healthcare (2-3k jobs each) for 3 hours, and all of them answered fine minutes later.
    # Empty boards are checked at the normal pace too (except in the rate-limited bulk lane): a small company's
    # first new job is exactly the kind found hours late on LinkedIn, and since polls save every due board
    # (lanes in their own processes, 2026-11-10) the 1,678 empty fast-lane boards cost about a minute a poll.
    status = company.last_status or ""
    failing = status.startswith("error")
    retry_soon = failing and company.last_ok_at is not None and (company.fail_streak or 0) <= RETRY_FAILS
    empty_and_slowed = status.startswith("ok (0 ") and adapter.lane == "bulk"
    if empty_and_slowed or (failing and not retry_soon):
        interval = max(interval, settings.idle_poll_minutes)
    if interval <= 0:
        return True
    # A little slack so a 15-minute source doesn't slip to every 20 minutes with 5-minute polls.
    return now - company.last_polled_at >= timedelta(minutes=interval - 1)


async def _fetch(client: httpx.AsyncClient, sem: asyncio.Semaphore, company: Company, known_ids: set[str]):
    adapter = ADAPTERS[company.ats]
    full = company.poll_count % adapter.full_scan_every == 0
    async with sem:
        try:
            jobs = await adapter.fetch(client, company.slug, full=full, known_ids=known_ids, first_scan=company.poll_count == 0)
            complete = adapter.complete_feed and full and adapter.last_fetch_complete(company.slug)
            return company, jobs, complete, None
        except httpx.HTTPStatusError as e:
            return company, None, False, f"HTTP {e.response.status_code}"
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
            return company, None, False, f"{type(e).__name__}: {e}"[:300]
        except Exception as e:  # an adapter bug must only fail its own source, never the whole poll
            log.exception("Unexpected error fetching %s (%s/%s)", company.name, company.ats, company.slug)
            return company, None, False, f"{type(e).__name__}: {e}"[:300]


def _save(session: Session, company: Company, fetched: list[NormalizedJob], prefs: UserPrefs, now: datetime, complete: bool) -> list[Job]:
    new_jobs = process_company(session, company, fetched, prefs, now, complete=complete, commit_every=200)
    session.commit()  # per source, so the write lock is held only briefly
    return new_jobs


async def poll_once(lane: str | None = None) -> dict:
    """Poll every enabled source that's due (only sources in `lane`, if given). Returns a summary."""
    now = datetime.now(timezone.utc)
    # Seconds spent in each phase, reported in the summary (GET /api/status shows the last poll's).
    clock, timings = time.monotonic(), {}

    def lap(phase: str) -> None:
        nonlocal clock
        timings[phase] = round(time.monotonic() - clock, 1)
        clock = time.monotonic()

    with SessionLocal() as session:
        if lane in (None, "fast"):  # one lane adds new companies, so two lanes starting together can't both insert them
            sync_companies(session, settings.companies_file)
        prefs = get_prefs(session)
        companies = [
            c for c in session.scalars(select(Company).where(Company.enabled.is_(True)))
            if _due(c, now) and (lane is None or ADAPTERS[c.ats].lane == lane)
        ]
        # Longest-waiting first: fetch slots are handed out in this order, and if the poll's deadline
        # cuts it short, the sources left over are the ones checked most recently.
        companies.sort(key=lambda c: c.last_polled_at or datetime.min.replace(tzinfo=timezone.utc))
        known: dict[int, set[str]] = {}
        for company_id, ext_id in session.execute(
            select(Job.company_id, Job.external_id).where(Job.company_id.in_([c.id for c in companies]))
        ):
            known.setdefault(company_id, set()).add(ext_id)
        # Jobs saved without a description are left out of known_ids (a batch per source per poll), so
        # adapters fetch their details again and process_company fills them in: without a description
        # there's no sponsorship or experience check.
        undescribed: dict[int, int] = {}
        # Newest first, and jobs found after tracking began before the ones already listed then: in database
        # order, a board's new jobs waited behind tens of thousands of old ones (JazzHR: 93k).
        for company_id, ext_id in session.execute(
            select(Job.company_id, Job.external_id).where(
                Job.company_id.in_([c.id for c in companies]), Job.closed_at.is_(None),
                Job.described.is_(False),
            ).order_by(Job.company_id, Job.baseline, Job.id.desc())
        ):
            if undescribed.get(company_id, 0) < settings.enrich_per_poll:
                known.get(company_id, set()).discard(ext_id)
                undescribed[company_id] = undescribed.get(company_id, 0) + 1

        # One pool of fetch slots per ATS, so a few slow Workday boards can't hold every slot.
        lap("prepare")
        sems = {
            ats: asyncio.Semaphore(ADAPTERS[ats].max_concurrency or settings.poll_concurrency)
            for ats in {c.ats for c in companies}
        }
        async with httpx.AsyncClient(timeout=30, headers={"User-Agent": USER_AGENT}, follow_redirects=True) as client:
            fetches = [asyncio.ensure_future(_fetch(client, sems[c.ats], c, known.get(c.id, set()))) for c in companies]
            # A poll ends by its deadline so the next one starts on time: one slow batch of boards
            # mustn't keep every other board from being checked again for an hour. Sources not
            # reached keep their last_polled_at, so they're first in line next time.
            deadline = settings.poll_deadline_minutes.get(lane) if lane else None  # --once polls everything

            new_total, errors = 0, 0
            to_alert: list[Job] = []
            employers: list[str] = []
            links: list[tuple[str, str]] = []  # (employer, job URL) from aggregators, to find their boards
            # Save each source as soon as it's fetched, so fast boards show up while slow ones are still going.
            cut_short, saving, offline, taken = 0, 0.0, 0, 0
            loop_started = time.monotonic()
            for next_done in asyncio.as_completed(fetches, timeout=deadline * 60 if deadline else None):
                try:
                    company, fetched, complete, error = await next_done
                except TimeoutError:
                    cut_short = sum(not f.done() for f in fetches)
                    for f in fetches:
                        f.cancel()
                    log.info("%s lane hit its %d-minute deadline; %d sources wait for the next poll", lane, deadline, cut_short)
                    break
                taken += 1
                if deadline and time.monotonic() - loop_started > deadline * 60:
                    # The deadline covers saving too: on 2026-11-10 a poll after a 3-hour backlog had every
                    # fetch done in minutes, then spent 72 minutes saving 12,603 boards, and no fast board was
                    # rechecked meanwhile. Unsaved sources keep their last check, so they're first next time.
                    cut_short = len(fetches) - taken + 1
                    for f in fetches:
                        f.cancel()
                    log.info("%s lane hit its %d-minute deadline while saving; %d sources wait for the next poll",
                             lane, deadline, cut_short)
                    break
                if error is not None and await is_offline(error):
                    # This machine is offline: the board isn't failing. Leave its last check as it was,
                    # so it's due again (and first in line) on the next poll.
                    offline += 1
                    continue
                if error is not None:
                    # Don't touch the source's jobs: a failed fetch says nothing about whether they closed.
                    errors += 1
                    company.last_polled_at = now
                    company.last_status = f"error: {error}"
                    company.fail_streak = (company.fail_streak or 0) + 1
                    if is_dead_board(company, error):
                        # A board that has never worked and keeps saying "not found" is a dead link from
                        # discovery or a list (2,078 of them were retried every 3 hours): switch it off.
                        company.enabled = False
                        company.last_status = f"error: {error} (switched off: this board never worked)"
                    log.warning("%s (%s/%s): %s", company.name, company.ats, company.slug, error)
                    continue
                try:
                    # In a worker thread: saving a big board takes seconds, and on the event loop it froze
                    # every download in flight. One save at a time, so the session is never shared.
                    started_save = time.monotonic()
                    new_jobs = await asyncio.to_thread(_save, session, company, fetched, prefs, now, complete)
                    saving += time.monotonic() - started_save
                except SQLAlchemyError as e:
                    # One source failing (e.g. the database stayed locked) mustn't break the rest of the poll.
                    session.rollback()
                    errors += 1
                    log.exception("Saving %s (%s/%s) failed", company.name, company.ats, company.slug)
                    company.last_status = f"error: save failed ({type(e).__name__})"
                    session.commit()
                    continue
                company.fail_streak = 0
                new_total += len(new_jobs)
                to_alert += [j for j in new_jobs if is_alert_worthy(j, prefs)]
                if ADAPTERS[company.ats].aggregator:
                    employers += [j.company_name for j in new_jobs if j.company_name]
                    links += [(j.company_name, j.url) for j in fetched if j.url]
            session.commit()

            lap("fetch_and_save")
            timings["of_which_saving"] = round(saving, 1)
            harvested = []
            verified_closed = 0
            if lane in (None, "fast"):  # only the fast lane adds sources and checks apply pages
                harvested = harvest(session, links, settings.harvest_per_poll)
                harvested += await maybe_sync_yc(session, client)
                harvested += await maybe_sync_lists(session, client)
                session.commit()
                lap("harvest")
                verified_closed = await verify_due(session, client, settings.verify_per_poll, now)
                lap("verify")
            session.commit()

            discovered = []
            if employers and settings.discovery_per_poll > 0:
                discovered = await discover(session, client, employers, settings.discovery_per_poll)
            lap("discover")

        emailed = False  # alerts are per user and opt-in now: pipeline/alerts.deliver_alerts, after each fast poll
        for j in to_alert:
            j.alerted = True
        session.commit()

    summary = {
        "lane": lane or "all",
        "sources": len(companies), "errors": errors, "new_jobs": new_total, "matches": len(to_alert),
        "emailed": emailed, "discovered": [c.name for c in discovered],
        "harvested": len(harvested), "closed_by_check": verified_closed, "cut_short": cut_short, "offline": offline, "timings": timings,
    }
    log.info("Poll finished: %s", summary)
    return summary


def backfill(batch: int = 500) -> int:
    """Compute fields added in later versions (locations, experience, employer) for older jobs."""
    done = 0
    with SessionLocal() as session:
        prefs = get_prefs(session)
        while True:
            jobs = list(session.scalars(select(Job).where(Job.exp_level.is_(None)).limit(batch)))
            if not jobs:
                break
            for job in jobs:
                job.company_name = job.company_name or job.company.name
                with session.no_autoflush:
                    apply_flags(session, job, prefs)
            session.commit()
            done += len(jobs)
    with SessionLocal() as session:  # described (added later): one pass over the descriptions, once
        filled_described = session.execute(text(
            "UPDATE jobs SET described = (description IS NOT NULL AND description != '') WHERE described IS NULL")).rowcount
        session.commit()
        if filled_described:
            log.info("Marked %d jobs as having a description or not", filled_described)
    with SessionLocal() as session:  # last_ok_at (added later): the last poll, if it succeeded
        session.execute(text("UPDATE companies SET last_ok_at = last_polled_at WHERE last_ok_at IS NULL AND last_status LIKE 'ok%'"))
        session.commit()
    with SessionLocal() as session:  # listed_at (added later) for older jobs, same rule as normalize.listed_at
        filled = session.execute(text("""
            UPDATE jobs SET listed_at = CASE
                WHEN posted_at IS NULL AND baseline THEN NULL
                WHEN posted_at IS NULL OR posted_at > first_seen_at THEN first_seen_at
                WHEN baseline THEN posted_at
                WHEN time(posted_at) = '00:00:00' AND julianday(first_seen_at) - julianday(posted_at) < 2 THEN first_seen_at
                ELSE posted_at END
            WHERE listed_at IS NULL""")).rowcount
        # Rows filled by an older rule that gave undated baseline jobs our first-scan time.
        session.execute(text("UPDATE jobs SET listed_at = NULL WHERE baseline AND posted_at IS NULL AND listed_at IS NOT NULL"))
        session.commit()
    done += filled or 0
    if done:
        log.info("Backfilled %d jobs", done)
    return done


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="run a single poll and exit")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    init_db()
    backfill()
    if args.once:
        print(asyncio.run(poll_once()))
        return

    async def forever():
        while True:
            await poll_once()
            await asyncio.sleep(settings.poll_minutes * 60)

    asyncio.run(forever())


if __name__ == "__main__":
    main()
