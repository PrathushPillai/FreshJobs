"""Phenom People career sites (jobs.cvshealth.com, careers.pentair.com, careers.xoriant.com, ...).

slug: "{host}/{country}/{lang}" as in the site's URLs, e.g. "careers.pentair.com/us/en",
"careers.xoriant.com/in/en". A bare host means "/us/en".

What the public pages use (verified live, Nov 2026; no login, no token):
- POST https://{host}/widgets with {"ddoKey": "refineSearch", "from", "size", "selected_fields", ...} ->
  {"refineSearch": {"totalHits", "data": {"jobs": [...], "aggregations": [{"field", "value": {name: count}}]}}}.
  This is what the search-results page calls. `size` up to 100 works; `from + size` is capped at 10,000
  (a deeper page comes back empty). `sortBy` is accepted but the order isn't by date, so every scan is full.
  US jobs are selected server-side with selected_fields.country, whose value is the site's own country name
  ("United States" at CVS, "United States of America" at Pentair), read from the country aggregation first.
  A tenant with more than 10,000 US jobs is paged per state (state aggregation); if that still can't reach
  every job, the scan is marked incomplete so nothing is closed.
- List entries carry only a teaser. The description is in the job page's embedded `phApp.ddo` JSON
  (jobDetail.data.job.description, HTML) at https://{host}/{country}/{lang}/job/{jobId}: one request per new job.
robots.txt (the same Phenom template on every site checked) disallows */apply, */jobcart, */chatbot, */iauth,
*/px-widgets and similar, but not /widgets or /job/ pages.

Many Phenom sites front another ATS (CVS and Pentair link to Workday); their jobs are then also found through
that ATS's own board, and dedupe marks the copies.
"""

import asyncio
import json
import re
from collections.abc import Set
from urllib.parse import urlsplit

import httpx

from app.pipeline.normalize import html_to_text
from app.sources import http_util
from app.sources.base import Adapter, NormalizedJob, parse_iso

PAGE_SIZE = 100
RESULT_CAP = 10_000  # from + size beyond this returns nothing
DETAIL_CONCURRENCY = 3
# A first scan reads descriptions for at most this many jobs (newest first); the rest are saved without one
# and filled in by later polls' enrichment, so a 19k-job tenant doesn't make 19k requests in one go.
FIRST_SCAN_DETAILS = 300
DETAILS_PER_FETCH = 600
US_NAMES = {"united states", "united states of america", "usa", "us", "u.s.", "u.s.a."}

PAGE_MARKER = re.compile(r"cdn\.phenompeople\.com|phApp\.ddo\s*=|phenompeople", re.I)
_LOCALE_PATH = re.compile(r"^/([a-z]{2})/([a-z]{2})(?:/|$)", re.I)
_DDO = re.compile(r"phApp\.ddo\s*=\s*")


def parse_slug(slug: str) -> tuple[str, str, str]:
    """'careers.pentair.com/us/en' -> ('careers.pentair.com', 'us', 'en')."""
    s = slug.strip().strip("/")
    s = re.sub(r"^https?://", "", s)
    host, _, path = s.partition("/")
    if not host or "." not in host:
        raise ValueError(f"Phenom slug must look like 'host/us/en', got {slug!r}")
    m = _LOCALE_PATH.match("/" + path) if path else None
    cc, lang = (m.group(1).lower(), m.group(2).lower()) if m else ("us", "en")
    return host.lower(), cc, lang


def detect(url: str) -> str | None:
    """The slug for a Phenom-hosted URL ({x}.phenompeople.com). Employers' own domains can't be told apart from
    the URL alone (their paths look like any site's /us/en/...): use slug_from_page for those."""
    parts = urlsplit(url if "://" in url else "https://" + url)
    host = (parts.hostname or "").lower()
    if not host.endswith(".phenompeople.com") or host.startswith(("cdn.", "content-", "assets")):
        return None
    m = _LOCALE_PATH.match(parts.path or "")
    return f"{host}/{m.group(1).lower()}/{m.group(2).lower()}" if m else host


