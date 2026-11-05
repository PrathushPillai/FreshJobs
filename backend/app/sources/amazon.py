"""amazon.jobs, through the JSON search the site itself uses (https://www.amazon.jobs/en/search.json).

slug: a query string of the site's search filters, e.g. "country=USA" or
"country=USA&category[]=Software Development&category[]=Data Science"; "" or "all" means every job.

API quirks (verified live, Nov 2026):
- `result_limit` is capped at 100, and `offset + result_limit` at 10,000 ("Cannot return more than
  10000 results at once"); `hits` is capped at 10,000 too. Amazon's US list alone is ~12,500 jobs, so a
  full scan asks for the category facet counts and pages through groups of categories that each stay
  under the cap. Jobs without a category are only reached if the whole query fits under the cap.
- `sort=recent` is newest-first by creation date, so partial scans read just the first pages.
- The list already carries the full description, basic and preferred qualifications: no per-job requests.
- `posted_date` is a date like "November  3, 2026" (no time), so posted_at is midnight UTC that day.
"""

import asyncio
import re
from collections.abc import Set
from datetime import datetime
from urllib.parse import parse_qsl

import httpx

from app.pipeline.normalize import html_to_text
from app.sources import http_util
from app.sources.base import Adapter, NormalizedJob

SEARCH = "https://www.amazon.jobs/en/search.json"
SITE = "https://www.amazon.jobs"
PAGE_SIZE = 100  # the API's maximum
RESULT_CAP = 10_000
BUCKET_LIMIT = 9_000  # keep each category group comfortably under the cap (counts drift while we page)
MAX_PARTIAL_PAGES = 10
CONCURRENCY = 4


def parse_slug(slug: str) -> list[tuple[str, str]]:
    slug = (slug or "").strip()
    if slug.lower() in ("", "all"):
        return []
    return [(k, v) for k, v in parse_qsl(slug, keep_blank_values=False) if k not in ("offset", "result_limit", "sort")]


def parse_posted(value: str | None) -> datetime | None:
    """'November  3, 2026' -> 2026-11-03 00:00 UTC."""
    text = re.sub(r"\s+", " ", value or "").strip()
    try:
        d = datetime.strptime(text, "%B %d, %Y")
    except ValueError:
        return None
    return http_util.midnight_utc(d.year, d.month, d.day)


def buckets(category_counts: list[tuple[str, int]], limit: int = BUCKET_LIMIT) -> list[list[str]]:
    """Group categories so each group's job count stays under `limit` (first-fit, biggest first)."""
    groups: list[tuple[int, list[str]]] = []
    for name, count in sorted(category_counts, key=lambda c: -c[1]):
        for i, (total, names) in enumerate(groups):
            if total + count <= limit:
                groups[i] = (total + count, names + [name])
                break
        else:
            groups.append((count, [name]))
    return [names for _, names in groups]


def _description(j: dict) -> str:
    parts = []
    for title, key in (("", "description"), ("Basic Qualifications", "basic_qualifications"),
                       ("Preferred Qualifications", "preferred_qualifications")):
        text = html_to_text(j.get(key) or "")
        if text:
            parts.append(f"{title}\n{text}" if title else text)
    return "\n\n".join(parts)


def to_job(j: dict) -> NormalizedJob:
    location = (j.get("normalized_location") or j.get("location") or "").strip()
    text = " ".join([location, j.get("title") or ""]).lower()
    return NormalizedJob(
        external_id=str(j.get("id_icims") or j["id"]),
        title=(j.get("title") or "").strip(),
        url=f"{SITE}{j['job_path']}" if j.get("job_path") else f"{SITE}/en/jobs/{j.get('id_icims')}",
        locations=[location] if location else [],
        remote="virtual" in text or "remote" in text,
        description=_description(j),
        department=j.get("job_category") or j.get("job_family") or "",
        posted_at=parse_posted(j.get("posted_date")),
    )


class AmazonAdapter(Adapter):
    ats = "amazon"
    # Amazon posts hundreds of jobs a day; a full US scan is ~130 list requests (100 jobs each).
    min_interval_minutes = 15
    full_scan_every = 12  # a full scan every ~3 hours
    lane = "slow"

    def __init__(self) -> None:
        self._capped: set[str] = set()

    def last_fetch_complete(self, slug: str) -> bool:
        return slug not in self._capped

    async def _page(self, client: httpx.AsyncClient, filters: list[tuple[str, str]], offset: int, extra=()) -> dict:
        params = filters + list(extra) + [("result_limit", str(PAGE_SIZE)), ("offset", str(offset)), ("sort", "recent")]
        data = (await http_util.get(client, SEARCH, params=params)).json()
        if data.get("error"):
            raise ValueError(f"amazon.jobs: {data['error']}")
        return data

    async def _scan(self, client, filters, extra=(), max_pages: int | None = None, known_ids: Set[str] | None = None):
        """Page through one query. Returns (jobs, hits). With known_ids, stops at a page with nothing new."""
        jobs: list[dict] = []
        offset, hits = 0, None
        while offset < RESULT_CAP and (max_pages is None or offset // PAGE_SIZE < max_pages):
            data = await self._page(client, filters, offset, extra)
            if hits is None:
                hits = data.get("hits") or 0
            page = data.get("jobs") or []
            jobs += page
            offset += PAGE_SIZE
            if not page or offset >= hits:
                break
            if known_ids is not None and all(str(j.get("id_icims")) in known_ids for j in page):
                break
        return jobs, hits or 0

    async def _full(self, client: httpx.AsyncClient, slug: str, filters: list[tuple[str, str]]) -> list[dict]:
        probe = (await http_util.get(
            client, SEARCH, params=filters + [("result_limit", "1"), ("facets[]", "category")]
        )).json()
        facets = (probe.get("facets") or {}).get("category_facet") or []
        counts = [(name, count) for f in facets for name, count in f.items()]
        hits = probe.get("hits") or 0
        if hits < RESULT_CAP or not counts or any(k == "category[]" for k, _ in filters):
            jobs, hits = await self._scan(client, filters)
            (self._capped.add if hits >= RESULT_CAP else self._capped.discard)(slug)
            return jobs
        # Too many for one query: split by category, a few groups at a time.
        sem = asyncio.Semaphore(CONCURRENCY)
        capped = False

        async def scan_group(names: list[str]) -> list[dict]:
            nonlocal capped
            async with sem:
                jobs, group_hits = await self._scan(client, filters, [("category[]", n) for n in names])
            capped = capped or group_hits >= RESULT_CAP
            return jobs

        results = await asyncio.gather(*(scan_group(g) for g in buckets(counts)))
        (self._capped.add if capped else self._capped.discard)(slug)
        return [j for group in results for j in group]

    async def fetch(
        self, client: httpx.AsyncClient, slug: str, *, full: bool = True, known_ids: Set[str] = frozenset(), **_
    ) -> list[NormalizedJob]:
        filters = parse_slug(slug)
        if full:
            raw = await self._full(client, slug, filters)
        else:
            raw, _ = await self._scan(client, filters, max_pages=MAX_PARTIAL_PAGES, known_ids=known_ids)
        unique = {str(j.get("id_icims") or j.get("id")): j for j in raw if j.get("id_icims") or j.get("id")}
        return [to_job(j) for j in unique.values()]
