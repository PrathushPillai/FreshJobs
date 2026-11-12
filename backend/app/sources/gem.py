"""Gem job boards (jobs.gem.com/{slug}): an ATS many VC-backed startups use.

slug: the board name in jobs.gem.com/{slug}, e.g. "11x-ai".

Gem's public job board API is shaped like Greenhouse's and returns every published job with its
description in one request.
"""

from collections.abc import Set

import httpx

from app.pipeline.normalize import html_to_text
from app.sources.base import Adapter, NormalizedJob, parse_iso

API = "https://api.gem.com/job_board/v0/{slug}/job_posts/"


def _job(j: dict, known_ids: Set[str] = frozenset()) -> NormalizedJob:
    location = ((j.get("location") or {}).get("name") or "").strip()
    offices = [o.get("name", "") for o in j.get("offices") or [] if o.get("name")]
    places = [location] if location else offices
    departments = [d.get("name", "") for d in j.get("departments") or [] if d.get("name")]
    return NormalizedJob(
        external_id=str(j["id"]),
        title=(j.get("title") or "").strip(),
        url=j.get("absolute_url") or "",
        locations=places,
        remote=j.get("location_type") == "remote" or any("remote" in p.lower() for p in places),
        description="" if str(j["id"]) in known_ids else (j.get("content_plain") or html_to_text(j.get("content") or "")),
        department=departments[0] if departments else "",
        posted_at=parse_iso(j.get("first_published_at") or j.get("created_at")),
    )


class GemAdapter(Adapter):
    ats = "gem"
    max_concurrency = 10

    async def fetch(self, client: httpx.AsyncClient, slug: str, *, known_ids: Set[str] = frozenset(), **_) -> list[NormalizedJob]:
        resp = await client.get(API.format(slug=slug))
        resp.raise_for_status()
        return [_job(j, known_ids) for j in resp.json() or []]  # known jobs skip their description (see Ashby)
