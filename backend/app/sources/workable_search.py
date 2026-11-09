"""Workable's public job search (jobs.workable.com): every employer on Workable in one newest-first feed.

slug: the search's filter parameters as a query string, e.g. "location=United States".

Polling thousands of Workable boards one by one gets HTTP 429 from Workable, and a board polled every
half hour or so finds a job late. This feed lists ~40 new US jobs an hour across all of them, so a few
pages every poll catch each one within minutes. The per-board adapter (workable.py) still runs, less
often, for complete listings (descriptions in full, closing jobs that are taken down).

Order: a block of "featured" (promoted) jobs, of any age, comes first, then every job newest first.
Pages hold 20 jobs (the API's maximum) and are chained with `pageToken`. robots.txt allows /api.
"""

from collections.abc import Set
from urllib.parse import parse_qsl

import httpx

from app.pipeline.normalize import html_to_text
from app.sources import http_util
from app.sources.base import Adapter, NormalizedJob, parse_iso

API = "https://jobs.workable.com/api/v1/jobs"
FIRST_SCAN_PAGES = 100  # 2,000 newest jobs
MAX_PAGES = 40  # later polls stop at the first page of jobs already seen; this caps a catch-up


def _place(loc: dict) -> str:
    return ", ".join(p for p in (loc.get("city"), loc.get("subregion"), loc.get("countryName")) if p)


def _job(j: dict) -> NormalizedJob:
    places = [p for p in j.get("locations") or [] if p and p != "TELECOMMUTE"] or [_place(j.get("location") or {})]
    remote = j.get("workplace") == "remote" or "TELECOMMUTE" in (j.get("locations") or [])
    sections = [j.get("description"), j.get("requirementsSection"), j.get("benefitsSection")]
    return NormalizedJob(
        external_id=str(j["id"]),
        title=(j.get("title") or "").strip(),
        url=j.get("url") or "",
        locations=[p for p in places if p],
        remote=remote,
        description=html_to_text("\n".join(s for s in sections if s)),
        department=j.get("department") or "",
        posted_at=parse_iso(j.get("created")),
        company_name=((j.get("company") or {}).get("title") or "").strip(),
    )


class WorkableSearchAdapter(Adapter):
    ats = "workable_search"
    complete_feed = False
    aggregator = True
    min_interval_minutes = 10

    async def fetch(
        self, client: httpx.AsyncClient, slug: str, *, known_ids: Set[str] = frozenset(), first_scan: bool = False, **_
    ) -> list[NormalizedJob]:
        filters = parse_qsl(slug or "")
        jobs: list[NormalizedJob] = []
        token = None
        for _page in range(FIRST_SCAN_PAGES if first_scan else MAX_PAGES):
            params = [*filters, *([("pageToken", token)] if token else [])]
            data = (await http_util.get(client, API, params=params)).json()
            page = data.get("jobs") or []
            jobs.extend(_job(j) for j in page)
            token = data.get("nextPageToken")
            regular = [j for j in page if not j.get("isFeatured")]
            # Past the featured block, a page of jobs we already have means everything after it is older.
            if not token or (not first_scan and regular and all(str(j["id"]) in known_ids for j in regular)):
                break
        return jobs
