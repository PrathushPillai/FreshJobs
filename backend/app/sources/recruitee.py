"""Recruitee career sites ({slug}.recruitee.com). One request returns every offer with its description.

slug: the subdomain, e.g. "hardrockdigital".
"""

import httpx

from app.pipeline.normalize import html_to_text
from app.sources.base import Adapter, NormalizedJob, parse_iso


def _place(loc: dict) -> str:
    return loc.get("name") or ", ".join(p for p in (loc.get("city"), loc.get("state_code"), loc.get("country")) if p)


class RecruiteeAdapter(Adapter):
    ats = "recruitee"

    async def fetch(self, client: httpx.AsyncClient, slug: str, **_) -> list[NormalizedJob]:
        resp = await client.get(f"https://{slug}.recruitee.com/api/offers/")
        resp.raise_for_status()
        jobs = []
        for j in resp.json().get("offers") or []:
            if not isinstance(j, dict) or not j.get("id"):
                continue
            locations = [_place(loc) for loc in j.get("locations") or [] if isinstance(loc, dict)]
            locations = [loc for loc in locations if loc] or ([j["location"]] if j.get("location") else [])
            description = html_to_text(j.get("description") or "")
            if j.get("requirements"):
                description += "\n\n" + html_to_text(j["requirements"])
            published = (j.get("published_at") or j.get("created_at") or "").replace(" UTC", "+00:00")
            jobs.append(
                NormalizedJob(
                    external_id=str(j["id"]),
                    title=(j.get("title") or "").strip(),
                    url=j.get("careers_url") or f"https://{slug}.recruitee.com/o/{j.get('slug')}",
                    locations=locations,
                    remote=bool(j.get("remote")),
                    description=description.strip(),
                    department=j.get("department") or "",
                    posted_at=parse_iso(published),
                )
            )
        return jobs