def ddo(html: str) -> dict:
    """The page's embedded `phApp.ddo = {...}` JSON ({} if none)."""
    m = _DDO.search(html)
    if not m:
        return {}
    try:
        data, _ = json.JSONDecoder().raw_decode(html, m.end())
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def slug_from_page(url: str, html: str) -> str | None:
    """The slug for a careers page built on Phenom (its host plus the country/language its URLs use)."""
    if not PAGE_MARKER.search(html):
        return None
    data = ddo(html)
    if not data:
        return None
    host = (urlsplit(url if "://" in url else "https://" + url).hostname or "").lower()
    if not host:
        return None
    m = _LOCALE_PATH.match(urlsplit(url).path or "")
    if m:
        return f"{host}/{m.group(1).lower()}/{m.group(2).lower()}"
    raw = json.dumps(data.get("siteConfig") or data)[:200_000]
    cc = re.search(r'"country"\s*:\s*"([a-z]{2})"', raw)
    locale = re.search(r'"locale"\s*:\s*"([a-z]{2})_[a-z]{2}"', raw, re.I)
    return f"{host}/{cc.group(1) if cc else 'us'}/{locale.group(1).lower() if locale else 'en'}"


_ZIP = re.compile(r"^[A-Z0-9]{2,5}(?:[- ][A-Z0-9]{2,4})?$")


def clean_location(loc: str) -> str:
    """'294 HARVARD STREET, Brookline, Massachusetts,United States' -> 'Brookline, Massachusetts, United States';
    'Delavan, Wisconsin, United States of America, 53115-2011' -> 'Delavan, Wisconsin, United States'."""
    parts = [p.strip() for p in (loc or "").split(",")]
    kept = []
    for p in parts:
        if not p or p.lower() == "unspecified" or p[0].isdigit() or (_ZIP.match(p) and any(c.isdigit() for c in p)):
            continue
        kept.append("United States" if p.lower() in US_NAMES else p)
    return ", ".join(kept)


def job_locations(j: dict) -> list[str]:
    raw = j.get("multi_location") or ([j["location"]] if j.get("location") else [])
    locs = [clean_location(x) for x in raw if isinstance(x, str)]
    if not any(locs):
        locs = [clean_location(", ".join(x for x in (j.get("city"), j.get("state"), j.get("country")) if x))]
    return list(dict.fromkeys(x for x in locs if x))


def _is_us(j: dict) -> bool:
    return (j.get("country") or "").strip().lower() in US_NAMES or (j.get("countryCode") or "").upper() == "US"


