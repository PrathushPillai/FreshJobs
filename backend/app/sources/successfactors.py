"""SAP SuccessFactors Recruiting Marketing (RMK) career sites, e.g. careers.ey.com, careers.wipro.com.

slug: "{host}" or "{host}|{filters}", e.g. "careers.ey.com|optionsFacetsDD_country=US" or
"careers.wipro.com|jobLocationCountry=United States".

RMK sites come in two generations (verified live, Nov 2026), detected per host on first use:
- "unify" sites (newer, JS-rendered): POST /services/recruiting/v1/jobs
  {"locale", "pageNumber" (0-based), "sortBy": "date", "facetFilters": {...}, ...} ->
  {"totalJobs", "jobSearchResult": [{"response": {id, unifiedStandardTitle, urlTitle,
  jobLocationShort, unifiedStandardStart ("11/3/26"), ...}}]}, 10 a page, newest first.
  Filters become facetFilters ({"jobLocationCountry": ["United States"]}).
  Job page: /job/{urlTitle}/{id}-en_US/.
- classic sites: GET /search/?q=&sortColumn=referencedate&sortDirection=desc&startrow=N (25 a page),
  HTML rows with /…/job/{title-slug}/{id}/ links and a <span class="jobLocation">. Filters become
  query parameters (optionsFacetsDD_country=US).
Either way the job page carries schema.org microdata (itemprop="description" / "datePosted"), and
descriptions need one request per new job. Some sites (jobs.sap.com) answer 403 to non-browser
clients: not supported, we don't work around that.
"""

import asyncio
import html as htmllib
import re
from collections.abc import Set
from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import parse_qsl

import httpx

from app.pipeline.normalize import html_to_text
from app.sources import http_util
from app.sources.base import Adapter, NormalizedJob

CLASSIC_PAGE = 25
UNIFY_PAGE = 10
MAX_PAGES = 400
MAX_PARTIAL_PAGES = 4
DETAIL_CONCURRENCY = 4
_ROW_LINK = re.compile(r'href="(/(?:[^"/]+/)?job/[^"]+?/(\d+)/)"[^>]*>(.*?)</a>', re.S)
_ROW = re.compile(r'<tr class="data-row[^"]*">(.*?)</tr>', re.S)
_ROW_LOCATION = re.compile(r'<span class="jobLocation[^"]*">\s*(.*?)\s*</span>', re.S)
_TOTAL = re.compile(r"Results\s*<b>[^<]*</b>\s*of\s*<b>([\d,]+)</b>")
_MORE = re.compile(r"\s*\+\s*\d+\s+more\W*$")  # "New York, NY, US, 10001 +3 more…"
_DATE_POSTED = re.compile(r'itemprop="datePosted"[^>]*content="([^"]+)"')


def parse_slug(slug: str) -> tuple[str, list[tuple[str, str]]]:
    host, _, filters = slug.partition("|")
    host = host.strip().removeprefix("https://").removeprefix("http://").rstrip("/")
    if not host:
        raise ValueError(f"SuccessFactors slug must look like 'host[|filters]', got {slug!r}")
    return host, parse_qsl(filters.strip())


def parse_date(value: str | None) -> datetime | None:
    """'Tue Nov 03 02:00:00 UTC 2026', '11/3/26' or '2026-11-03' -> midnight UTC that day."""
    text = (value or "").strip()
    for fmt in ("%a %b %d %H:%M:%S UTC %Y", "%m/%d/%y", "%m/%d/%Y", "%Y-%m-%d", "%b %d, %Y"):
        try:
            d = datetime.strptime(text, fmt)
            return http_util.midnight_utc(d.year, d.month, d.day)
        except ValueError:
            continue
    return None


