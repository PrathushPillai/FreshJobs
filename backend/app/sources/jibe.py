"""iCIMS Jibe career sites (careers.ice.com, careers.siriusxm.com, careers.heb.com/careers-home,
careers.footlocker.com, {client}.jibeapply.com, ...).

slug: "{host}" or "{host}/{base path}" when the site lives under a path, e.g. "careers.heb.com/careers-home".
The base path only shapes the job links (https://{host}/{base}/jobs/{slug}); the API is always at the host root.

Endpoint (what the site's own search page calls; verified live, Nov 2026, no login):
GET https://{host}/api/jobs?page=N&limit=100&sortBy=posted_date&descending=true&country=United States
-> {"totalCount", "count", "jobs": [{"data": {slug, req_id, title, description (HTML), city, state, country,
country_code, posted_date, apply_url, ...}}]}. Newest first, US-filtered server-side, descriptions included,
so no per-job requests. Partial scans stop at the first page with nothing new.
robots.txt on every site checked: "User-agent: * / Allow: / crawl-delay: 5", so pages of one site are
requested at least CRAWL_DELAY seconds apart.
"""

import asyncio
import re
from collections.abc import Set
from urllib.parse import urlsplit

import httpx

from app.pipeline.normalize import html_to_text
from app.sources import http_util
from app.sources.base import Adapter, NormalizedJob, parse_iso

PAGE_SIZE = 100
MAX_PAGES = 200  # 20,000 jobs
MAX_PARTIAL_PAGES = 5
CRAWL_DELAY = 5.0  # robots.txt crawl-delay on Jibe sites

PAGE_MARKER = re.compile(r"jibecdn\.com|window\._jibe\b", re.I)
_BASE_HREF = re.compile(r'<base\s+href="([^"]*)"', re.I)
_JOB_PATH = re.compile(r"^(/[\w-]+)?/jobs(?:/\d+)?/?$")


def parse_slug(slug: str) -> tuple[str, str]:
    """'careers.heb.com/careers-home' -> ('careers.heb.com', '/careers-home'); 'careers.ice.com' -> (..., '')."""
    s = re.sub(r"^https?://", "", slug.strip()).strip("/")
    host, _, base = s.partition("/")
    if not host or "." not in host:
        raise ValueError(f"Jibe slug must look like 'host' or 'host/base-path', got {slug!r}")
    return host.lower(), ("/" + base.strip("/")) if base.strip("/") else ""


def _slug(host: str, base: str) -> str:
    return host + (base if base and base != "/" else "")


def detect(url: str) -> str | None:
    """The slug for a URL that is recognisably Jibe: a *.jibeapply.com host, or a "/careers-home" job path
    (the default Jibe base path). Other employer domains need slug_from_page."""
    parts = urlsplit(url if "://" in url else "https://" + url)
    host = (parts.hostname or "").lower()
    path = parts.path or ""
    if host.endswith(".jibeapply.com"):
        m = _JOB_PATH.match(path)
        return _slug(host, (m.group(1) or "") if m else "")
    if host and re.match(r"^/careers-home(/jobs(/\d+)?)?/?$", path):
        return _slug(host, "/careers-home")
    return None


def slug_from_page(url: str, html: str) -> str | None:
    """The slug for a careers page built on Jibe (host plus its <base href> path)."""
    if not PAGE_MARKER.search(html):
        return None
    host = (urlsplit(url if "://" in url else "https://" + url).hostname or "").lower()
    if not host:
        return None
    m = _BASE_HREF.search(html)
    base = (m.group(1) if m else "").strip()
    if base.startswith("http"):
        base = urlsplit(base).path
    return _slug(host, "/" + base.strip("/") if base.strip("/") else "")


def _additional(d: dict) -> list:
    """A job's other places: `additional_locations` (a list of address dicts; `multipleLocations` is only a flag)."""
    extra = d.get("additional_locations")
    return extra if isinstance(extra, list) else []


