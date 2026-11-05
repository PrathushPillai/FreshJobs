import hashlib
import re
from datetime import datetime, timedelta

from sqlalchemy import or_, select
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


def find_original(
    session: Session,
    company_key: str,
    title: str,
    location: str,
    description: str,
    now: datetime,
    window_days: int,
    same_poll_ids: set[int] = frozenset(),
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
    same_fp = next((j for j in session.scalars(
        select(Job).where(Job.company_key == company_key, recent, Job.fingerprint == fp).order_by(Job.first_seen_at)
    ) if j.id not in same_poll_ids), None)
    if same_fp:
        return same_fp

    if not description.strip():
        return None
    # Titles first (a narrow select), descriptions only for the few with the same title: loading every
    # recent job's description at a big employer for each new job was quadratic too.
    norm = normalize_title(title)
    candidates = [
        job_id for job_id, candidate_title in session.execute(
            select(Job.id, Job.title).where(Job.company_key == company_key, recent, Job.first_seen_at >= cutoff))
        if job_id not in same_poll_ids and normalize_title(candidate_title) == norm
    ]
    for job_id in candidates:
        candidate = session.get(Job, job_id)
        if candidate is not None and similarity(candidate.description or "", description) >= SIMILARITY_THRESHOLD:
            return candidate
    return None
