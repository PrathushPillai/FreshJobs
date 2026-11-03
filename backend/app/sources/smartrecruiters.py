"""SmartRecruiters public Posting API.

slug: the company identifier in jobs.smartrecruiters.com/{slug} (case-sensitive, e.g. "ServiceNow").
An unknown slug returns HTTP 200 with zero postings, not a 404.

The list is newest-first by releasedDate, 100 per page. Descriptions need one GET per posting
(jobAd.sections), made only for postings not already known.
"""

import asyncio
from collections.abc import Set

import httpx

from app.pipeline.normalize import html_to_text
from app.sources.base import Adapter, NormalizedJob, parse_iso

API = "https://api.smartrecruiters.com/v1/companies"
PAGE_SIZE = 100  # the API's maximum
MAX_PAGES = 100
DETAIL_CONCURRENCY = 4
# Order the description sections; the company blurb goes last.
SECTIONS = ("jobDescription", "qualifications", "additionalInformation", "companyDescription")


def _location(loc: dict) -> str:
    if loc.get("fullLocation"):
        return loc["fullLocation"]
    parts = [loc.get("city"), loc.get("region"), (loc.get("country") or "").upper()]
    return ", ".join(p for p in parts if p)


def _description(job_ad: dict) -> str:
    sections = (job_ad or {}).get("sections") or {}
    keys = [k for k in SECTIONS if k in sections] + [k for k in sections if k not in SECTIONS]
    parts = []
    for key in keys:
        text = html_to_text((sections[key] or {}).get("text") or "")
        if text:
            title = (sections[key] or {}).get("title") or ""
            parts.append(f"{title}\n{text}" if title else text)
    return "\n\n".join(parts)


class SmartRecruitersAdapter(Adapter):
    ats = "smartrecruiters"
    min_interval_minutes = 10
    # Newest-first, so a partial scan reads just the first page (the 100 newest postings).
    full_scan_every = 6
    lane = "slow"

    async def fetch(
        self, client: httpx.AsyncClient, slug: str, *, full: bool = True, known_ids: Set[str] = frozenset()
    ) -> list[NormalizedJob]:
        postings: list[dict] = []
        offset = 0
        for _ in range(MAX_PAGES if full else 1):
            resp = await client.get(f"{API}/{slug}/postings", params={"limit": PAGE_SIZE, "offset": offset})
            resp.raise_for_status()
            data = resp.json()
            page = data.get("content") or []
            postings += page
            offset += PAGE_SIZE
            if not page or offset >= (data.get("totalFound") or 0):
                break

        sem = asyncio.Semaphore(DETAIL_CONCURRENCY)

        async def build(p: dict) -> NormalizedJob | None:
            ext_id = str(p["id"])
            detail: dict = {}
            if ext_id not in known_ids:
                async with sem:
                    try:
                        resp = await client.get(f"{API}/{slug}/postings/{ext_id}")
                        resp.raise_for_status()
                        detail = resp.json()
                    except (httpx.HTTPError, ValueError):
                        return None  # retried next poll, since it's still new
            loc = detail.get("location") or p.get("location") or {}
            location = _location(loc)
            dept = detail.get("department") or p.get("department") or {}
            func = detail.get("function") or p.get("function") or {}
            return NormalizedJob(
                external_id=ext_id,
                title=(p.get("name") or "").strip(),
                url=detail.get("postingUrl") or f"https://jobs.smartrecruiters.com/{slug}/{ext_id}",
                locations=[location] if location else [],
                remote=bool(loc.get("remote")) or "remote" in location.lower(),
                description=_description(detail.get("jobAd") or {}),
                department=dept.get("label") or func.get("label") or "",
                posted_at=parse_iso(p.get("releasedDate")),
            )

        unique = {str(p["id"]): p for p in postings}  # pages can shift while we read them
        jobs = await asyncio.gather(*(build(p) for p in unique.values()))
        return [j for j in jobs if j is not None]
