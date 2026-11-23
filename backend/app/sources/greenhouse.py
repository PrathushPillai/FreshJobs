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
from app.sources import http_util
from app.sources.base import Adapter, NormalizedJob, parse_iso

API = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
DETAIL_CONCURRENCY = 4

# Greenhouse rate-limits by volume (2026-11-23, from 16:00 UTC: ~13,000 HTTP 429s an hour and ~3,900 of 6,000
# boards refused, while a single request from the server still got 200). We kept sending 25 at a time through every
# refusal. Now a 429 pauses every Greenhouse request in the lane for PAUSE_AFTER_429 seconds, then requests go out
# one at a time GENTLE_SPACING apart until GENTLE_SUCCESSES in a row succeed (as Workday does, sources/workday.py).
PAUSE_AFTER_429 = 60.0
GENTLE_SPACING = 0.5
GENTLE_SUCCESSES = 40
_PAUSE = http_util.Spacer(0.0)
_gentle = {"left": 0}


async def _get(client: httpx.AsyncClient, url: str, **kwargs) -> httpx.Response:
    """A Greenhouse request that waits out a pause, starts one on a 429, and comes back gently."""
    await _PAUSE.wait()
    resp = await client.get(url, **kwargs)
    if resp.status_code == 429:
        _PAUSE.pause(PAUSE_AFTER_429)
        _PAUSE.seconds = GENTLE_SPACING
        _gentle["left"] = GENTLE_SUCCESSES
    elif _gentle["left"] and resp.status_code < 400:
        _gentle["left"] -= 1
        if not _gentle["left"]:
            _PAUSE.seconds = 0.0  # answering normally again: full speed
    return resp


def _job(j: dict, content: dict | None = None) -> NormalizedJob:
    content = content or j
    # Some boards send "location": {"name": null} (Veracode, Podium, Built: they never loaded until 2026-11-10).
    location = (j.get("location") or {}).get("name") or ""
    departments = content.get("departments") or []
    return NormalizedJob(
        external_id=str(j["id"]),
        title=(j.get("title") or "").strip(),
        url=j.get("absolute_url", ""),
        locations=[location] if location else [],
        remote="remote" in location.lower(),
        # Greenhouse returns the description HTML-escaped, so unescape before stripping tags.
        description=html_to_text(html.unescape(content.get("content") or "")),
        department=(departments[0].get("name") or "") if departments else "",
        posted_at=parse_iso(j.get("first_published") or j.get("updated_at")),
    )


class GreenhouseAdapter(Adapter):
    ats = "greenhouse"
    # 10 at once made a pass over ~2,700 boards take ~30 min (2026-11); 25 drew rate limits once there were ~6,000
    # boards (2026-11-23). A pass over 6,000 at 12 is a couple of minutes.
    max_concurrency = 12

    async def fetch(
        self, client: httpx.AsyncClient, slug: str, *, known_ids: Set[str] = frozenset(), **_
    ) -> list[NormalizedJob]:
        if not known_ids:  # first scan: one request with every description
            resp = await _get(client, API.format(slug=slug), params={"content": "true"})
            resp.raise_for_status()
            return [_job(j) for j in resp.json().get("jobs", [])]

        resp = await _get(client, API.format(slug=slug))
        resp.raise_for_status()
        listed = resp.json().get("jobs", [])
        sem = asyncio.Semaphore(DETAIL_CONCURRENCY)

        async def build(j: dict) -> NormalizedJob | None:
            if str(j["id"]) in known_ids:
                return _job(j, {})
            async with sem:
                try:
                    r = await _get(client, f"{API.format(slug=slug)}/{j['id']}")
                    r.raise_for_status()
                except httpx.HTTPError:
                    return None  # taken down since the list call, or transient: it's still new next poll
            return _job(j, r.json())

        return [job for job in await asyncio.gather(*(build(j) for j in listed)) if job is not None]
