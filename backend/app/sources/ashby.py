from collections.abc import Set

import httpx

from app.pipeline.normalize import html_to_text
from app.sources.base import Adapter, NormalizedJob, parse_iso


class AshbyAdapter(Adapter):
    ats = "ashby"
    max_concurrency = 25  # public posting API, one request per board

    async def fetch(self, client: httpx.AsyncClient, slug: str, *, known_ids: Set[str] = frozenset(), **_) -> list[NormalizedJob]:
        # Known jobs skip their description: only new ones (and those missing one, left out of known_ids) use it,
        # and converting every job's HTML every poll was about a fifth of the fast lane's CPU (cloud server, 2026-11-12).
        resp = await client.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
        resp.raise_for_status()
        jobs = []
        for j in resp.json().get("jobs", []):
            if j.get("isListed") is False:
                continue
            locations = [j.get("location") or ""]
            locations += [s.get("location", "") for s in j.get("secondaryLocations") or []]
            locations = [loc for loc in locations if loc]
            workplace = (j.get("workplaceType") or "").lower()
            jobs.append(
                NormalizedJob(
                    external_id=j["id"],
                    title=j.get("title", "").strip(),
                    url=j.get("jobUrl") or j.get("applyUrl") or "",
                    locations=locations,
                    remote=bool(j.get("isRemote")) or workplace == "remote",
                    description="" if j["id"] in known_ids else (
                        j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml") or "")),
                    department=j.get("department") or j.get("team") or "",
                    posted_at=parse_iso(j.get("publishedAt")),
                )
            )
        return jobs
