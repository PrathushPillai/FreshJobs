"""Manatal career pages (www.careers-page.com/{slug}), used by small recruiting and staffing agencies.

slug: the career-page name, e.g. "manatal".

Verified live (Nov 2026):
- The career page loads its jobs from Manatal's open API, core.api.manatal.com/open/v3/career-page/{slug}/jobs/
  (no key, no login; neither host has a robots.txt). It's paginated DRF-style ({count, next, results}), and
  each result has the full HTML description, city/state/country and is_remote, but no posting date.
- Job pages are www.careers-page.com/{slug}/job/{hash}.
"""

import re

import httpx

from app.pipeline.normalize import html_to_text
from app.sources import http_util
from app.sources.base import Adapter, NormalizedJob
from app.sources.staffing_common import outside_us, page_urls

API = "https://core.api.manatal.com/open/v3/career-page/{slug}/jobs/"
PAGE_SIZE = 100
MAX_PAGES = 30
_DETECT = re.compile(r"^https?://(?:www\.)?careers-page\.com/([A-Za-z0-9_-]+)(?:[/?#]|$)", re.I)
_API_SLUG = re.compile(r"core\.api\.manatal\.com/open/v\d+/career-page/([A-Za-z0-9_-]+)", re.I)
_RESERVED = {"static", "api", "assets"}

PAGE_MARKER = re.compile(r"careers-page\.com/[A-Za-z0-9_-]+|core\.api\.manatal\.com", re.I)


def detect(url: str) -> str | None:
    url = (url or "").strip()
    m = _DETECT.match(url) or _API_SLUG.search(url)
    if not m or m.group(1).lower() in _RESERVED:
        return None
    return m.group(1).lower()


def slug_from_page(url: str, html: str) -> str | None:
    for link in page_urls(html):
        slug = detect(link)
        if slug:
            return slug
    return None


def _flag(value) -> bool:
    return value is True or str(value).lower() == "true"


class ManatalAdapter(Adapter):
    ats = "manatal"
    min_interval_minutes = 15
    max_concurrency = 4

    async def fetch(self, client: httpx.AsyncClient, slug: str, **_) -> list[NormalizedJob]:
        slug = slug.strip().lower()
        url: str | None = API.format(slug=slug)
        params: dict | None = {"page_size": PAGE_SIZE}
        jobs = []
        for _page in range(MAX_PAGES):
            if not url:
                break
            data = (await http_util.get(client, url, params=params)).json()
            for j in data.get("results") or []:
                if not isinstance(j, dict) or not j.get("hash") or not j.get("position_name"):
                    continue
                place = j.get("location_display") or ", ".join(
                    p for p in (j.get("city"), j.get("state"), j.get("country")) if p
                )
                remote = _flag(j.get("is_remote"))
                if outside_us([place]):
                    continue
                jobs.append(
                    NormalizedJob(
                        external_id=str(j.get("id") or j["hash"]),
                        title=j["position_name"].strip(),
                        url=f"https://www.careers-page.com/{slug}/job/{j['hash']}",
                        locations=[place] if place else [],
                        remote=remote,
                        description=html_to_text(j.get("description") or ""),
                        department=j.get("organization_name") or "",
                    )
                )
            url, params = data.get("next"), None
        return jobs
