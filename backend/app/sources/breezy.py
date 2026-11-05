"""Breezy HR career sites ({slug}.breezy.hr).

slug: the subdomain, e.g. "nucleusteq".

/json lists every published job (title, locations, date) but no description, which is read from
the job's page, once, for jobs not already known.
"""

import asyncio
from collections.abc import Set

import httpx

from app.pipeline.normalize import html_to_text
from app.sources.base import Adapter, NormalizedJob, parse_iso

DETAIL_CONCURRENCY = 4
_DESC_END = ('class="apply-button', 'class="container footer"')


def page_description(html: str) -> str:
    start = html.find('class="description"')
    if start < 0:
        return ""
    start = html.find(">", start) + 1
    ends = [i for i in (html.find(marker, start) for marker in _DESC_END) if i > 0]
    return html_to_text(html[start : min(ends) if ends else len(html)])


def _name(value) -> str:
    return (value.get("name") or "") if isinstance(value, dict) else (value or "")


def _place(loc: dict) -> str:
    return ", ".join(p for p in (loc.get("city"), _name(loc.get("state")), _name(loc.get("country"))) if p) or loc.get("name") or ""


class BreezyAdapter(Adapter):
    ats = "breezy"
    min_interval_minutes = 10

    async def fetch(
        self, client: httpx.AsyncClient, slug: str, *, known_ids: Set[str] = frozenset(), **_
    ) -> list[NormalizedJob]:
        resp = await client.get(f"https://{slug}.breezy.hr/json")
        resp.raise_for_status()
        listed = [j for j in resp.json() if isinstance(j, dict) and j.get("id")]
        sem = asyncio.Semaphore(DETAIL_CONCURRENCY)

        async def description(j: dict) -> str:
            if j["id"] in known_ids or not j.get("url"):
                return ""
            async with sem:
                r = await client.get(j["url"])
            return page_description(r.text) if r.status_code == 200 else ""

        descriptions = await asyncio.gather(*(description(j) for j in listed))
        jobs = []
        for j, desc in zip(listed, descriptions):
            locs = [loc for loc in j.get("locations") or [j.get("location")] if isinstance(loc, dict)]
            locations = list(dict.fromkeys(p for p in map(_place, locs) if p))
            jobs.append(
                NormalizedJob(
                    external_id=j["id"],
                    title=(j.get("name") or "").strip(),
                    url=j.get("url") or "",
                    locations=locations,
                    remote=any(loc.get("is_remote") for loc in locs),
                    description=desc,
                    department=_name(j.get("department")),
                    posted_at=parse_iso(j.get("published_date")),
                )
            )
        return jobs
