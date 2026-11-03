"""Adzuna job-search API (aggregator; needs ADZUNA_APP_ID / ADZUNA_APP_KEY from developer.adzuna.com).

slug: optional query string of extra Adzuna search parameters (`what`, `where`, `category`,
`what_exclude`, ...), plus an optional `pages` (default 3, max 5). Example: "category=it-jobs".
Empty, or any string without "=", means every job in `settings.adzuna_country`.

Each poll reads the newest jobs from the last 2 days (sort_by=date, max_days_old=2), 50 per page,
stopping early on a short page. Free-tier limits are about 25 calls/minute and 250/day *per key*,
shared by every adzuna entry: 3 pages every 30 minutes is at most 144 calls/day for one entry.
Adzuna's `description` is a snippet of a few hundred characters, not the full ad.
"""

from urllib.parse import parse_qsl

import httpx

from app.config import settings
from app.pipeline.normalize import html_to_text
from app.sources.base import Adapter, NormalizedJob, parse_iso

PER_PAGE = 50
DEFAULT_PAGES = 3
MAX_PAGES = 5


class AdzunaAdapter(Adapter):
    ats = "adzuna"
    complete_feed = False
    min_interval_minutes = 30

    def is_configured(self) -> bool:
        return bool(settings.adzuna_app_id and settings.adzuna_app_key)

    async def fetch(self, client: httpx.AsyncClient, slug: str, **_) -> list[NormalizedJob]:
        pages = DEFAULT_PAGES
        extra = []
        for key, value in parse_qsl(slug or ""):
            if key == "pages":
                pages = max(1, min(MAX_PAGES, int(value)))
            else:
                extra.append((key, value))

        jobs = []
        for page in range(1, pages + 1):
            params = [
                ("app_id", settings.adzuna_app_id),
                ("app_key", settings.adzuna_app_key),
                ("results_per_page", str(PER_PAGE)),
                ("sort_by", "date"),
                ("max_days_old", "2"),
                ("content-type", "application/json"),
                *extra,
            ]
            resp = await client.get(
                f"https://api.adzuna.com/v1/api/jobs/{settings.adzuna_country}/search/{page}", params=params
            )
            resp.raise_for_status()
            results = resp.json().get("results") or []
            jobs += [self._job(j) for j in results]
            if len(results) < PER_PAGE:
                break
        return jobs

    @staticmethod
    def _job(j: dict) -> NormalizedJob:
        loc = j.get("location") or {}
        location = loc.get("display_name") or ", ".join(reversed((loc.get("area") or [])[1:]))
        title = html_to_text(j.get("title") or "")  # Adzuna wraps search hits in <strong>
        return NormalizedJob(
            external_id=str(j["id"]),
            title=title,
            url=j.get("redirect_url") or "",
            locations=[location] if location else [],
            remote="remote" in f"{title} {location}".lower(),
            description=html_to_text(j.get("description") or ""),
            department=(j.get("category") or {}).get("label") or "",
            posted_at=parse_iso(j.get("created")),
            company_name=((j.get("company") or {}).get("display_name") or "").strip(),
        )
