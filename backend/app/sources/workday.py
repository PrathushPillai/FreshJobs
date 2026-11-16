"""Workday career sites, through the JSON API ("CXS") that the public career site itself uses.

slug: "{tenant}|{wdN}|{site}", taken from the career-site URL
https://{tenant}.{wdN}.myworkdayjobs.com/{site}, e.g. "nvidia|wd5|NVIDIAExternalCareerSite". A few tenants live on a host with no wdN
(https://nxp.myworkdayjobs.com/careers); their slug has "-" in the middle: "nxp|-|careers".

API quirks (verified live, Nov 2026):
- The list endpoint caps `limit` at 20 (larger values return HTTP 400).
- `total` is only reliable on the first page (later pages often report 0), and paging past the
  real end can wrap around to the first page again. So we keep the first page's total and stop
  when a page brings no new IDs.
- Results are newest-first by posting date for most tenants, but some pin a block of "featured"
  jobs (up to ~100, of any age) above the newest-first list. A full scan learns where that block
  ends, and later partial scans start there.
- The list only has title, path, a location summary and "Posted N Days Ago". The description,
  exact start date and locations need one GET per job, made only for jobs not already known
  (and, on a board's first scan, only for jobs posted in the last BASELINE_DETAIL_DAYS days; the
  poller fetches the rest later, a batch per poll, see poll_once's enrichment).
"""

import asyncio
import re
from collections.abc import Set
from datetime import datetime, timedelta, timezone

import httpx

from app.pipeline.normalize import html_to_text
from app.sources import http_util
from app.sources.base import Adapter, NormalizedJob, parse_iso

PAGE_SIZE = 20  # the API's maximum
# Boards with more jobs report total == 2000 exactly and stop paging there (Micron pages past it normally).
RESULT_CAP = 2000
MAX_PARTIAL_PAGES = 15
DETAIL_CONCURRENCY = 4
# Workday's Cloudflare answers 429 to bursts, with no Retry-After. Three quick retries (2, 4 and 8 s) weren't
# enough: on 2026-11-15, 729 of ~4,900 boards failed a check in 6 hours (CVS Health, Dollar Tree, O'Reilly,
# Micron, Northrop Grumman...), while the same requests a minute later answered 200. List pages now wait
# 5, 10, 20, 40 and 60 s, and a scan that still gets a 429 part way keeps the pages it read (see _keep_partial).
# Then on Monday 2026-11-16 the week's first wave (13,561 new jobs in one slow-lane pass, each a detail request)
# kept Workday rate-limiting the whole server, not single tenants: boards of every employer got 429 at once, and
# those patient retries (over 2 minutes per board, ~2,500 boards) stretched one slow pass past 3.5 hours while the
# constant retrying kept the limit on. Now a 429 pauses every Workday request for PAUSE_AFTER_429 seconds (like
# YC, sources/yc.py), the list page is retried once, and a board still refused fails fast and is retried in 30
# minutes (poller.RATE_LIMITED_RETRY_MINUTES).
LIST_RETRIES = 1
LIST_BACKOFF_SECONDS = 5.0
PAUSE_AFTER_429 = 120.0
SLOW_DOWN = (429, 503)
_PAUSE = http_util.Spacer(0.0)  # no spacing, only the shared pause after a 429


# After a pause, requests go out one at a time GENTLE_SPACING seconds apart until GENTLE_SUCCESSES of them in a row
# succeed, then at full speed again. With only the pause, all 20 waiting requests went out together when it ended
# and were refused at once (2026-11-16 evening: 45 of 53 Workday boards refused in 16 minutes).
GENTLE_SPACING = 3.0
GENTLE_SUCCESSES = 20
_gentle = {"left": 0}  # successes still needed before full speed


async def _request(method, client: httpx.AsyncClient, url: str, **kwargs) -> httpx.Response:
    """A Workday request that waits out a pause, starts one when Workday answers 429, and comes back gently."""
    await _PAUSE.wait()
    try:
        resp = await method(client, url, **kwargs)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 429:
            _PAUSE.pause(PAUSE_AFTER_429)
            _PAUSE.seconds = GENTLE_SPACING
            _gentle["left"] = GENTLE_SUCCESSES
        raise
    if _gentle["left"]:
        _gentle["left"] -= 1
        if not _gentle["left"]:
            _PAUSE.seconds = 0.0  # Workday answers normally again: full speed
    return resp


def _slow_down(e: Exception) -> bool:
    return isinstance(e, httpx.HTTPStatusError) and e.response.status_code in SLOW_DOWN
# A board's first scan fetches details only for jobs posted in the last few days:
# a big tenant has thousands of old jobs, and a detail call for each would hold the slow lane for hours.
BASELINE_DETAIL_DAYS = 7


def parse_slug(slug: str) -> tuple[str, str, str]:
    parts = [p.strip() for p in slug.split("|")]
    if len(parts) != 3 or not all(parts):
        raise ValueError(f"Workday slug must look like 'tenant|wd5|Site', got {slug!r}")
    return parts[0], parts[1], parts[2]


