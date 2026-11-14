"""Rippling ATS job boards (ats.rippling.com/{slug}).

slug: the board name in ats.rippling.com/{slug}/jobs, e.g. "flexai".

The list endpoint has title, department, URL and one location per job; the description and the
posting date need one GET per job, made only for jobs not already known.
"""

import asyncio
from collections.abc import Set

import httpx

from app.pipeline.normalize import html_to_text
from app.sources import http_util
from app.sources.base import Adapter, NormalizedJob, parse_iso

API = "https://api.rippling.com/platform/api/ats/v1/board/{slug}/jobs"
DETAIL_CONCURRENCY = 4


class RipplingAdapter(Adapter):
    ats = "rippling"
    # Every board is on api.rippling.com (Cloudflare). On 2026-11-14 all ~1,190 boards got 403 from 13:34 UTC for a few
    # hours at 15 at a time; fewer at once, and 429s wait and retry.
    max_concurrency = 8

    async def fetch(
        self, client: httpx.AsyncClient, slug: str, *, known_ids: Set[str] = frozenset(), **_
    ) -> list[NormalizedJob]:
        resp = await http_util.get(client, API.format(slug=slug))
        listed = [j for j in resp.json() if isinstance(j, dict) and j.get("uuid")]
        sem = asyncio.Semaphore(DETAIL_CONCURRENCY)

        async def detail(uuid: str) -> dict:
            if uuid in known_ids:
                return {}
            async with sem:
                r = await client.get(f"{API.format(slug=slug)}/{uuid}")
            return r.json() if r.status_code == 200 else {}

        details = await asyncio.gather(*(detail(j["uuid"]) for j in listed))
        jobs = []
        for j, d in zip(listed, details):
            sections = d.get("description") or {}
            description = "\n\n".join(html_to_text(v) for v in sections.values() if isinstance(v, str))
            locations = d.get("workLocations") or [(j.get("workLocation") or {}).get("label") or ""]
            locations = [loc for loc in locations if loc]
            jobs.append(
                NormalizedJob(
                    external_id=j["uuid"],
                    title=(j.get("name") or "").strip(),
                    url=j.get("url") or f"https://ats.rippling.com/{slug}/jobs/{j['uuid']}",
                    locations=locations,
                    remote=any("remote" in loc.lower() for loc in locations),
                    description=description,
                    department=(j.get("department") or {}).get("label") or "",
                    posted_at=parse_iso(d.get("createdOn")),
                )
            )
        return jobs
