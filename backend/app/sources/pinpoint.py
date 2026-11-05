"""Pinpoint career sites ({slug}.pinpointhq.com). One request returns every posting with its description.

slug: the subdomain, e.g. "wolve".
"""

import httpx

from app.pipeline.normalize import html_to_text
from app.sources.base import Adapter, NormalizedJob

SECTIONS = ("description", "key_responsibilities", "skills_knowledge_expertise", "benefits")


def _place(loc: dict) -> str:
    return loc.get("name") or ", ".join(p for p in (loc.get("city"), loc.get("province")) if p)


class PinpointAdapter(Adapter):
    ats = "pinpoint"

    async def fetch(self, client: httpx.AsyncClient, slug: str, **_) -> list[NormalizedJob]:
        resp = await client.get(f"https://{slug}.pinpointhq.com/postings.json")
        resp.raise_for_status()
        jobs = []
        for j in resp.json().get("data") or []:
            if not isinstance(j, dict) or not j.get("id"):
                continue
            place = _place(j.get("location") or {})
            department = ((j.get("job") or {}).get("department") or {}).get("name", "") if isinstance(j.get("job"), dict) else ""
            jobs.append(
                NormalizedJob(
                    external_id=str(j["id"]),
                    title=(j.get("title") or "").strip(),
                    url=j.get("url") or f"https://{slug}.pinpointhq.com{j.get('path', '')}",
                    locations=[place] if place else [],
                    remote=(j.get("workplace_type") or "").lower() == "remote",
                    description="\n\n".join(html_to_text(j[s]) for s in SECTIONS if isinstance(j.get(s), str)),
                    department=department,
                )
            )
        return jobs
