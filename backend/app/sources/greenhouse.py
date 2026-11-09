"""Greenhouse job boards (boards.greenhouse.io/{slug}).

The list with `content=true` includes every description, which is ~12x the size of the plain list
(5.8 MB vs 0.5 MB for Stripe). So a board's first scan makes that one big request, and later polls
fetch the plain list plus one detail request per job not seen before.
"""

import asyncio
import html
from collections.abc import Set

import httpx

from app.pipeline.normalize import html_to_text
from app.sources.base import Adapter, NormalizedJob, parse_iso

API = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
DETAIL_CONCURRENCY = 4


def _job(j: dict, content: dict | None = None) -> NormalizedJob:
    content = content or j
    location = (j.get("location") or {}).get("name", "")
    departments = content.get("departments") or []
    return NormalizedJob(
        external_id=str(j["id"]),
        title=(j.get("title") or "").strip(),
        url=j.get("absolute_url", ""),
        locations=[location] if location else [],
        remote="remote" in location.lower(),
        # Greenhouse returns the description HTML-escaped, so unescape before stripping tags.
        description=html_to_text(html.unescape(content.get("content") or "")),
        department=departments[0]["name"] if departments else "",
        posted_at=parse_iso(j.get("first_published") or j.get("updated_at")),
    )


class GreenhouseAdapter(Adapter):
    ats = "greenhouse"
    max_concurrency = 25  # a public CDN-backed API; 10 at once made a pass over ~2,700 boards take ~30 min

    async def fetch(
        self, client: httpx.AsyncClient, slug: str, *, known_ids: Set[str] = frozenset(), **_
    ) -> list[NormalizedJob]:
        if not known_ids:  # first scan: one request with every description
            resp = await client.get(API.format(slug=slug), params={"content": "true"})
            resp.raise_for_status()
            return [_job(j) for j in resp.json().get("jobs", [])]

        resp = await client.get(API.format(slug=slug))
        resp.raise_for_status()
        listed = resp.json().get("jobs", [])
        sem = asyncio.Semaphore(DETAIL_CONCURRENCY)

        async def build(j: dict) -> NormalizedJob | None:
            if str(j["id"]) in known_ids:
                return _job(j, {})
            async with sem:
                try:
                    r = await client.get(f"{API.format(slug=slug)}/{j['id']}")
                    r.raise_for_status()
                except httpx.HTTPError:
                    return None  # taken down since the list call, or transient: it's still new next poll
            return _job(j, r.json())

        return [job for job in await asyncio.gather(*(build(j) for j in listed)) if job is not None]
