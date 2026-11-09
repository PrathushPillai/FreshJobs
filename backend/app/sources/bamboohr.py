"""BambooHR career sites ({slug}.bamboohr.com/careers).

slug: the subdomain, e.g. "asuresoftware".

The list has title, department and location; the description and posting date need one GET per job
(/careers/{id}/detail), made only for jobs not already known.
"""

import asyncio
from collections.abc import Set
from datetime import datetime, timezone

import httpx

from app.pipeline.normalize import html_to_text
from app.sources.base import Adapter, NormalizedJob

DETAIL_CONCURRENCY = 4
JSON = {"Accept": "application/json"}


def _place(j: dict) -> str:
    loc = j.get("atsLocation") or {}
    if not any(loc.get(k) for k in ("city", "state", "country")):
        loc = j.get("location") or {}
    parts = [loc.get("city"), loc.get("state") or loc.get("province"), loc.get("country") or loc.get("addressCountry")]
    return ", ".join(p for p in parts if p)


def _date(value: str | None) -> datetime | None:
    try:
        return datetime.strptime(value or "", "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


class BambooHRAdapter(Adapter):
    ats = "bamboohr"
    min_interval_minutes = 10

    async def fetch(
        self, client: httpx.AsyncClient, slug: str, *, known_ids: Set[str] = frozenset(), **_
    ) -> list[NormalizedJob]:
        base = f"https://{slug}.bamboohr.com/careers"
        resp = await client.get(f"{base}/list", headers=JSON)
        resp.raise_for_status()
        listed = [j for j in resp.json().get("result") or [] if isinstance(j, dict) and j.get("id")]
        sem = asyncio.Semaphore(DETAIL_CONCURRENCY)

        async def detail(job_id: str) -> dict:
            if job_id in known_ids:
                return {}
            async with sem:
                r = await client.get(f"{base}/{job_id}/detail", headers=JSON)
            if r.status_code != 200:
                return {}
            return (r.json().get("result") or {}).get("jobOpening") or {}

        details = await asyncio.gather(*(detail(str(j["id"])) for j in listed))
        jobs = []
        for j, d in zip(listed, details):
            place = _place(j)
            jobs.append(
                NormalizedJob(
                    external_id=str(j["id"]),
                    title=(j.get("jobOpeningName") or "").strip(),
                    url=f"{base}/{j['id']}",
                    locations=[place] if place else [],
                    remote=bool(j.get("isRemote")),
                    description=html_to_text(d.get("description") or ""),
                    department=j.get("departmentLabel") or "",
                    posted_at=_date(d.get("datePosted")),
                )
            )
        return jobs