class PhenomAdapter(Adapter):
    ats = "phenom"
    # Every scan is full (the list has no date order): one POST per 100 jobs, plus one page per new job.
    min_interval_minutes = 120
    lane = "slow"
    max_concurrency = 4

    def __init__(self) -> None:
        self._complete: dict[str, bool] = {}

    def last_fetch_complete(self, slug: str) -> bool:
        return self._complete.get(slug, True)

    async def _search(self, client, host, cc, lang, selected: dict, start: int, size: int,
                      facets: list[str] | None = None) -> tuple[list[dict], int, dict[str, dict]]:
        body = {
            "lang": f"{lang}_{cc}", "deviceType": "desktop", "country": cc, "pageName": "search-results",
            "ddoKey": "refineSearch", "sortBy": "", "subsearch": "", "from": start, "jobs": True,
            "counts": bool(facets), "all_fields": facets or [], "size": size, "clearAll": False,
            "jdsource": "facets", "isSliderEnable": False, "pageId": "page20", "siteType": "external",
            "keywords": "", "global": True, "selected_fields": selected, "locationData": {},
        }
        resp = await http_util.post(client, f"https://{host}/widgets", json=body,
                                    headers={"Accept": "application/json", "Content-Type": "application/json"})
        rs = resp.json().get("refineSearch") or {}
        data = rs.get("data") or {}
        aggs = {a.get("field"): a.get("value") or {} for a in data.get("aggregations") or [] if isinstance(a, dict)}
        return data.get("jobs") or [], int(rs.get("totalHits") or 0), aggs

    async def _page_all(self, client, host, cc, lang, selected: dict, total: int, out: dict[str, dict]) -> int:
        """Pages one selection into `out` (by jobId); returns how many it could reach (≤ RESULT_CAP)."""
        start = 0
        while start < min(total, RESULT_CAP):
            jobs, total_now, _ = await self._search(client, host, cc, lang, selected, start,
                                                    min(PAGE_SIZE, RESULT_CAP - start))
            if not jobs:
                break
            for j in jobs:
                if j.get("jobId") and _is_us(j):
                    out.setdefault(str(j["jobId"]), j)
            start += len(jobs)
            total = total_now or total
        return start

    async def _list(self, client, slug, host, cc, lang) -> list[dict]:
        _, _, aggs = await self._search(client, host, cc, lang, {}, 0, 1, ["country"])
        us_keys = [k for k in (aggs.get("country") or {}) if k.strip().lower() in US_NAMES]
        self._complete[slug] = True
        if not us_keys:
            return []
        selected = {"country": us_keys}
        out: dict[str, dict] = {}
        first, total, state_aggs = await self._search(client, host, cc, lang, selected, 0, PAGE_SIZE,
                                                      ["state"])
        for j in first:
            if j.get("jobId") and _is_us(j):
                out.setdefault(str(j["jobId"]), j)
        if total <= RESULT_CAP:
            if total > len(first):
                await self._page_all(client, host, cc, lang, selected, total, out)
            return list(out.values())
        # Over the cap: page each state on its own.
        reached = 0
        for state, count in (state_aggs.get("state") or {}).items():
            if not count:
                continue
            got = await self._page_all(client, host, cc, lang, {**selected, "state": [state]}, int(count), out)
            reached += min(got, int(count))
        if len(out) < total or reached < total:
            self._complete[slug] = False
        return list(out.values())

    def _summary(self, host: str, cc: str, lang: str, j: dict) -> NormalizedJob:
        locations = job_locations(j)
        text = " ".join(locations + [j.get("title") or "", j.get("type") or ""]).lower()
        return NormalizedJob(
            external_id=str(j["jobId"]),
            title=(j.get("title") or "").strip(),
            url=f"https://{host}/{cc}/{lang}/job/{j['jobId']}",
            locations=locations,
            remote="remote" in text,
            department=j.get("category") or j.get("department") or "",
            posted_at=parse_iso(j.get("postedDate") or j.get("dateCreated")),
        )

    async def _description(self, client, url: str) -> str | None:
        html = (await http_util.get(client, url, follow_redirects=True)).text
        job = ((ddo(html).get("jobDetail") or {}).get("data") or {}).get("job") or {}
        raw = job.get("description") or http_util.json_ld_job(html).get("description") or ""
        return html_to_text(raw) if raw else None

    async def fetch(
        self, client: httpx.AsyncClient, slug: str, *, full: bool = True, known_ids: Set[str] = frozenset(),
        first_scan: bool = False,
    ) -> list[NormalizedJob]:
        host, cc, lang = parse_slug(slug)
        raw = await self._list(client, slug, host, cc, lang)
        jobs = [self._summary(host, cc, lang, j) for j in raw if (j.get("title") or "").strip()]
        new = [j for j in jobs if j.external_id not in known_ids]
        new.sort(key=lambda j: j.posted_at.timestamp() if j.posted_at else 0, reverse=True)
        budget = FIRST_SCAN_DETAILS if first_scan else DETAILS_PER_FETCH
        sem = asyncio.Semaphore(DETAIL_CONCURRENCY)

        async def describe(job: NormalizedJob) -> None:
            async with sem:
                try:
                    text = await self._description(client, job.url)
                except (httpx.HTTPError, ValueError):
                    return  # saved without a description; enrichment retries it
            if text:
                job.description = text

        await asyncio.gather(*(describe(j) for j in new[:budget]))
        return jobs