def board_host(tenant: str, wd: str) -> str:
    """https://{tenant}.{wdN}.myworkdayjobs.com, or https://{tenant}.myworkdayjobs.com when wd is "-".

    NXP's wd3 host has shown Workday's maintenance page since 2026-11-06 (CXS answers 422) while
    nxp.myworkdayjobs.com serves the same 800 jobs.
    """
    return f"https://{tenant}.myworkdayjobs.com" if wd == "-" else f"https://{tenant}.{wd}.myworkdayjobs.com"


def days_ago(posted_on: str | None) -> int | None:
    """'Posted Today' -> 0, 'Posted Yesterday' -> 1, 'Posted 3 Days Ago' -> 3, 'Posted 30+ Days Ago' -> 30."""
    text = (posted_on or "").lower()
    if "today" in text:
        return 0
    if "yesterday" in text:
        return 1
    m = re.search(r"(\d+)\+?\s*day", text)
    return int(m.group(1)) if m else None


def job_id(external_path: str) -> str:
    """The posting's stable ID: the part after the last "_" in its path, e.g. "JR2019819" or "R1005012-1".

    The bare requisition ID isn't unique (one requisition can be posted several times, as "_JR123",
    "_JR123-1", ...), and the rest of the path changes when the title or location is edited.
    """
    tail = external_path.rsplit("/", 1)[-1]
    return tail.rsplit("_", 1)[-1] if "_" in tail else external_path


def _approx_date(posted_on: str | None, now: datetime) -> datetime | None:
    n = days_ago(posted_on)
    if n is None:
        return None
    day = (now - timedelta(days=n)).date()
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc)


def _pinned_end(postings: list[dict]) -> int:
    """Index where a pinned block of featured jobs ends and the newest-first list starts (0 = none).

    Detected as the first fresh (posted today/yesterday) job that follows a job at least 5 days old.
    """
    prev = None
    for i, p in enumerate(postings[:300]):
        n = days_ago(p.get("postedOn"))
        if n is not None and prev is not None and prev >= 5 and n <= 1:
            return i
        if n is not None:
            prev = n
    return 0


