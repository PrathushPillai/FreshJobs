import hashlib
import re
from datetime import datetime, timedelta

from sqlalchemy import inspect, or_, select
from sqlalchemy.orm import Session

from app.models import Job
from app.pipeline.normalize import normalize_location, normalize_title

SIMILARITY_THRESHOLD = 0.85


def fingerprint(company_key: str, title: str, location: str) -> str:
    key = f"{company_key}|{normalize_title(title)}|{normalize_location(location)}"
    return hashlib.sha1(key.encode()).hexdigest()


def shingles(text: str, k: int = 5) -> set[int]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    if len(words) < k:
        return {hash(" ".join(words))} if words else set()
    return {hash(" ".join(words[i : i + k])) for i in range(len(words) - k + 1)}


def similarity(a: str, b: str) -> float:
    sa, sb = shingles(a), shingles(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _loaded(session: Session, job_id: int) -> Job | None:
    """The job, with any expired attributes loaded now (inside the caller's no_autoflush).

    A copy in the session may have been expired by a bulk UPDATE; reading it later, outside no_autoflush,
    would autoflush a half-built job. Only expired attributes are reloaded, so unsaved changes stay.
    """
    job = session.get(Job, job_id)
    expired = inspect(job).expired_attributes if job is not None else ()
    if expired:
        session.refresh(job, attribute_names=list(expired))
    return job


def find_original(
    session: Session,
    company_key: str,
    title: str,
    location: str,
    description: str,
    now: datetime,
    window_days: int,
    same_poll_ids: set[int] = frozenset(),
    company_id: int | None = None,
    titles: dict[str, dict[str, list[int]]] | None = None,
) -> Job | None:
    """Find an earlier job this one repeats, from any source.

    A match is a job at the same employer (by normalized name, so a company's own board and an
    aggregator listing match each other) that has the same normalized title and location
    (fingerprint), or the same title with a near-identical description, and that is still open
    or closed within the window. Jobs added in the same poll are skipped, since a company opening
    several identical roles at once isn't reposting them.
    """
    if not company_key:
        return None
    cutoff = now - timedelta(days=window_days)
    fp = fingerprint(company_key, title, location)
    recent = or_(Job.closed_at.is_(None), Job.closed_at >= cutoff)
    # Same-poll jobs are skipped in Python: as a NOT IN list in the SQL, a big first scan (JazzHR: ~110k
    # jobs) sent ever longer queries and saving slowed quadratically.
    # By fingerprint alone (it already includes company_key), through ix_jobs_fingerprint, and ids first: with
    # company_key in the WHERE, SQLite walked the employer's index and read every recent job's row, and the full
    # rows of every match (up to 890 per fingerprint) were loaded to take the first (2026-11-11 profile).
    same_fp_id = next((job_id for job_id in session.scalars(
        select(Job.id).where(Job.fingerprint == fp, recent).order_by(Job.first_seen_at)
    ) if job_id not in same_poll_ids), None)
    if same_fp_id is not None:
        return _loaded(session, same_fp_id)

    if not description.strip():
        return None
    # Titles first (a narrow select), descriptions only for the few with the same title: loading every
    # recent job's description at a big employer for each new job was quadratic too.
    # `titles` (one dict per save, from process_company) keeps each employer's {normalized title: [ids]}
    # for the rest of the save: built per new job, it read every recent job at a big employer for each one,
    # and the slow lane's worker spent most of its time here (2026-11-11 profile: ~35 boards saved a minute).
    by_title = titles.get(company_key) if titles is not None else None
    if by_title is None:
        by_title = {}
        # Index-only through ix_jobs_company_titles (every selected column is in it).
        for job_id, candidate_title, board_id, closed_at in session.execute(
            select(Job.id, Job.title, Job.company_id, Job.closed_at)
            .where(Job.company_key == company_key, recent, Job.first_seen_at >= cutoff)
        ):
            by_title.setdefault(normalize_title(candidate_title), []).append((job_id, board_id, closed_at is None))
        if titles is not None:
            titles[company_key] = by_title
    for job_id, board_id, still_open in by_title.get(normalize_title(title), ()):
        # The same role, still open on the same board, in another place is a separate opening (a chain
        # hiring a store manager in Seattle and in San Francisco), not a repost: the city matters. Skipped
        # before loading the job: 500 open "Store Associate" jobs were each read with their descriptions.
        if job_id in same_poll_ids or (company_id is not None and board_id == company_id and still_open):
            continue
        candidate = _loaded(session, job_id)
        if candidate is None:
            continue
        if similarity(candidate.description or "", description) >= SIMILARITY_THRESHOLD:
            return candidate
    return None
