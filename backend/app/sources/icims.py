"""iCIMS career portals (careers-{slug}.icims.com).

slug: the portal name in careers-{slug}.icims.com, e.g. "peraton". A full host also works
("uscareers-acme.icims.com") for portals with another prefix.

iCIMS has no public JSON API. Verified live (Nov 2026):
- /jobs/search?pr={page, 0-based}&in_iframe=1 returns the bare listing HTML (no wrapper iframe):
  job links look like https://careers-x.icims.com/jobs/{id}/{title-slug}/job?in_iframe=1, with the
  title in the link's `title="{id} - {title}"`, plus a "Page 1 of N" paginator. Layouts are
  per-portal (job cards or a table), so we only rely on the links, the paginator and nearby labels.
- Sorting by posted date isn't available on every portal, so every poll is a full listing scan
  (cheap: ~20-50 jobs a page), and only new jobs get a detail request.
- /jobs/{id}/job?in_iframe=1 embeds a schema.org JobPosting as JSON-LD (description, datePosted,
  jobLocation), which is where descriptions, dates and locations come from.
- A wrong slug answers 404; a portal that moved away from iCIMS answers 200 with a JS redirect and
  no jobs.
"""

import asyncio
import html as htmllib
import re
from collections.abc import Set

import httpx

from app.pipeline.normalize import html_to_text
from app.sources import http_util
from app.sources.base import Adapter, NormalizedJob, parse_iso

MAX_PAGES = 100
DETAIL_CONCURRENCY = 4
_LINK = re.compile(r'<a\b[^>]*href="(https?://[^"/]+)?/jobs/(\d+)/([^"/?]*)/job[^"]*"[^>]*>(.*?)</a>', re.S | re.I)
_TITLE_ATTR = re.compile(r'title="\d+\s*-\s*([^"]*)"')
_PAGES = re.compile(r"Page\s+\d+\s+of\s+(\d+)", re.I)
_LOCATION = re.compile(r'field-label">\s*(?:Job\s+)?Locations?\s*</span>\s*<span[^>]*>\s*([^<]+?)\s*<', re.I)
_CARD_SPLIT = re.compile(r'(?=<(?:li|div|tr)\b[^>]*class="[^"]*(?:iCIMS_JobCardItem|iCIMS_JobListingRow)[^"]*")', re.I)


def host_for(slug: str) -> str:
    slug = slug.strip().lower()
    return slug if "." in slug else f"careers-{slug}.icims.com"


def parse_listing(html: str) -> tuple[list[dict], int]:
    """([{id, title, location}], page count) from a /jobs/search?in_iframe=1 page."""
    cards = _CARD_SPLIT.split(html)
    if len(cards) <= 1:
        cards = [html]
    jobs: dict[str, dict] = {}
    for card in cards:
        location_match = _LOCATION.search(card)
        for m in _LINK.finditer(card):
            job_id, inner = m.group(2), m.group(4)
            if job_id in jobs:
                continue
            attr = _TITLE_ATTR.search(m.group(0))
            title = htmllib.unescape(attr.group(1)).strip() if attr else html_to_text(inner)
            title = re.sub(r"^External Job Posting Title\s*|^Job Title\s*", "", title).strip()
            if not title:
                continue
            jobs[job_id] = {
                "id": job_id,
                "title": title,
                "location": htmllib.unescape(location_match.group(1)).strip() if location_match else "",
            }
    pages = max((int(n) for n in _PAGES.findall(html)), default=1)
    return list(jobs.values()), pages


class ICIMSAdapter(Adapter):
    ats = "icims"
    min_interval_minutes = 30
    lane = "slow"

    async def fetch(
        self, client: httpx.AsyncClient, slug: str, *, full: bool = True, known_ids: Set[str] = frozenset(), **_
    ) -> list[NormalizedJob]:
        host = host_for(slug)
        base = f"https://{host}"
        listed: dict[str, dict] = {}
        pages = 1
        page = 0
        while page < min(pages, MAX_PAGES):
            resp = await http_util.get(client, f"{base}/jobs/search", params={"pr": page, "in_iframe": 1, "ss": 1})
            jobs, pages = parse_listing(resp.text)
            fresh = [j for j in jobs if j["id"] not in listed]
            if not fresh:
                break
            listed.update((j["id"], j) for j in fresh)
            page += 1

        sem = asyncio.Semaphore(DETAIL_CONCURRENCY)

        async def build(j: dict) -> NormalizedJob | None:
            location = j["location"]
            job = NormalizedJob(
                external_id=j["id"],
                title=j["title"],
                url=f"{base}/jobs/{j['id']}/job",
                locations=[location] if location else [],
                remote="remote" in location.lower(),
            )
            if j["id"] in known_ids:
                return job
            async with sem:
                try:
                    resp = await http_util.get(client, f"{base}/jobs/{j['id']}/job", params={"in_iframe": 1})
                except httpx.HTTPError:
                    return None  # still new next poll
            posting = http_util.json_ld_job(resp.text)
            if not posting:
                return None
            locations = http_util.ld_locations(posting)
            job.title = htmllib.unescape(posting.get("title") or job.title).strip()
            job.description = html_to_text(htmllib.unescape(posting.get("description") or ""))
            job.locations = locations or job.locations
            job.posted_at = parse_iso(posting.get("datePosted"))
            job.department = posting.get("occupationalCategory") or ""
            job.remote = job.remote or posting.get("jobLocationType") == "TELECOMMUTE"
            if posting.get("url"):
                job.url = posting["url"]
            return job

        jobs = await asyncio.gather(*(build(j) for j in listed.values()))
        return [j for j in jobs if j is not None]
