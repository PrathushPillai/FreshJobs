"""Fetch every source, record new/closed/reposted jobs, and send alerts.

Run one poll by hand:  python -m app.pipeline.poller --once
"""

import argparse
import asyncio
import logging
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal, init_db
from app.h1b import sponsor_index
from app.models import Company, Job, JobEvent, JobLocation, UserPrefs
from app.notify.email import send_alert
from app.pipeline.dedupe import find_original, fingerprint
from app.pipeline.discovery import discover
from app.pipeline.filters import is_us_location, role_matches
from app.pipeline.locations import parse_locations
from app.pipeline.normalize import company_key, experience_level, experience_years, seniority
from app.pipeline.sponsorship import find_no_sponsor_phrase
from app.prefs import get_prefs, sync_companies
from app.sources import ADAPTERS, NormalizedJob

log = logging.getLogger(__name__)

USER_AGENT = "job-portal-personal/0.1 (personal job search tool)"


def apply_flags(session: Session, job: Job, prefs: UserPrefs) -> None:
    """Compute every derived field of a job from its raw data."""
    job.company_key = company_key(job.company_name or "")
    job.fingerprint = fingerprint(job.company_key, job.title, job.location)
    job.seniority = seniority(job.title)
    job.min_years = experience_years(job.description)
    job.exp_level = experience_level(job.min_years, job.seniority)
    job.role_match = role_matches(job.title, prefs.include_keywords, prefs.exclude_keywords)
    job.us_eligible = is_us_location(job.location, job.remote)
    job.no_sponsor = find_no_sponsor_phrase(job.description) is not None
    job.h1b_filings = sponsor_index(session).filings(job.company_name or "")
    job.places = [JobLocation(city=p.city, state=p.state, country=p.country) for p in parse_locations(job.location)]


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
    existing = {j.external_id: j for j in session.scalars(select(Job).where(Job.company_id == company.id))}
    seen_ids = set()
    new_jobs: list[Job] = []

    for nj in fetched:
        if nj.external_id in seen_ids:
            continue
        seen_ids.add(nj.external_id)
        job = existing.get(nj.external_id)
        if job is not None:
            job.last_seen_at = now
            job.missed_polls = 0
            if job.closed_at is not None:
                job.closed_at = None
                session.add(JobEvent(job=job, kind="reopened", at=now))
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
            baseline=first_poll,
            alerted=first_poll,
        )
        with session.no_autoflush:  # the half-built job is already attached via job.company
            apply_flags(session, job, prefs)
            original = find_original(
                session, job.company_key, nj.title, nj.location, nj.description, now,
                settings.repost_window_days, {j.id for j in new_jobs},
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
        session.add(job)
        session.flush()
        session.add(JobEvent(job=job, kind=kind, at=now, detail=detail))
        new_jobs.append(job)
        if commit_every and len(new_jobs) % commit_every == 0:
            # A big board's first scan adds thousands of jobs: commit in batches so the
            # SQLite write lock is never held long enough to block the other lane or the API.
            session.commit()

    for ext_id, job in existing.items():
        if ext_id in seen_ids or job.closed_at is not None:
            continue
        if complete:
            # Gone from a complete listing: close after enough misses in a row.
            job.missed_polls += 1
            if job.missed_polls >= settings.missed_polls_to_close:
                job.closed_at = now
                session.add(JobEvent(job=job, kind="closed", at=now))
        elif not ADAPTERS[company.ats].complete_feed and job.last_seen_at < now - timedelta(days=settings.stale_days):
            # Aggregators only list recent jobs, so age jobs out instead.
            job.closed_at = now
            session.add(JobEvent(job=job, kind="closed", at=now, detail=f"not seen for {settings.stale_days} days"))

    company.poll_count += 1
    company.last_polled_at = now
    company.last_status = f"ok ({len(seen_ids)} {'open' if complete else 'fetched'})"
    return new_jobs


def _due(company: Company, now: datetime) -> bool:
    adapter = ADAPTERS.get(company.ats)
    if adapter is None or not adapter.is_configured():
        return False
    if company.last_polled_at is None or adapter.min_interval_minutes <= 0:
        return True
    # A little slack so a 15-minute source doesn't slip to every 20 minutes with 5-minute polls.
    return now - company.last_polled_at >= timedelta(minutes=adapter.min_interval_minutes - 1)


async def _fetch(client: httpx.AsyncClient, sem: asyncio.Semaphore, company: Company, known_ids: set[str]):
    adapter = ADAPTERS[company.ats]
    full = company.poll_count % adapter.full_scan_every == 0
    async with sem:
        try:
            jobs = await adapter.fetch(client, company.slug, full=full, known_ids=known_ids)
            complete = adapter.complete_feed and full and adapter.last_fetch_complete(company.slug)
            return company, jobs, complete, None
        except httpx.HTTPStatusError as e:
            return company, None, False, f"HTTP {e.response.status_code}"
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
            return company, None, False, f"{type(e).__name__}: {e}"[:300]
        except Exception as e:  # an adapter bug must only fail its own source, never the whole poll
            log.exception("Unexpected error fetching %s (%s/%s)", company.name, company.ats, company.slug)
            return company, None, False, f"{type(e).__name__}: {e}"[:300]


async def poll_once(lane: str | None = None) -> dict:
    """Poll every enabled source that's due (only sources in `lane`, if given). Returns a summary."""
    now = datetime.now(timezone.utc)
    with SessionLocal() as session:
        if lane != "slow":  # one lane adds new companies, so two lanes starting together can't both insert them
            sync_companies(session, settings.companies_file)
        prefs = get_prefs(session)
        companies = [
            c for c in session.scalars(select(Company).where(Company.enabled.is_(True)))
            if _due(c, now) and (lane is None or ADAPTERS[c.ats].lane == lane)
        ]
        known: dict[int, set[str]] = {}
        for company_id, ext_id in session.execute(
            select(Job.company_id, Job.external_id).where(Job.company_id.in_([c.id for c in companies]))
        ):
            known.setdefault(company_id, set()).add(ext_id)

        # One pool of fetch slots per ATS, so a few slow Workday boards can't hold every slot.
        sems = {ats: asyncio.Semaphore(settings.poll_concurrency) for ats in {c.ats for c in companies}}
        async with httpx.AsyncClient(timeout=30, headers={"User-Agent": USER_AGENT}, follow_redirects=True) as client:
            fetches = [_fetch(client, sems[c.ats], c, known.get(c.id, set())) for c in companies]

            new_total, errors = 0, 0
            to_alert: list[Job] = []
            employers: list[str] = []
            # Save each source as soon as it's fetched, so fast boards show up while slow ones are still going.
            for next_done in asyncio.as_completed(fetches):
                company, fetched, complete, error = await next_done
                if error is not None:
                    # Don't touch the source's jobs: a failed fetch says nothing about whether they closed.
                    errors += 1
                    company.last_polled_at = now
                    company.last_status = f"error: {error}"
                    log.warning("%s (%s/%s): %s", company.name, company.ats, company.slug, error)
                    continue
                try:
                    new_jobs = process_company(session, company, fetched, prefs, now, complete=complete, commit_every=200)
                    session.commit()  # per source, so the write lock is held only briefly
                except SQLAlchemyError as e:
                    # One source failing (e.g. the database stayed locked) mustn't break the rest of the poll.
                    session.rollback()
                    errors += 1
                    log.exception("Saving %s (%s/%s) failed", company.name, company.ats, company.slug)
                    company.last_status = f"error: save failed ({type(e).__name__})"
                    session.commit()
                    continue
                new_total += len(new_jobs)
                to_alert += [j for j in new_jobs if is_alert_worthy(j, prefs)]
                if not ADAPTERS[company.ats].complete_feed:
                    employers += [j.company_name for j in new_jobs if j.company_name]
            session.commit()

            discovered = []
            if employers and settings.discovery_per_poll > 0:
                discovered = await discover(session, client, employers, settings.discovery_per_poll)

        emailed = False
        if to_alert and prefs.email_alerts:
            emailed = send_alert(to_alert)
        for j in to_alert:
            j.alerted = True
        session.commit()

    summary = {
        "lane": lane or "all",
        "sources": len(companies), "errors": errors, "new_jobs": new_total, "matches": len(to_alert),
        "emailed": emailed, "discovered": [c.name for c in discovered],
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
