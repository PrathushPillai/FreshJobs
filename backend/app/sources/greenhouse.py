import html

import httpx

from app.pipeline.normalize import html_to_text
from app.sources.base import Adapter, NormalizedJob, parse_iso


class GreenhouseAdapter(Adapter):
    ats = "greenhouse"

    async def fetch(self, client: httpx.AsyncClient, slug: str, **_) -> list[NormalizedJob]:
        resp = await client.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", params={"content": "true"})
        resp.raise_for_status()
        jobs = []
        for j in resp.json().get("jobs", []):
            location = (j.get("location") or {}).get("name", "")
            departments = j.get("departments") or []
            # Greenhouse returns the description HTML-escaped, so unescape before stripping tags.
            description = html_to_text(html.unescape(j.get("content") or ""))
            jobs.append(
                NormalizedJob(
                    external_id=str(j["id"]),
                    title=j.get("title", "").strip(),
                    url=j.get("absolute_url", ""),
                    locations=[location] if location else [],
                    remote="remote" in location.lower(),
                    description=description,
                    department=departments[0]["name"] if departments else "",
                    posted_at=parse_iso(j.get("first_published") or j.get("updated_at")),
                )
            )
        return jobs
