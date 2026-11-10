"""Paylocity recruiting job boards (recruiting.paylocity.com/recruiting/jobs/All/{guid}/{Company-Name}).

slug: the board GUID, e.g. "07da01d3-e918-4687-9c6b-b7ae0125d86d" (Meyer Distributing).

Verified live (Nov 2026); recruiting.paylocity.com has no robots.txt (404):
- The board page embeds every open job server-side as `window.pageData = {..., "Jobs": [{JobId,
  JobTitle, LocationName ("IN - Jasper"), PublishedDate (ISO with offset), Description (a
  ~100-character teaser), IsInternal, HiringDepartment, IsRemote, JobLocation{City, State, Country}}]}`,
  so one request lists the board. Internal-only postings (IsInternal) are skipped.
- /Recruiting/Jobs/Details/{JobId} carries a schema.org JobPosting as JSON-LD with the full
  description, fetched once per new job.
- /recruiting/v2/api/feed/jobs/{guid} (the embeddable widget's feed) answers {"jobs": []} for boards
  that haven't switched the widget on, so the board page is the reliable source.
"""

import asyncio
import json
import re
from collections import Counter
from collections.abc import Set

import httpx

from app.pipeline.normalize import html_to_text
from app.sources import http_util
from app.sources.base import Adapter, NormalizedJob, parse_iso
from app.sources.hr_common import outside_us

BASE = "https://recruiting.paylocity.com/Recruiting"
DETAIL_CONCURRENCY = 3
_GUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
_URL = re.compile(
    r"https?://recruiting\.paylocity\.com/recruiting/(?:jobs/(?:All|List)|v2/api/feed/jobs|PublicLeads/New)/(" + _GUID + ")",
    re.I,
)
PAGE_MARKER = re.compile(r"recruiting\.paylocity\.com/recruiting/(?:jobs/(?:All|List)|v2/api/feed/jobs)/" + _GUID, re.I)
_PAGE_DATA = re.compile(r"window\.pageData\s*=\s*(\{.*?\});\s*\n", re.S)


def detect(url: str) -> str | None:
    m = _URL.match(url.strip())
    return m.group(1).lower() if m else None


def slug_from_page(url: str, html: str) -> str | None:
    found = Counter(m.group(1).lower() for m in _URL.finditer(html))
    return found.most_common(1)[0][0] if found else None


def parse_board(html: str) -> list[dict]:
    m = _PAGE_DATA.search(html)
    if not m:
        raise ValueError("no Paylocity job board data in page")
    return [j for j in json.loads(m.group(1)).get("Jobs") or [] if isinstance(j, dict) and j.get("JobId")]


def _place(j: dict) -> str:
    loc = j.get("JobLocation") or {}
    parts = [loc.get("City"), loc.get("State"), loc.get("Country")]
    text = ", ".join(p.strip() for p in parts if isinstance(p, str) and p.strip())
    return text or (j.get("LocationName") or "").strip()


class PaylocityAdapter(Adapter):
    ats = "paylocity"
    min_interval_minutes = 15
    max_concurrency = 6

    async def fetch(
        self, client: httpx.AsyncClient, slug: str, *, known_ids: Set[str] = frozenset(), **_
    ) -> list[NormalizedJob]:
        guid = slug.strip().lower()
        if not re.fullmatch(_GUID, guid):
            raise ValueError(f"Paylocity slug must be the board GUID, got {slug!r}")
        resp = await http_util.get(client, f"{BASE}/Jobs/All/{guid}")
        listed = [j for j in parse_board(resp.text) if not j.get("IsInternal")]
        sem = asyncio.Semaphore(DETAIL_CONCURRENCY)

        async def build(j: dict) -> NormalizedJob:
            job_id = str(j["JobId"])
            place = _place(j)
            job = NormalizedJob(
                external_id=job_id,
                title=(j.get("JobTitle") or "").strip(),
                url=f"{BASE}/Jobs/Details/{job_id}",
                locations=[place] if place else [],
                remote=bool(j.get("IsRemote")),
                department=(j.get("HiringDepartment") or "").strip(),
                posted_at=parse_iso(j.get("PublishedDate")),
            )
            if job_id not in known_ids:
                async with sem:
                    try:
                        detail = await http_util.get(client, job.url)
                    except httpx.HTTPError:
                        return job  # listed without a description; enrichment retries later
                posting = http_util.json_ld_job(detail.text)
                job.description = html_to_text(posting.get("description") or "")
                job.locations = http_util.ld_locations(posting) or job.locations
            return job

        kept = [j for j in listed if (j.get("JobTitle") or "").strip() and not outside_us([_place(j)])]
        return list(await asyncio.gather(*(build(j) for j in kept)))