def job_locations(d: dict) -> list[str]:
    def one(x: dict) -> str:
        parts = [x.get("city"), x.get("state"), x.get("country_code") or x.get("country")]
        return ", ".join(p.strip() for p in parts if isinstance(p, str) and p.strip())

    locs = [one(d) or (d.get("full_location") or d.get("location_name") or "").strip()]
    for extra in _additional(d):
        if isinstance(extra, dict):
            locs.append(one(extra))
        elif isinstance(extra, str):
            locs.append(extra.strip())
    return list(dict.fromkeys(x for x in locs if x))


def _is_us(d: dict) -> bool:
    if (d.get("country_code") or "").upper() == "US" or (d.get("country") or "").lower() in (
            "united states", "united states of america", "usa"):
        return True
    return any(isinstance(x, dict) and (x.get("country_code") or "").upper() == "US"
               for x in _additional(d))


class JibeAdapter(Adapter):
    ats = "jibe"
    # One request per 100 jobs, with the sites' 5 s crawl-delay between them: a 2,000-job site is ~100 s.
    min_interval_minutes = 20
    full_scan_every = 6
    lane = "slow"
    max_concurrency = 4

    def __init__(self) -> None:
        self._complete: dict[str, bool] = {}

    def last_fetch_complete(self, slug: str) -> bool:
        return self._complete.get(slug, True)

    def _job(self, host: str, base: str, d: dict) -> NormalizedJob:
        locations = job_locations(d)
        lang = d.get("language") or "en-us"
        tags = [t for k in ("tags1", "tags2", "tags3") for t in (d.get(k) or []) if isinstance(t, str)]
        text = " ".join(locations + tags + [d.get("location_name") or "", d.get("title") or ""]).lower()
        category = d.get("category")
        department = d.get("department") or (category[0] if isinstance(category, list) and category else "")
        return NormalizedJob(
            external_id=str(d["slug"]),
            title=(d.get("title") or "").strip(),
            url=f"https://{host}{base}/jobs/{d['slug']}?lang={lang}",
            locations=locations,
            remote="remote" in text or (d.get("location_type") or "").upper() == "REMOTE",
            description=html_to_text(d.get("description") or ""),
            department=department if isinstance(department, str) else "",
            posted_at=parse_iso(d.get("posted_date") or d.get("create_date")),
        )

    async def fetch(
        self, client: httpx.AsyncClient, slug: str, *, full: bool = True, known_ids: Set[str] = frozenset(),
        first_scan: bool = False,
    ) -> list[NormalizedJob]:
        host, base = parse_slug(slug)
        full = full or first_scan
        by_id: dict[str, NormalizedJob] = {}
        complete = True
        total = None
        for page in range(1, (MAX_PAGES if full else MAX_PARTIAL_PAGES) + 1):
            if page > 1:
                await asyncio.sleep(CRAWL_DELAY)
            resp = await http_util.get(client, f"https://{host}/api/jobs", params={
                "page": page, "limit": PAGE_SIZE, "sortBy": "posted_date", "descending": "true",
                "country": "United States",
            }, headers={"Accept": "application/json"})
            data = resp.json()
            total = data.get("totalCount") if total is None else total
            rows = [r.get("data") or {} for r in data.get("jobs") or []]
            fresh = 0
            for d in rows:
                if not d.get("slug") or not (d.get("title") or "").strip():
                    continue
                if not _is_us(d):
                    continue
                key = str(d["slug"])
                if key in by_id and not (d.get("language") or "").lower().startswith("en"):
                    continue  # the same job in another language
                fresh += key not in known_ids and key not in by_id
                by_id[key] = self._job(host, base, d)
            if len(rows) < PAGE_SIZE or page * PAGE_SIZE >= (total or 0):
                break
            if not full and not fresh:
                break
        else:
            complete = not full  # ran out of pages on a full scan
        self._complete[slug] = complete
        return list(by_id.values())
