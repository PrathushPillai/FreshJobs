import hashlib
import re
from datetime import datetime, timedelta

from sqlalchemy import inspect, or_, select
from sqlalchemy.orm import Session

from app.models import Job
from app.pipeline.normalize import normalize_location, normalize_title

SIMILARITY_THRESHOLD = 0.85
SHORT_DESCRIPTION = 400  # an aggregator's stub ("Category: ... Terms: ...") or a snippet: too short to compare


_LOCALE = re.compile(r"/[a-z]{2}(-[a-z]{2})?(?=/)")
_APPLY_TAIL = re.compile(r"/(apply|application|applynow|job)/?$")


def url_key(url: str) -> str:
    """An apply link reduced to host and path, so one posting reached two ways compares equal:
    "https://jobs.ashbyhq.com/x/123/application" and "https://jobs.ashbyhq.com/x/123", or a Workday link with
    and without its "/en-US" locale. Query strings (tracking, ?mobile=true) and case are dropped."""
    u = (url or "").strip().lower().split("#", 1)[0].split("?", 1)[0]
    u = u.split("://", 1)[-1].removeprefix("www.")
    u = _LOCALE.sub("", u, count=1)
    return _APPLY_TAIL.sub("", u.rstrip("/")).rstrip("/")


def _places(location: str) -> set[tuple[str, str]]:
    from app.pipeline.locations import parse_locations

    return {(p.city.lower(), p.state) for p in parse_locations(location or "") if p.city and p.state}


def same_posting(a_url: str, a_location: str, b_url: str, b_location: str) -> bool:
    """Two listings of one role at one employer (already the same title) are the same posting: the same apply
    link, or the same city."""
    if url_key(a_url) and url_key(a_url) == url_key(b_url):
        return True
    return bool(_places(a_location) & _places(b_location))


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
    url: str = "",
    aggregator: bool = False,
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
    from app.sources import ADAPTERS

    norm_title = normalize_title(title)
    if not description.strip():
        # Nothing to compare a description with: only another source's open copy with the same apply link or city
        # can match. Those are read once per save and title (link and place only, never the job row with its
        # description) and matched in memory: loading each candidate in full for each new job was quadratic,
        # and Dollar Tree's first Phenom scan (thousands of same-title store jobs also open on Workday) kept the
        # slow lane saving one board for hours (2026-11-17 profile: all its time in `_loaded`).
        for job_id in _same_posting_ids(session, titles, company_key, norm_title, by_title, company_id,
                                        same_poll_ids, url, location):
            candidate = _loaded(session, job_id)
            if candidate is not None:
                return candidate
        return None

    for job_id, board_id, still_open in by_title.get(norm_title, ()):
        # The same role, still open on the same board, in another place is a separate opening (a chain
        # hiring a store manager in Seattle and in San Francisco), not a repost: the city matters. Skipped
        # before loading the job: 500 open "Store Associate" jobs were each read with their descriptions.
        if job_id in same_poll_ids or (company_id is not None and board_id == company_id and still_open):
            continue
        if not description.strip() and not (still_open and board_id != company_id):
            continue  # nothing to compare a description with, and not a copy on another source
        candidate = _loaded(session, job_id)
        if candidate is None:
            continue
        if description.strip() and similarity(candidate.description or "", description) >= SIMILARITY_THRESHOLD:
            return candidate
        # The same role still open on another source, where the descriptions can't be compared: an aggregator's
        # copy (SimplifyJobs keeps a stub, Google Jobs a rewrite) of a job on the employer's own board, or a job
        # saved without its description. The same apply link or the same city makes it the same posting. Two of
        # the employer's own boards with full descriptions still need near-identical text (above).
        if still_open and board_id != company_id:
            other_aggregator = getattr(ADAPTERS.get(candidate.company.ats), "aggregator", False)
            short = (len(description.strip()) < SHORT_DESCRIPTION
                     or len((candidate.description or "").strip()) < SHORT_DESCRIPTION)
            if (aggregator or other_aggregator or short) and same_posting(url, location, candidate.url, candidate.location):
                return candidate
    return None


def _same_posting_ids(session, titles, company_key, norm_title, by_title, company_id, same_poll_ids, url, location):
    """Ids of still-open same-title jobs on other sources that `same_posting` matches, in `by_title` order.

    The candidates' (url_key, places) are read once and kept in `titles` (keyed by a tuple, so they never clash
    with an employer's key) for the rest of the save."""
    key = ("postings", company_key, norm_title, company_id)
    cached = titles.get(key) if titles is not None else None
    if cached is None:
        ids = [job_id for job_id, board_id, still_open in by_title.get(norm_title, ())
               if still_open and board_id != company_id]
        info: dict[int, tuple[str, set]] = {}
        for i in range(0, len(ids), 500):
            for job_id, job_url, job_location in session.execute(
                select(Job.id, Job.url, Job.location).where(Job.id.in_(ids[i:i + 500]))
            ):
                info[job_id] = (url_key(job_url), _places(job_location))
        cached = [(job_id, *info[job_id]) for job_id in ids if job_id in info]
        if titles is not None:
            titles[key] = cached
    mine_url, mine_places = url_key(url), _places(location)
    return [job_id for job_id, their_url, their_places in cached
            if job_id not in same_poll_ids and ((mine_url and mine_url == their_url) or (mine_places & their_places))]
