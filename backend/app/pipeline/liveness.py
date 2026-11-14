"""Is a job still open? Opens its apply page the way a person would, and closes the job if it's gone.

Jobs from a company board that polls fine close on their own (missing from the listing twice). These
don't: jobs from aggregators (Google Jobs, Adzuna, HN, The Muse...) only age out after `stale_days`, and
jobs from a board whose fetches keep failing are never re-listed or closed. So every fast poll checks up
to `verify_per_poll` of them (the freshest first, the ones the feed shows on top), each at most once a
day, and the job page checks its job when it's opened (POST /api/jobs/{id}/verify).

A page counts as closed on HTTP 404/410, a redirect to an error page (Greenhouse sends closed jobs to
`?error=true`), or a "no longer accepting applications" kind of message. Anything else that isn't a
clear answer (403 from bot protection, timeouts) leaves the job as it is. Workday job pages are
rendered client-side, so their JSON API is asked instead. LinkedIn, Indeed and Glassdoor pages
(some Google Jobs apply links) are never fetched: collecting from them is against their terms.
"""

import asyncio
import re
from datetime import datetime, timedelta
from urllib.parse import urlparse

import httpx
from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session

from app.models import Company, Job, JobEvent
from app.sources import ADAPTERS

CHECK_EVERY = timedelta(hours=24)
CONCURRENCY = 8
_SKIP_HOSTS = ("linkedin.com", "indeed.com", "glassdoor.com")
_CLOSED = re.compile(
    r"no longer (accepting applications|available|open|active|being accepted|posted)|"
    r"(job|position|posting|vacancy|requisition|role|opening)\s+(has|have)\s+(been\s+)?(expired|closed|filled|removed)|"
    r"(this|the) (job|position|posting|opportunity|role) (is|has been) (closed|filled|no longer)|"
    r"job (posting )?(was )?not found|position (is )?(now )?closed|applications (are|have) (now )?closed|"
    r"job you('re| are) (looking for|trying to (view|find|apply))",
    re.I,
)
_LOCALE = re.compile(r"^[a-z]{2}-[A-Z]{2}$")
_HIDDEN = re.compile(r"<(script|style|noscript|template)\b.*?</\1\s*>", re.I | re.S)
_TAG = re.compile(r"<[^>]+>")


def visible_text(html: str) -> str:
    """The page's text without scripts, styles and tags. Many job sites ship every message they might show
    as data in a script ("Sorry, this role does not exist or is no longer available"), so matching the raw
    HTML closed open jobs: every Apple job opened in the UI was marked closed."""
    return _TAG.sub(" ", _HIDDEN.sub(" ", html))


def _workday_api(url: str) -> str | None:
    """A Workday job page's JSON endpoint: {host}/wday/cxs/{tenant}/{site}/job/..."""
    p = urlparse(url)
    m = re.match(r"^([a-z0-9-]+)\.(?:wd\d+\.)?myworkdayjobs\.com$", p.hostname or "")
    parts = [x for x in p.path.split("/") if x and not _LOCALE.match(x)]
    if not m or "job" not in parts or parts.index("job") == 0:
        return None
    site, rest = parts[0], parts[parts.index("job") :]
    return f"{p.scheme}://{p.hostname}/wday/cxs/{m.group(1)}/{site}/" + "/".join(rest)


async def check(client: httpx.AsyncClient, url: str) -> tuple[bool | None, str]:
    """(True if still open / False if closed / None if unclear, why)."""
    host = (urlparse(url).hostname or "").lower()
    if not url.startswith("http") or any(host == h or host.endswith("." + h) for h in _SKIP_HOSTS):
        return None, "not checked"
    api = _workday_api(url)
    try:
        if api:
            r = await client.get(api, headers={"Accept": "application/json"})
            if r.status_code in (404, 410):
                return False, f"taken down (HTTP {r.status_code})"
            return (True, "open") if r.status_code == 200 else (None, f"HTTP {r.status_code}")
        r = await client.get(url)
    except httpx.HTTPError as e:
        return None, type(e).__name__
    if r.status_code in (404, 410):
        return False, f"apply page is gone (HTTP {r.status_code})"
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}"
    if "error=true" in str(r.url):
        return False, "apply page redirects to an error"
    m = _CLOSED.search(visible_text(r.text[:600_000]))
    if m:
        return False, f'apply page says "{m.group(0)}"'
    return True, "open"


