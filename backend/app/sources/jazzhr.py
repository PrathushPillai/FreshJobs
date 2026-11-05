"""JazzHR (*.applytojob.com): every JazzHR employer's jobs, from JazzHR's own job sitemaps.

slug: unused (one source covers all of JazzHR).

JazzHR is used by thousands of small US employers that never post on the big boards. Every
*.applytojob.com robots.txt points to sitemaps at app.jazz.co/feeds/google/xml/{0,1,...}, which list
every open job's URL (https://{company}.applytojob.com/apply/{id}/{Title-Words}), ~100k in all. A job's
title and employer come from its URL; its description, location and posted date from the JSON-LD on
its page, fetched once per new job (a capped number per poll; the rest are filled in by enrichment).
The sitemaps' lastmod is just today's date, so it says nothing about when a job was posted.
"""

import asyncio
import re
from collections.abc import Set

import httpx

from app.pipeline.normalize import html_to_text
from app.sources.base import Adapter, NormalizedJob, parse_iso
from app.sources.http_util import json_ld_jobs, ld_locations

SITEMAP = "http://app.jazz.co/feeds/google/xml/{n}"
MAX_SITEMAPS = 20
DETAILS_PER_POLL = 400
DETAIL_CONCURRENCY = 6
_LOC = re.compile(r"<loc>https?://([a-z0-9-]+)\.applytojob\.com/apply/([A-Za-z0-9]+)/([^<?]*)")


def parse_sitemap(xml: str) -> list[tuple[str, str, str]]:
    """(company subdomain, job id, title words) for every job URL in a sitemap."""
    return [(m.group(1), m.group(2), m.group(3)) for m in _LOC.finditer(xml)]


def _from_url(company: str, job_id: str, words: str) -> NormalizedJob:
    return NormalizedJob(
        external_id=f"{company}/{job_id}",
        title=words.replace("-", " ").strip() or "Open role",
        url=f"https://{company}.applytojob.com/apply/{job_id}/{words}",
        company_name=company.replace("-", " ").title(),
    )


def _with_details(job: NormalizedJob, page: str) -> NormalizedJob:
    postings = json_ld_jobs(page)
    if not postings:
        return job
    p = postings[0]
    locations = ld_locations(p)
    job.title = (p.get("title") or job.title).strip()
    job.company_name = ((p.get("hiringOrganization") or {}).get("name") or job.company_name).strip()
    job.locations = locations
    job.remote = "TELECOMMUTE" in str(p.get("jobLocationType") or "").upper() or any("remote" in loc.lower() for loc in locations)
    job.description = html_to_text(str(p.get("description") or ""))
    job.posted_at = parse_iso(str(p.get("datePosted") or ""))
    return job


class JazzHRAdapter(Adapter):
    ats = "jazzhr"
    aggregator = True
    min_interval_minutes = 30
    lane = "bulk"  # its first save is ~110k jobs: kept away from the fast boards

    async def fetch(
        self, client: httpx.AsyncClient, slug: str, *, known_ids: Set[str] = frozenset(), first_scan: bool = False, **_
    ) -> list[NormalizedJob]:
        listed: list[tuple[str, str, str]] = []
        for n in range(MAX_SITEMAPS):
            resp = await client.get(SITEMAP.format(n=n))
            if resp.status_code == 404 and n > 0:
                break  # past the last sitemap
            resp.raise_for_status()  # a missing sitemap mid-way: fail, so nothing is wrongly closed
            page = parse_sitemap(resp.text)
            if not page:
                break
            listed += page
        jobs = [_from_url(*row) for row in dict.fromkeys(listed)]
        # A first scan saves the URLs only (thousands of jobs); after that, new jobs get their page read.
        todo = [] if first_scan else [j for j in jobs if j.external_id not in known_ids][:DETAILS_PER_POLL]
        sem = asyncio.Semaphore(DETAIL_CONCURRENCY)

        async def enrich(job: NormalizedJob) -> None:
            async with sem:
                try:
                    r = await client.get(job.url)
                except httpx.HTTPError:
                    return
            if r.status_code == 200:
                _with_details(job, r.text)

        await asyncio.gather(*(enrich(j) for j in todo))
        return jobs
