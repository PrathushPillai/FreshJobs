"""Apple jobs (jobs.apple.com), through the JSON API the site's own frontend calls.

slug: a postLocation filter id, e.g. "postLocation-USA" (the US), or "" / "all" for every job.
Several ids can be given comma-separated ("postLocation-USA,postLocation-CAN").

Verified live (Nov 2026):
- POST /api/v1/search {"query", "filters": {"locations": [...]}, "page": N (1-based), "locale",
  "sort": "newest", "format": {...}} -> {"res": {"searchResults": [...], "totalRecords"}}; 20 rows a page,
  newest-first by postDateInGMT. No CSRF token is needed for search. The "format" key is required
  (without it the page comes back empty).
- One role posted in several cities comes back as one row per city ("200692743-5819", "-2459", ...),
  sharing a positionId. We group rows by positionId: one job per role, with every city.
- Descriptions: GET /api/v1/jobDetails/{row id} -> {"res": {jobSummary, description, responsibilities,
  minimumQualifications, preferredQualifications, ...}}, one per new role.
- Public URL: https://jobs.apple.com/en-us/details/{positionId}/{transformedPostingTitle}
"""

import asyncio
from collections.abc import Set

import httpx

from app.pipeline.normalize import html_to_text
from app.sources import http_util
from app.sources.base import Adapter, NormalizedJob, parse_iso

SITE = "https://jobs.apple.com"
PAGE_SIZE = 20
MAX_PAGES = 400
MAX_PARTIAL_PAGES = 10
DETAIL_CONCURRENCY = 4
FORMAT = {"longDate": "MMMM D, YYYY", "mediumDate": "MMM D, YYYY"}
SECTIONS = (
    ("", "jobSummary"), ("Description", "description"), ("Responsibilities", "responsibilities"),
    ("Minimum Qualifications", "minimumQualifications"), ("Preferred Qualifications", "preferredQualifications"),
)


def parse_slug(slug: str) -> list[str]:
    slug = (slug or "").strip()
    if slug.lower() in ("", "all"):
        return []
    return [s.strip() for s in slug.split(",") if s.strip()]


def _loc_name(loc: dict) -> str:
    country = loc.get("countryName") or ""
    city = loc.get("city") or loc.get("name") or ""
    parts = [city, loc.get("stateProvince") or "", country]
    if city == loc.get("name") == "United States":
        parts = [country]
    seen: list[str] = []
    for p in parts:
        if p and p not in seen:
            seen.append(p)
    return ", ".join(seen)


def description(detail: dict) -> str:
    parts = []
    for heading, key in SECTIONS:
        text = html_to_text((detail.get(key) or "").replace("\n", "<br>"))
        if text:
            parts.append(f"{heading}\n{text}" if heading else text)
    return "\n\n".join(parts)


class AppleAdapter(Adapter):
    ats = "apple"
    # ~4,500 US rows (Nov 2026): a full scan is ~225 list requests.
    min_interval_minutes = 15
    full_scan_every = 16  # every ~4 hours
    lane = "slow"

    async def _page(self, client: httpx.AsyncClient, locations: list[str], page: int) -> dict:
        body = {"query": "", "filters": {"locations": locations} if locations else {}, "page": page,
                "locale": "en-us", "sort": "newest", "format": FORMAT}
        return (await http_util.post(client, f"{SITE}/api/v1/search", json=body)).json().get("res") or {}

    async def fetch(
        self, client: httpx.AsyncClient, slug: str, *, full: bool = True, known_ids: Set[str] = frozenset()
    ) -> list[NormalizedJob]:
        locations = parse_slug(slug)
        roles: dict[str, list[dict]] = {}  # positionId -> its rows (one per city)
        seen_rows: set[str] = set()
        for page in range(1, (MAX_PAGES if full else MAX_PARTIAL_PAGES) + 1):
            res = await self._page(client, locations, page)
            rows = [r for r in res.get("searchResults") or [] if r.get("id") and r["id"] not in seen_rows]
            if not rows:
                break
            for r in rows:
                seen_rows.add(r["id"])
                roles.setdefault(str(r.get("positionId") or r["id"]), []).append(r)
            if page * PAGE_SIZE >= (res.get("totalRecords") or 0):
                break
            if not full and all(str(r.get("positionId") or r["id"]) in known_ids for r in rows):
                break

        sem = asyncio.Semaphore(DETAIL_CONCURRENCY)

        async def build(pid: str, rows: list[dict]) -> NormalizedJob | None:
            first = rows[0]
            locs: list[str] = []
            for r in rows:
                for loc in r.get("locations") or []:
                    name = _loc_name(loc)
                    if name and name not in locs:
                        locs.append(name)
            job = NormalizedJob(
                external_id=pid,
                title=(first.get("postingTitle") or "").strip(),
                url=f"{SITE}/en-us/details/{pid}/{first.get('transformedPostingTitle') or ''}".rstrip("/"),
                locations=locs,
                remote=any("remote" in loc.lower() for loc in locs),
                department=((first.get("team") or {}).get("teamName") or ""),
                posted_at=parse_iso(first.get("postDateInGMT")),
            )
            if pid in known_ids:
                return job
            async with sem:
                try:
                    resp = await http_util.get(client, f"{SITE}/api/v1/jobDetails/{first['id']}")
                    detail = resp.json().get("res") or {}
                except (httpx.HTTPError, ValueError):
                    return None  # still new next poll
            job.description = description(detail)
            detail_locs = [name for name in (_loc_name(loc) for loc in detail.get("locations") or []) if name]
            if detail_locs:  # the detail has every city, with its state
                job.locations = list(dict.fromkeys(detail_locs))
            return job

        jobs = await asyncio.gather(*(build(pid, rows) for pid, rows in roles.items()))
        return [j for j in jobs if j is not None]
