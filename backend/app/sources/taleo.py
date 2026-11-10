"""Oracle Taleo Enterprise career sections ({tenant}.taleo.net/careersection/{section}/jobsearch.ftl).

slug: "{tenant}|{section}" or "{tenant}|{section}|{portal}", e.g. "textron|textron",
"drhorton|2", "massanf|ex". `tenant` may be a full host for career sections on an employer's own
domain (anything with a dot, e.g. "careers.stlouis-mo.gov|cstl_career_portal_ext").

Verified live (Nov 2026); *.taleo.net has no robots.txt (404), so nothing is disallowed:
- The search page itself calls POST /careersection/rest/jobboard/searchjobs?lang=en&portal={portal}
  with a JSON body (fieldData, filterSelectionParam, sortingSelection, pageNo) and gets
  {"requisitionList": [{"jobId", "contestNo", "column": [...], "linkedColumn", "locationsColumns"}],
  "pagingData": {"currentPageNo", "pageSize" (25), "totalCount"}}. The portal number identifies the
  career section; it's in the search page's JS (`portalNo: '8140753014'`), so it is read once from
  jobsearch.ftl when the slug doesn't carry it.
- The columns are configured per section: usually [title, '["US-Kansas-Wichita"]', '11/09/2026'].
  Locations are JSON lists of "Country-State-City" paths; the posted date is whichever other column
  parses as a date. Mobile sections ("joinexternalmobile") return no columns at all, so they can't be
  read: use the section's desktop name instead.
- sortBySelectionParam "3" descending lists the newest postings first, so a partial scan reads only
  the first pages.
- jobdetail.ftl?job={contestNo} has no JSON-LD; the description is in the page's
  api.fillList('requisitionDescriptionInterface', 'descRequisition', [...]) call, as URL-encoded
  HTML strings that start with "!*!" (description, then qualifications; each appears twice).
Legacy sections whose jobsearch.ftl redirects to joblist.ftl (e.g. kearney|01) have no REST search
and fail with a clear error, as do tenants that moved off Taleo (energytransfer -> SelectMinds).
Taleo Business Edition (*.tbe.taleo.net/.../ats/careers/v2/...) is a different product with
session-based HTML paging; it isn't covered here.
"""

import asyncio
import json
import re
from collections import Counter
from collections.abc import Set
from urllib.parse import unquote, urlparse, parse_qs

import httpx

from app.pipeline.normalize import html_to_text
from app.sources import http_util
from app.sources.base import Adapter, NormalizedJob
from app.sources.hr_common import outside_us, parse_day

MAX_PAGES = 200  # 5,000 jobs
MAX_PARTIAL_PAGES = 3
DETAIL_CONCURRENCY = 3
HEADERS = {"Accept": "application/json", "tz": "GMT-05:00", "tzname": "America/New_York"}

_PORTAL = re.compile(r"portalNo:\s*'(\d+)'")
_URL = re.compile(
    r"https?://([A-Za-z0-9.-]+)/careersection/([A-Za-z0-9_+%.-]+)/(?:jobsearch|jobdetail|moresearch|joblist|mysearches)\.ftl[^\s\"'<>]*",
    re.I,
)
_NOT_SECTIONS = {"rest", "theme", "feed", "iam"}
PAGE_MARKER = re.compile(r"\.taleo\.net/careersection/|/careersection/[^/\"'\s]+/(?:jobsearch|jobdetail|moresearch)\.ftl", re.I)
_FILL = re.compile(r"api\.fillList\('requisitionDescriptionInterface',\s*'descRequisition',\s*\[(.*?)\]\);", re.S)
_JS_STRING = re.compile(r"'((?:[^'\\]|\\.)*)'")


def parse_slug(slug: str) -> tuple[str, str, str]:
    parts = [p.strip() for p in slug.split("|")]
    if len(parts) not in (2, 3) or not parts[0] or not parts[1]:
        raise ValueError(f"Taleo slug must look like 'tenant|section[|portal]', got {slug!r}")
    host = parts[0] if "." in parts[0] else f"{parts[0]}.taleo.net"
    return host.lower(), parts[1], parts[2] if len(parts) == 3 else ""


def detect(url: str) -> str | None:
    """'tenant|section[|portal]' for a Taleo Enterprise career-section URL, else None."""
    m = _URL.match(url.replace("&amp;", "&").strip())
    if not m:
        return None
    host, section = m.group(1).lower(), m.group(2)
    if section.lower() in _NOT_SECTIONS or re.match(r"^\d{4}PRD", section) or ".tbe." in host:
        return None
    tenant = host.removesuffix(".taleo.net") if host.endswith(".taleo.net") else host
    portal = (parse_qs(urlparse(m.group(0)).query).get("portal") or [""])[0]
    return f"{tenant}|{section}|{portal}" if portal.isdigit() else f"{tenant}|{section}"


def slug_from_page(url: str, html: str) -> str | None:
    """The career section a company's careers page links to most (desktop sections preferred)."""
    found = Counter(s for s in (detect(m.group(0)) for m in _URL.finditer(html.replace("&amp;", "&"))) if s)
    if not found:
        return None
    desktop = [s for s, _ in found.most_common() if "mobile" not in s.split("|")[1].lower()]
    return desktop[0] if desktop else found.most_common(1)[0][0]


