"""Jobvite career sites (jobs.jobvite.com/{slug}/jobs).

slug: the career-site name, e.g. "saama" (Saama Technologies). Company pages embed it as
`<div class="jv-careersite" data-careersite="saama">` or `Jobvite = { careersite: 'saama' }`.

Verified live (Nov 2026):
- jobs.jobvite.com has no robots.txt (404), and the career site is plain server-rendered HTML.
- /{slug}/jobs lists every opening in `table.jv-job-list` tables under department headings (`<h3>`):
  `<a href="/{slug}/job/{id}">title</a>` plus a location cell ("San Jose, California", "Bangalore, India",
  or "3 Locations"). Big sites page it with ?p=N links, which are followed.
- /{slug}/job/{id} embeds a schema.org JobPosting as JSON-LD (description, datePosted, every jobLocation),
  fetched only for new jobs, and not for jobs whose listed place is clearly outside the US. Some sites
  (Nutanix) have no JSON-LD; then the description comes from `div.jv-job-detail-description`, with no date.
"""

import asyncio
import html as htmllib
import re
from collections.abc import Set

import httpx

from app.pipeline.normalize import html_to_text
from app.sources import http_util
from app.sources.base import Adapter, NormalizedJob, parse_iso
from app.sources.staffing_common import outside_us, page_urls

BASE = "https://jobs.jobvite.com"
MAX_PAGES = 30
DETAIL_CONCURRENCY = 3

_SECTION = re.compile(r"<h3\b[^>]*>(.*?)</h3>", re.S | re.I)
_ROW = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.S | re.I)
_NAME = re.compile(r'<a\b[^>]*href="(?:https?://jobs\.jobvite\.com)?/([^"/]+)/job/([A-Za-z0-9]+)"[^>]*>(.*?)</a>', re.S | re.I)
_LOCATION = re.compile(r'class="[^"]*jv-job-list-location[^"]*"[^>]*>(.*?)</td>', re.S | re.I)
_N_LOCATIONS = re.compile(r"^\d+\s+Locations?$", re.I)
_DETECT = re.compile(r"^https?://jobs\.jobvite\.com/([A-Za-z0-9_-]+)(?:/|$|\?|#)", re.I)
_EMBED = re.compile(r"""data-careersite=["']([A-Za-z0-9_-]+)["']|careersite\s*:\s*["']([A-Za-z0-9_-]+)["']""", re.I)
_DESCRIPTION = re.compile(r'class="jv-job-detail-description"[^>]*>(.*?)<div class="jv-job-detail-bottom-actions', re.S | re.I)
_RESERVED = {"__assets__", "api", "careersite", "static", "cdn"}

PAGE_MARKER = re.compile(r"jobs\.jobvite\.com/(?!__assets__)[A-Za-z0-9_-]+|jv-careersite|Jobvite\s*=\s*\{", re.I)


def detect(url: str) -> str | None:
    m = _DETECT.match((url or "").strip())
    if not m or m.group(1).lower() in _RESERVED:
        return None
    return m.group(1).lower()


def slug_from_page(url: str, html: str) -> str | None:
    m = _EMBED.search(html or "")
    if m:
        return (m.group(1) or m.group(2)).lower()
    for link in page_urls(html):
        slug = detect(link)
        if slug:
            return slug
    return None


def _text(fragment: str) -> str:
    return " ".join(htmllib.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def parse_listing(html: str) -> list[dict]:
    """[{id, title, location, department}] from a /{slug}/jobs page."""
    jobs: dict[str, dict] = {}
    pieces = _SECTION.split(html or "")
    # pieces: [before, heading1, body1, heading2, body2, ...]
    sections = [("", pieces[0])] + [(_text(pieces[i]), pieces[i + 1]) for i in range(1, len(pieces) - 1, 2)]
    for department, body in sections:
        for row in _ROW.findall(body):
            m = _NAME.search(row)
            if not m or m.group(2) in jobs:
                continue
            loc = _LOCATION.search(row)
            location = _text(loc.group(1)) if loc else ""
            jobs[m.group(2)] = {
                "id": m.group(2),
                "title": _text(m.group(3)),
                "location": "" if _N_LOCATIONS.match(location) else location,
                "department": department,
            }
    return list(jobs.values())


class JobviteAdapter(Adapter):
    ats = "jobvite"
    min_interval_minutes = 20
    max_concurrency = 4
    lane = "slow"

    async def fetch(
        self, client: httpx.AsyncClient, slug: str, *, known_ids: Set[str] = frozenset(), **_
    ) -> list[NormalizedJob]:
        slug = slug.strip().lower()
        listed: dict[str, dict] = {}
        page = 0
        while page < MAX_PAGES:
            resp = await http_util.get(client, f"{BASE}/{slug}/jobs", params={"p": page} if page else None)
            fresh = [j for j in parse_listing(resp.text) if j["id"] not in listed]
            listed.update((j["id"], j) for j in fresh)
            page += 1
            if not fresh or f"/{slug}/jobs?p={page}" not in resp.text.replace("&amp;", "&"):
                break

        sem = asyncio.Semaphore(DETAIL_CONCURRENCY)

        async def build(j: dict) -> NormalizedJob | None:
            job = NormalizedJob(
                external_id=j["id"],
                title=j["title"],
                url=f"{BASE}/{slug}/job/{j['id']}",
                locations=[j["location"]] if j["location"] else [],
                remote="remote" in j["location"].lower(),
                department=j["department"],
            )
            if outside_us(job.locations):
                return None
            if j["id"] in known_ids:
                return job
            async with sem:
                try:
                    resp = await http_util.get(client, job.url)
                except httpx.HTTPError:
                    return None  # still new next poll
            posting = http_util.json_ld_job(resp.text)
            if posting:
                job.title = htmllib.unescape(posting.get("title") or job.title).strip()
                job.description = html_to_text(htmllib.unescape(posting.get("description") or ""))
                job.locations = http_util.ld_locations(posting) or job.locations
                job.posted_at = parse_iso(posting.get("datePosted"))
                job.remote = job.remote or posting.get("jobLocationType") == "TELECOMMUTE"
            else:  # some sites carry no JSON-LD: read the description block (dates aren't shown)
                m = _DESCRIPTION.search(resp.text)
                job.description = html_to_text(re.sub(r"<!--.*?-->", " ", m.group(1), flags=re.S)) if m else ""
            return None if outside_us(job.locations) else job

        jobs = await asyncio.gather(*(build(j) for j in listed.values()))
        return [j for j in jobs if j is not None]