def close(session: Session, job: Job, why: str, now: datetime) -> None:
    job.closed_at = now
    session.add(JobEvent(job=job, kind="closed", at=now, detail=why))


# Job id -> when this process last checked it, so picking the next jobs doesn't re-read the rows just checked.
_checked: dict[int, datetime] = {}
_CHUNK = 500


def _chunks(ids: list[int]):
    for i in range(0, len(ids), _CHUNK):
        yield ids[i : i + _CHUNK]


def due_jobs(session: Session, limit: int, now: datetime) -> list[Job]:
    """The `limit` newest open jobs that can't close on their own and weren't checked in the last day.

    The old query walked every open job by date (ix_jobs_by_listed_v2) and read each one's row to see its
    source and checked_at: 100-460 s per fast poll on the cloud server (2026-11-13), growing through the day as
    the newest candidates got checked. Now the candidates' ids come first, index-only from their sources
    (ix_jobs_company_sync; ~140k, JazzHR alone 108k), then the date index is walked newest first reading ids
    only, and rows are read just for candidates this process hasn't checked today, stopping at `limit`.
    (Looking up each candidate's date by id instead was random reads: over 10 minutes on the server.)"""
    aggregators = [ats for ats, a in ADAPTERS.items() if a.aggregator]
    stale_source = or_(Company.last_ok_at.is_(None), Company.last_ok_at < now - CHECK_EVERY)
    company_ids = list(session.scalars(select(Company.id).where(or_(Company.ats.in_(aggregators), stale_source))))
    since = now - CHECK_EVERY
    for k in [k for k, at in _checked.items() if at < since]:
        del _checked[k]
    # Raw SQL: SQLAlchemy's with_hint() renders nothing on SQLite. Left alone, SQLite answered the first query
    # from ix_jobs_by_sponsor, reading every open job's row.
    hint = (lambda ix: f"INDEXED BY {ix} ") if session.get_bind().dialect.name == "sqlite" else (lambda ix: "")
    candidates: set[int] = set()
    for chunk in _chunks(company_ids):
        candidates.update(i for (i,) in session.execute(text(
            f"SELECT id FROM jobs {hint('ix_jobs_company_sync')}WHERE closed_at IS NULL AND company_id IN "
            f"({','.join(str(int(c)) for c in chunk)})")))
    candidates.difference_update(_checked)
    if not candidates:
        return []
    walk = text(f"SELECT id FROM jobs {hint('ix_jobs_by_listed_v2')}WHERE closed_at IS NULL AND is_repost = :no "
                "ORDER BY listed_at DESC").bindparams(no=False)
    jobs: list[Job] = []
    pending: list[int] = []

    def take(ids: list[int]) -> bool:
        rows = {j.id: j for j in session.scalars(select(Job).where(Job.id.in_(ids)))}
        for i in ids:
            j = rows.get(i)
            if j is None or not j.us_eligible:
                continue
            if j.checked_at and j.checked_at >= since:
                _checked[i] = j.checked_at  # checked by another process (the job panel's verify)
                continue
            jobs.append(j)
            if len(jobs) >= limit:
                return True
        return False

    for part in session.execute(walk.execution_options(yield_per=5000)).scalars().partitions():
        pending += [i for i in part if i in candidates]
        while len(pending) >= limit:
            if take(pending[:limit]):
                return jobs
            pending = pending[limit:]
    if pending:
        take(pending)
    return jobs


async def verify_due(session: Session, client: httpx.AsyncClient, limit: int, now: datetime) -> int:
    """Check up to `limit` jobs that can't close on their own; returns how many were closed."""
    if limit <= 0:
        return 0
    jobs = due_jobs(session, limit, now)
    sem = asyncio.Semaphore(CONCURRENCY)

    async def one(job: Job):
        async with sem:
            return job, await check(client, job.url)

    closed = 0
    for job, (is_open, why) in await asyncio.gather(*(one(j) for j in jobs)):
        job.checked_at = now
        _checked[job.id] = now
        if is_open is False:
            close(session, job, why, now)
            closed += 1
    session.commit()
    return closed
