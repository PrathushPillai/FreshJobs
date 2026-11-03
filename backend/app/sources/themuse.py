"""The Muse public jobs API (aggregator, no key needed; 500 requests/hour per IP without a key).

slug: The Muse's filter parameters as a query string, plus an optional `pages` (default 5, max 20).
Repeat a key to OR its values. Empty, or any string without "=", means every job. Example:
  "category=Software Engineering&category=Data and Analytics&level=Entry Level&level=Mid Level"

Results are sorted newest-first with the (undocumented but working) `sort=publication_date` and
`descending=true`; without `sort` the order is effectively random. The API refuses page > 99.
"""

from urllib.parse import parse_qsl

import httpx

from app.pipeline.normalize import html_to_text
from app.sources.base import Adapter, NormalizedJob, parse_iso

API = "https://www.themuse.com/api/public/jobs"
DEFAULT_PAGES = 5
MAX_PAGES = 20


def split_slug(slug: str, default_pages: int, max_pages: int) -> tuple[list[tuple[str, str]], int]:
    params = parse_qsl(slug or "")
    pages = default_pages
    filters = []
    for key, value in params:
        if key == "pages":
            pages = max(1, min(max_pages, int(value)))
        else:
            filters.append((key, value))
    return filters, pages


class TheMuseAdapter(Adapter):
    ats = "themuse"
    complete_feed = False
    min_interval_minutes = 30

    async def fetch(self, client: httpx.AsyncClient, slug: str, **_) -> list[NormalizedJob]:
        filters, pages = split_slug(slug, DEFAULT_PAGES, MAX_PAGES)
        jobs = []
        for page in range(pages):
            params = [*filters, ("page", str(page)), ("sort", "publication_date"), ("descending", "true")]
            resp = await client.get(API, params=params)
            resp.raise_for_status()
            data = resp.json()
            for j in data.get("results") or []:
                jobs.append(self._job(j))
            if page + 1 >= (data.get("page_count") or 0):
                break
        return jobs

    @staticmethod
    def _job(j: dict) -> NormalizedJob:
        raw = [loc.get("name", "") for loc in j.get("locations") or []]
        remote = any("remote" in loc.lower() for loc in raw)
        locations = ["Remote" if loc == "Flexible / Remote" else loc for loc in raw if loc]
        levels = [lv.get("name", "") for lv in j.get("levels") or [] if lv.get("name")]
        categories = [c.get("name", "") for c in j.get("categories") or [] if c.get("name")]
        description = html_to_text(j.get("contents") or "")
        if levels:  # The Muse's own experience level, kept with the text since there's no field for it
            description = f"Experience level: {', '.join(levels)}\n\n{description}"
        return NormalizedJob(
            external_id=str(j["id"]),
            title=(j.get("name") or "").strip(),
            url=(j.get("refs") or {}).get("landing_page") or "",
            locations=locations,
            remote=remote,
            description=description,
            department=categories[0] if categories else "",
            posted_at=parse_iso(j.get("publication_date")),
            company_name=((j.get("company") or {}).get("name") or "").strip(),
        )