def _place(path: str) -> str:
    """'US-Kansas-Wichita' -> 'Wichita, Kansas, US'; 'TX-Arlington' -> 'Arlington, TX'."""
    parts = [p.strip() for p in path.split("-") if p.strip()]
    if len(parts) > 3:  # a hyphenated city ("US-NC-Winston-Salem")
        parts = parts[:2] + ["-".join(parts[2:])]
    return ", ".join(reversed(parts))


def _column_locations(value: str) -> list[str]:
    try:
        raw = json.loads(value)
    except ValueError:
        raw = [value]
    out = []
    for item in raw if isinstance(raw, list) else [raw]:
        text = _place(str(item)) if item else ""
        if text and text not in out:
            out.append(text)
    return out


def parse_row(row: dict) -> dict | None:
    cols = [c if isinstance(c, str) else "" for c in row.get("column") or []]
    if not cols or not row.get("contestNo"):
        return None
    title_at = row.get("linkedColumn") or 0
    loc_at = set(row.get("locationsColumns") or [])
    locations: list[str] = []
    for i in sorted(loc_at):
        if i < len(cols):
            locations += [loc for loc in _column_locations(cols[i]) if loc not in locations]
    posted = None
    for i, col in enumerate(cols):
        if i != title_at and i not in loc_at and posted is None:
            posted = parse_day(col)
    return {
        "id": str(row["contestNo"]),
        "title": cols[title_at].strip() if title_at < len(cols) else "",
        "locations": locations,
        "posted_at": posted,
    }


def parse_description(html: str) -> str:
    m = _FILL.search(html)
    if not m:
        return ""
    parts: list[str] = []
    for raw in _JS_STRING.findall(m.group(1)):
        if raw.startswith("!*!"):
            text = html_to_text(unquote(raw[3:].replace("\\'", "'")))
            if text and text not in parts:
                parts.append(text)
    return "\n\n".join(parts)


def _body(page: int) -> dict:
    return {
        "multilineEnabled": False,
        "sortingSelection": {"sortBySelectionParam": "3", "ascendingSortingOrder": "false"},
        "fieldData": {"fields": {"KEYWORD": "", "LOCATION": ""}, "valid": True},
        "filterSelectionParam": {"searchFilterSelections": []},
        "advancedSearchFiltersSelectionParam": {"searchFilterSelections": []},
        "pageNo": page,
    }


class TaleoAdapter(Adapter):
    ats = "taleo"
    min_interval_minutes = 30
    full_scan_every = 4
    lane = "slow"
    max_concurrency = 4
    _portals: dict[str, str] = {}

    async def _portal(self, client: httpx.AsyncClient, host: str, section: str) -> str:
        key = f"{host}|{section}"
        if key not in self._portals:
            # Some tenants send a corrupt gzip body on this page, so ask for it uncompressed.
            resp = await http_util.get(
                client, f"https://{host}/careersection/{section}/jobsearch.ftl", params={"lang": "en"},
                headers={"Accept-Encoding": "identity"}, follow_redirects=True,
            )
            m = _PORTAL.search(resp.text)
            if not m:
                # Legacy sections redirect to joblist.ftl (no REST search); others moved off Taleo.
                raise ValueError(f"no Taleo REST career section at {host}/{section} (ended at {resp.url})")
            self._portals[key] = m.group(1)
        return self._portals[key]

    async def fetch(
        self, client: httpx.AsyncClient, slug: str, *, full: bool = True, known_ids: Set[str] = frozenset(), **_
    ) -> list[NormalizedJob]:
        host, section, portal = parse_slug(slug)
        portal = portal or await self._portal(client, host, section)
        base = f"https://{host}/careersection"
        listed: dict[str, dict] = {}
        pages, page = 1, 1
        limit = MAX_PAGES if full else MAX_PARTIAL_PAGES
        while page <= min(pages, limit):
            resp = await http_util.post(
                client, f"{base}/rest/jobboard/searchjobs", params={"lang": "en", "portal": portal},
                json=_body(page), headers=HEADERS,
            )
            data = resp.json()
            rows = data.get("requisitionList") or []
            paging = data.get("pagingData") or {}
            size = paging.get("pageSize") or 25
            pages = -(-(paging.get("totalCount") or 0) // size)
            parsed = [r for r in (parse_row(r) for r in rows) if r]
            fresh = [r for r in parsed if r["id"] not in listed]
            if rows and not any(r["title"] for r in parsed):
                raise ValueError(f"Taleo section {host}/{section} lists no columns (a mobile section?)")
            if not fresh:
                break
            listed.update((r["id"], r) for r in fresh)
            page += 1

        sem = asyncio.Semaphore(DETAIL_CONCURRENCY)

        async def build(r: dict) -> NormalizedJob:
            url = f"{base}/{section}/jobdetail.ftl?job={r['id']}&lang=en"
            job = NormalizedJob(
                external_id=r["id"], title=r["title"], url=url, locations=r["locations"], posted_at=r["posted_at"],
                remote=any("remote" in loc.lower() for loc in r["locations"]),
            )
            if r["id"] not in known_ids:
                async with sem:
                    try:
                        resp = await http_util.get(client, url)
                        job.description = parse_description(resp.text)
                    except httpx.HTTPError:
                        pass  # listed without a description; enrichment retries later
            return job

        kept = [r for r in listed.values() if r["title"] and not outside_us(r["locations"])]
        return list(await asyncio.gather(*(build(r) for r in kept)))