class WorkdayAdapter(Adapter):
    ats = "workday"
    # Tenants post up to ~150 jobs a day, and a full scan of a big board is ~100 list requests.
    min_interval_minutes = 10
    full_scan_every = 6
    lane = "slow"
    # Each employer is its own tenant, and 429s are retried with backoff (http_util). With the default 10,
    # a pass over ~3,700 boards left the median Workday board unchecked for 6.6 hours (2026-11-10).
    max_concurrency = 20

    def __init__(self) -> None:
        # slug -> offset where the newest-first list starts (after any pinned block), learned on full scans.
        # Kept in workday_state.json next to the database: held only in memory, every restart turned each
        # board's next partial scan into a full one (up to 100 list requests for a big board).
        self._pinned: dict[str, int] = {}
        # Slugs whose last full scan hit Workday's 2000-result cap: jobs past it are invisible, not closed.
        self._capped: set[str] = set()
        self._loaded = False

    def _state_path(self):
        """workday_state.json next to the database; None for an in-memory database (tests)."""
        from pathlib import Path

        from app.config import settings
        from app.matching import _models_dir

        url = settings.database_url
        if url in ("sqlite://", "sqlite:///:memory:") or ":memory:" in url:
            return None
        return Path(_models_dir()).parent / "workday_state.json"

    def _load_state(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        import json

        path = self._state_path()
        if path is None:
            return
        try:
            data = json.loads(path.read_text())
            self._pinned.update({k: int(v) for k, v in data.get("pinned", {}).items()})
            self._capped.update(data.get("capped", []))
        except (OSError, ValueError, TypeError):
            pass

    def _save_state(self) -> None:
        import json
        import os

        path = self._state_path()
        if path is None:
            return
        tmp = path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps({"pinned": self._pinned, "capped": sorted(self._capped)}))
            os.replace(tmp, path)
        except OSError:
            pass  # only a speed-up: the next full scan learns it again

    def last_fetch_complete(self, slug: str) -> bool:
        return slug not in self._capped

    async def _page(self, client: httpx.AsyncClient, api: str, offset: int) -> dict:
        # Retried with backoff on 429/503: tenants rate-limit bursts, and one 429 used to fail the whole board
        # (149 boards, Kraft Heinz, Lowe's, Lilly, CVS Health, GEICO, Wells Fargo..., from 2026-11-06 on).
        resp = await _request(
            http_util.post, client, f"{api}/jobs",
            json={"appliedFacets": {}, "limit": PAGE_SIZE, "offset": offset, "searchText": ""},
            retries=LIST_RETRIES, backoff=LIST_BACKOFF_SECONDS,
        )
        return resp.json()

    async def _list_all(self, client: httpx.AsyncClient, api: str) -> tuple[list[dict], bool]:
        """Every posting, and whether the list is complete (not cut off by the result cap)."""
        postings: list[dict] = []
        seen: set[str] = set()
        total = None
        offset = 0
        while True:
            try:
                data = await self._page(client, api, offset)
            except httpx.HTTPError as e:
                # Rate-limited part way through a big board: the pages read so far hold the newest jobs, so
                # keep them. The scan counts as incomplete, so no job is closed for missing from it.
                if postings and _slow_down(e):
                    return postings, False
                raise
            if total is None:
                total = data.get("total") or 0
            page = [p for p in data.get("jobPostings") or [] if p.get("externalPath")]
            fresh = [p for p in page if job_id(p["externalPath"]) not in seen]
            if not fresh:  # empty page, or wrapped around to the start
                break
            seen.update(job_id(p["externalPath"]) for p in fresh)
            postings += fresh
            offset += PAGE_SIZE
            if offset >= total:
                break
        return postings, total != RESULT_CAP and len(postings) >= total - PAGE_SIZE

    async def _list_recent(self, client: httpx.AsyncClient, api: str, start: int, known_ids: Set[str]) -> list[dict]:
        """Newest jobs only: page through until a page has nothing unknown and nothing posted today/yesterday."""
        postings: list[dict] = []
        seen: set[str] = set()

        def add(page: list[dict]) -> list[dict]:
            fresh = [p for p in page if p.get("externalPath") and job_id(p["externalPath"]) not in seen]
            seen.update(job_id(p["externalPath"]) for p in fresh)
            postings.extend(fresh)
            return fresh

        if start > 0:  # the pinned block can hold new jobs too
            add((await self._page(client, api, 0)).get("jobPostings") or [])
        offset = start
        for _ in range(MAX_PARTIAL_PAGES):
            try:
                data = await self._page(client, api, offset)
            except httpx.HTTPError as e:
                if postings and _slow_down(e):
                    break  # keep the newest pages (a partial scan never closes jobs)
                raise
            fresh = add(data.get("jobPostings") or [])
            if not fresh:
                break
            unknown = any(job_id(p["externalPath"]) not in known_ids for p in fresh)
            recent = any(days_ago(p.get("postedOn")) in (0, 1) for p in fresh)
            if not (unknown or recent):
                break
            offset += PAGE_SIZE
        return postings

    async def fetch(
        self, client: httpx.AsyncClient, slug: str, *, full: bool = True, known_ids: Set[str] = frozenset(),
        first_scan: bool = False,
    ) -> list[NormalizedJob]:
        tenant, wd, site = parse_slug(slug)
        host = board_host(tenant, wd)
        api = f"{host}/wday/cxs/{tenant}/{site}"

        self._load_state()
        if full or slug not in self._pinned:  # a partial scan needs the pinned offset from a full scan
            postings, complete = await self._list_all(client, api)
            (self._capped.discard if complete else self._capped.add)(slug)
            self._pinned[slug] = _pinned_end(postings) // PAGE_SIZE * PAGE_SIZE
            self._save_state()
        else:
            postings = await self._list_recent(client, api, self._pinned[slug], known_ids)

        now = datetime.now(timezone.utc)
        sem = asyncio.Semaphore(DETAIL_CONCURRENCY)

        async def build(p: dict) -> NormalizedJob | None:
            path = p["externalPath"]
            ext_id = job_id(path)
            url = f"{host}/{site}{path}"
            age = days_ago(p.get("postedOn"))
            if ext_id in known_ids or (first_scan and (age is None or age > BASELINE_DETAIL_DAYS)):
                summary = p.get("locationsText") or ""
                return NormalizedJob(
                    external_id=ext_id,
                    title=(p.get("title") or "").strip(),
                    url=url,
                    # "3 Locations" is a count, not a place
                    locations=[] if re.fullmatch(r"\d+ Locations", summary) else [summary] if summary else [],
                    remote="remote" in (p.get("remoteType") or summary).lower(),
                    posted_at=_approx_date(p.get("postedOn"), now),
                )
            async with sem:
                try:
                    resp = await _request(http_util.get, client, f"{api}{path}", retries=1)
                    info = resp.json().get("jobPostingInfo") or {}
                except (httpx.HTTPError, ValueError):
                    # Taken down since the list call, or a transient error. Skip it: it's still new
                    # next poll, so it gets another try instead of being saved without a description.
                    return None
            locations = [info.get("location") or ""] + list(info.get("additionalLocations") or [])
            locations = [loc for loc in locations if loc]
            remote_type = (info.get("remoteType") or p.get("remoteType") or "").lower()
            return NormalizedJob(
                external_id=ext_id,
                title=(info.get("title") or p.get("title") or "").strip(),
                url=info.get("externalUrl") or url,
                locations=locations,
                remote="remote" in remote_type or any("remote" in loc.lower() for loc in locations),
                description=html_to_text(info.get("jobDescription") or ""),
                posted_at=parse_iso(info.get("startDate")) or _approx_date(info.get("postedOn") or p.get("postedOn"), now),
            )

        jobs = await asyncio.gather(*(build(p) for p in postings))
        return [j for j in jobs if j is not None]