class _ItempropText(HTMLParser):
    """Collects the inner HTML of every element with itemprop="description"."""

    VOID = {"br", "img", "hr", "meta", "input", "link", "area", "base", "col", "source", "wbr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.depth = 0
        self.parts: list[list[str]] = []

    def handle_starttag(self, tag, attrs):
        if self.depth:
            self.parts[-1].append(self.get_starttag_text() or "")
            if tag not in self.VOID:
                self.depth += 1
        elif dict(attrs).get("itemprop") == "description" and tag not in self.VOID:
            self.depth = 1
            self.parts.append([])

    def handle_endtag(self, tag):
        if self.depth:
            self.depth -= 1
            if self.depth:
                self.parts[-1].append(f"</{tag}>")

    def handle_data(self, data):
        if self.depth:
            self.parts[-1].append(data)

    def handle_entityref(self, name):
        if self.depth:
            self.parts[-1].append(f"&{name};")

    def handle_charref(self, name):
        if self.depth:
            self.parts[-1].append(f"&#{name};")


def page_description(html: str) -> str:
    parser = _ItempropText()
    parser.feed(html)
    texts = [html_to_text(htmllib.unescape("".join(p))) for p in parser.parts]
    return "\n\n".join(dict.fromkeys(t for t in texts if t))


def _first(r: dict, *keys: str) -> str:
    for key in keys:
        value = r.get(key)
        if isinstance(value, list):
            value = value[0] if value else ""
        if isinstance(value, str) and value.strip() and value.strip().lower() != "others":
            return value.strip()
    return ""


def unify_locations(r: dict) -> list[str]:
    """Location strings from a unify search result. Sites name these fields differently: Wipro has
    jobLocationShort ("San Jose, USA-CA, USA, 62682"), HCLTech custprimecity + custCountryRegion."""
    short = [html_to_text(x) for x in r.get("jobLocationShort") or []]
    if any(short):
        return [s for s in short if s]
    parts = [
        _first(r, "custprimecity", "sfstd_jobLocation_obj", "city"),
        _first(r, "jobLocationState", "state"),
        _first(r, "jobLocationCountry", "custCountryRegion", "country"),
    ]
    text = ", ".join(p for p in parts if p)
    return [text] if text else []


def parse_classic(html: str) -> tuple[list[dict], int]:
    rows = _ROW.findall(html) or [html]
    jobs: dict[str, dict] = {}
    for row in rows:
        loc = _ROW_LOCATION.search(row)
        for path, job_id, inner in _ROW_LINK.findall(row):
            if job_id not in jobs:
                jobs[job_id] = {
                    "id": job_id, "path": path, "title": html_to_text(inner),
                    "location": _MORE.sub("", html_to_text(loc.group(1))) if loc else "", "date": None,
                }
    total = _TOTAL.search(html)
    return list(jobs.values()), int(total.group(1).replace(",", "")) if total else 0


class SuccessFactorsAdapter(Adapter):
    ats = "successfactors"
    min_interval_minutes = 20
    full_scan_every = 6
    lane = "slow"

    def __init__(self) -> None:
        self._flavor: dict[str, str] = {}  # host -> "unify" | "classic"

    async def _unify_page(self, client, host, filters, page: int) -> tuple[list[dict], int] | None:
        facets: dict[str, list[str]] = {}
        for k, v in filters:
            facets.setdefault(k, []).append(v)
        body = {"locale": "en_US", "pageNumber": page, "sortBy": "date", "keywords": "", "location": "",
                "facetFilters": facets, "brand": "", "skills": [], "categoryId": 0, "alertId": "", "rcmCandidateId": ""}
        try:
            resp = await http_util.post(client, f"https://{host}/services/recruiting/v1/jobs", json=body)
            data = resp.json()
        except (httpx.HTTPStatusError, ValueError):
            if self._flavor.get(host) == "unify":
                raise
            return None
        if "jobSearchResult" not in data:
            return None
        jobs = []
        for item in data.get("jobSearchResult") or []:
            r = item.get("response") or {}
            if not r.get("id"):
                continue
            jobs.append({
                "id": str(r["id"]),
                "path": f"/job/{r.get('urlTitle') or 'job'}/{r['id']}-en_US/",
                "title": (r.get("unifiedStandardTitle") or "").strip(),
                "location": " | ".join(unify_locations(r)),
                "date": parse_date(r.get("unifiedStandardStart")),
            })
        return jobs, data.get("totalJobs") or 0

    async def _classic_page(self, client, host, filters, page: int) -> tuple[list[dict], int]:
        params = [("q", ""), ("sortColumn", "referencedate"), ("sortDirection", "desc"),
                  ("startrow", str(page * CLASSIC_PAGE)), *filters]
        resp = await http_util.get(client, f"https://{host}/search/", params=params)
        return parse_classic(resp.text)

    async def _page(self, client, host, filters, page: int) -> tuple[list[dict], int, int]:
        if self._flavor.get(host) != "classic":
            result = await self._unify_page(client, host, filters, page)
            if result is not None:
                self._flavor[host] = "unify"
                return result[0], result[1], UNIFY_PAGE
            self._flavor[host] = "classic"
        jobs, total = await self._classic_page(client, host, filters, page)
        return jobs, total, CLASSIC_PAGE

    async def fetch(
        self, client: httpx.AsyncClient, slug: str, *, full: bool = True, known_ids: Set[str] = frozenset()
    ) -> list[NormalizedJob]:
        host, filters = parse_slug(slug)
        listed: dict[str, dict] = {}
        for page in range(MAX_PAGES if full else MAX_PARTIAL_PAGES):
            jobs, total, size = await self._page(client, host, filters, page)
            fresh = [j for j in jobs if j["id"] not in listed]
            if not fresh:
                break
            listed.update((j["id"], j) for j in fresh)
            if (page + 1) * size >= total:
                break
            if not full and all(j["id"] in known_ids for j in fresh):
                break

        sem = asyncio.Semaphore(DETAIL_CONCURRENCY)

        async def build(j: dict) -> NormalizedJob | None:
            url = f"https://{host}{j['path']}"
            locations = [loc.strip() for loc in j["location"].split(" | ") if loc.strip()]
            job = NormalizedJob(
                external_id=j["id"], title=j["title"], url=url, locations=locations,
                remote=any("remote" in loc.lower() for loc in locations), posted_at=j["date"],
            )
            if j["id"] in known_ids:
                return job
            async with sem:
                try:
                    resp = await http_util.get(client, url)
                except httpx.HTTPError:
                    return None  # still new next poll
            job.description = page_description(resp.text)
            date = _DATE_POSTED.search(resp.text)
            job.posted_at = (parse_date(date.group(1)) if date else None) or job.posted_at
            return job

        jobs = await asyncio.gather(*(build(j) for j in listed.values()))
        return [j for j in jobs if j is not None]
