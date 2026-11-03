"""Remotive remote-jobs API (aggregator, no key).

slug: optional query string of Remotive's filters (`category`, `search`, `company_name`, `limit`),
e.g. "category=software-dev"; empty, or any string without "=", means all jobs.

Remotive's terms: link back to the job's Remotive URL, credit Remotive as the source, and call the
API at most ~4 times a day. The free feed is delayed 24 hours, and (as of Nov 2026) returns only
about 10 jobs per call whatever the filters.
"""

from urllib.parse import parse_qsl

import httpx

from app.pipeline.normalize import html_to_text
from app.sources.base import Adapter, NormalizedJob, parse_iso


class RemotiveAdapter(Adapter):
    ats = "remotive"
    complete_feed = False
    min_interval_minutes = 360  # their docs: "max. 4 times a day"

    async def fetch(self, client: httpx.AsyncClient, slug: str, **_) -> list[NormalizedJob]:
        resp = await client.get("https://remotive.com/api/remote-jobs", params=parse_qsl(slug or ""))
        resp.raise_for_status()
        jobs = []
        for j in resp.json().get("jobs") or []:
            location = (j.get("candidate_required_location") or "").strip()
            jobs.append(
                NormalizedJob(
                    external_id=str(j["id"]),
                    title=(j.get("title") or "").strip(),
                    url=j.get("url") or "",  # Remotive's own page; their terms require linking to it
                    locations=[location] if location else [],
                    remote=True,
                    description=html_to_text(j.get("description") or ""),
                    department=j.get("category") or "",
                    posted_at=parse_iso(j.get("publication_date")),  # naive, in UTC
                    company_name=(j.get("company_name") or "").strip(),
                )
            )
        return jobs
