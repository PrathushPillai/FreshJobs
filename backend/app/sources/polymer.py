"""Polymer job boards (jobs.polymer.co/{slug}): an ATS used by early-stage startups.

slug: the organization in jobs.polymer.co/{slug}, e.g. "axoft".

The list is paged (`meta.next_page`) and has no descriptions, so each job not seen before gets one
detail request.
"""

import asyncio
from collections.abc import Set

import httpx

from app.pipeline.normalize import html_to_text
from app.sources.base import Adapter, NormalizedJob, parse_iso

API = "https://api.polymer.co/v1/hire/organizations/{slug}/jobs"
MAX_PAGES = 20
DETAIL_CONCURRENCY = 4


def _job(j: dict, description: str = "") -> NormalizedJob:
    place = j.get("display_location") or ", ".join(p for p in (j.get("city"), j.get("state_region"), j.get("country")) if p)
    remoteness = (j.get("remoteness_pretty") or "").lower()
    return NormalizedJob(
        external_id=str(j["id"]),
        title=(j.get("title") or "").strip(),
        url=j.get("job_post_url") or "",
        locations=[place] if place else [],
        remote="remote" in remoteness and not remoteness.startswith("no remote"),
        description=description,
        department=j.get("job_category_name") or "",
        posted_at=parse_iso(j.get("published_at") or j.get("created_at")),
    )


class PolymerAdapter(Adapter):
    ats = "polymer"
    max_concurrency = 6

    async def fetch(self, client: httpx.AsyncClient, slug: str, *, known_ids: Set[str] = frozenset(), **_) -> list[NormalizedJob]:
        listed: list[dict] = []
        page = 1
        for _ in range(MAX_PAGES):
            resp = await client.get(API.format(slug=slug), params={"page": page})
            resp.raise_for_status()
            data = resp.json()
            listed += data.get("items") or []
            page = (data.get("meta") or {}).get("next_page")
            if not page:
                break
        sem = asyncio.Semaphore(DETAIL_CONCURRENCY)

        async def build(j: dict) -> NormalizedJob:
            if str(j["id"]) in known_ids:
                return _job(j)
            async with sem:
                try:
                    r = await client.get(f"{API.format(slug=slug)}/{j['id']}")
                    r.raise_for_status()
                    return _job(j, html_to_text(r.json().get("description") or ""))
                except (httpx.HTTPError, ValueError):
                    return _job(j)  # saved without a description; enrichment fetches it later

        return list(await asyncio.gather(*(build(j) for j in listed)))
