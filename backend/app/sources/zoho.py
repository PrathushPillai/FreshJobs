"""Zoho Recruit career sites ({slug}.zohorecruit.com/jobs/Careers), common at small IT consultancies.

slug: the subdomain, e.g. "zenatta". Other data centres or portal names use the full host and an optional
portal: "acme.zohorecruit.in", "acme/Jobs", "acme.zohorecruit.eu/Jobs" (the portal defaults to "Careers").

Verified live (Nov 2026):
- robots.txt allows /jobs, /jobs/*, /careers and /recruit/Portal.na for every agent and disallows the rest
  (the logged-in app), so only those pages are read.
- /jobs/{portal} carries every published opening as JSON in `<input type="hidden" value="[...]" id="jobs">`:
  id, Posting_Title, City, State, Country, Remote_Job, Date_Opened (YYYY-MM-DD), Job_Type, and the first
  ~150 characters of Job_Description.
- /jobs/{portal}/{id} has the full record in `var jobs = JSON.parse('...')` (a JS-escaped string), which is
  where the description comes from. Those pages are ~1.7 MB, so they're read only for new US jobs.
- A wrong subdomain answers 200 with "Page does not exist".
"""

import asyncio
import html as htmllib
import json
import re
from collections.abc import Set
from datetime import datetime, timezone

import httpx

from app.pipeline.normalize import html_to_text
from app.sources import http_util
from app.sources.base import Adapter, NormalizedJob
from app.sources.staffing_common import outside_us, page_urls, url_slug

DEFAULT_PORTAL = "Careers"
DETAIL_CONCURRENCY = 2

_LIST_INPUT = re.compile(r'<input\b[^>]*value="([^"]*)"[^>]*\bid="jobs"', re.S)
_LIST_INPUT_REV = re.compile(r'<input\b[^>]*\bid="jobs"[^>]*value="([^"]*)"', re.S)
_DETAIL_JS = re.compile(r"var\s+jobs\s*=\s*JSON\.parse\('((?:[^'\\]|\\.)*)'\)", re.S)
_JS_ESCAPE = re.compile(r"\\(x[0-9A-Fa-f]{2}|u[0-9A-Fa-f]{4}|.)", re.S)
_JS_SIMPLE = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}
_HOST = r"([a-z0-9-]+)\.(zohorecruit\.(?:com|in|eu|com\.au|jp|com\.cn|ca|sa|uk))"
_DETECT = re.compile(rf"^https?://{_HOST}(?:/(?:jobs/([A-Za-z0-9_-]+)|careers|recruit/Portal\.na)\b.*)?/?$", re.I)
_RESERVED = {"www", "recruit", "static", "accounts", "help"}

PAGE_MARKER = re.compile(r"[a-z0-9-]+\.zohorecruit\.[a-z.]+|static\.zohocdn\.com/recruit", re.I)


def parse_slug(slug: str) -> tuple[str, str]:
    host, _, portal = slug.strip().partition("/")
    host = host.lower()
    if "." not in host:
        host = f"{host}.zohorecruit.com"
    return host, portal or DEFAULT_PORTAL


def detect(url: str) -> str | None:
    m = _DETECT.match((url or "").strip())
    if not m or m.group(1).lower() in _RESERVED:
        return None
    sub, domain, portal = m.group(1).lower(), m.group(2).lower(), m.group(3)
    slug = sub if domain == "zohorecruit.com" else f"{sub}.{domain}"
    return f"{slug}/{portal}" if portal and portal != DEFAULT_PORTAL else slug


def slug_from_page(url: str, html: str) -> str | None:
    for link in page_urls(html):
        slug = detect(link)
        if slug:
            return slug
    return None


def js_unescape(s: str) -> str:
    """Decode a single-quoted JavaScript string literal's body (\\x22, \\u00e9, \\n, \\-, \\/ ...)."""

    def one(m: re.Match) -> str:
        esc = m.group(1)
        if esc[0] in "xu" and len(esc) > 1:
            return chr(int(esc[1:], 16))
        return _JS_SIMPLE.get(esc, esc)

    return _JS_ESCAPE.sub(one, s)


def parse_listing(html: str) -> list[dict]:
    m = _LIST_INPUT.search(html or "") or _LIST_INPUT_REV.search(html or "")
    if not m:
        return []
    try:
        data = json.loads(htmllib.unescape(m.group(1)))
    except ValueError:
        return []
    return [j for j in data if isinstance(j, dict) and j.get("id")] if isinstance(data, list) else []


def parse_detail(html: str) -> dict:
    m = _DETAIL_JS.search(html or "")
    if not m:
        return {}
    try:
        data = json.loads(js_unescape(m.group(1)))
    except ValueError:
        return {}
    if isinstance(data, list):
        data = data[0] if data else {}
    return data if isinstance(data, dict) else {}


def _place(j: dict) -> str:
    parts = [j.get(k) for k in ("City", "State", "Country")]
    return ", ".join(p.strip() for p in parts if isinstance(p, str) and p.strip())


def _date(value) -> datetime | None:
    try:
        return datetime.strptime(str(value or "")[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


class ZohoRecruitAdapter(Adapter):
    ats = "zoho"
    min_interval_minutes = 30
    max_concurrency = 3
    lane = "slow"

    async def fetch(
        self, client: httpx.AsyncClient, slug: str, *, known_ids: Set[str] = frozenset(), **_
    ) -> list[NormalizedJob]:
        host, portal = parse_slug(slug)
        base = f"https://{host}/jobs/{portal}"
        resp = await http_util.get(client, base)
        listed = parse_listing(resp.text)
        if not listed and ('id="jobs"' not in resp.text):
            # "Page does not exist" (wrong subdomain) or a moved portal: report it as a dead board.
            raise httpx.HTTPStatusError(f"no Zoho Recruit portal at {base}", request=resp.request,
                                        response=httpx.Response(404, request=resp.request))
        sem = asyncio.Semaphore(DETAIL_CONCURRENCY)

        async def build(j: dict) -> NormalizedJob | None:
            job_id = str(j["id"])
            title = (j.get("Posting_Title") or j.get("Job_Opening_Name") or "").strip()
            if not title:
                return None
            place = _place(j)
            remote = bool(j.get("Remote_Job"))
            if outside_us([place]) and not remote:
                return None
            job = NormalizedJob(
                external_id=job_id,
                title=title,
                url=f"{base}/{job_id}/{url_slug(title)}",
                locations=[place] if place else [],
                remote=remote,
                description=html_to_text(j.get("Job_Description") or ""),
                department=j.get("Industry") or "",
                posted_at=_date(j.get("Date_Opened")),
            )
            if job_id in known_ids:
                return job
            async with sem:
                try:
                    detail = parse_detail((await http_util.get(client, job.url)).text)
                except httpx.HTTPError:
                    return None  # still new next poll (saving the ~150-character snippet would count as described)
            if detail.get("Job_Description"):
                job.description = html_to_text(detail["Job_Description"])
            job.locations = [_place(detail)] if _place(detail) else job.locations
            return None if outside_us(job.locations) and not remote else job

        jobs = await asyncio.gather(*(build(j) for j in listed))
        return [j for j in jobs if j is not None]
