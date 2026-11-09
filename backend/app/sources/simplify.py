"""SimplifyJobs' community-maintained GitHub job lists (New-Grad-Positions, Summer2026-Internships...).

slug: "owner/repo" or "owner/repo/branch", e.g. "SimplifyJobs/New-Grad-Positions" (branch defaults to dev).

Each repo publishes every listing it has ever had as one JSON file; we keep the active, visible ones.
It's a complete feed: a listing marked inactive (or removed) closes here too. Most links point at the
employer's own ATS, which is how the poller finds new company boards to track (see pipeline/harvest).
"""

from datetime import datetime, timezone

import httpx

from app.sources.base import Adapter, NormalizedJob

RAW = "https://raw.githubusercontent.com/{owner}/{repo}/{branch}/.github/scripts/listings.json"


def _url(slug: str) -> str:
    parts = slug.strip("/").split("/")
    if len(parts) not in (2, 3):
        raise ValueError(f"Simplify slug must look like 'owner/repo[/branch]', got {slug!r}")
    return RAW.format(owner=parts[0], repo=parts[1], branch=parts[2] if len(parts) == 3 else "dev")


def _description(j: dict) -> str:
    lines = []
    if j.get("category"):
        lines.append(f"Category: {j['category']}")
    if j.get("terms"):
        lines.append("Terms: " + ", ".join(j["terms"]))
    if j.get("degrees"):
        lines.append("Degrees: " + ", ".join(j["degrees"]))
    # Simplify's labels ("Does Not Offer Sponsorship", "U.S. Citizenship is Required") are phrased so the
    # no-sponsorship detector picks them up.
    if j.get("sponsorship") and j["sponsorship"] != "Other":
        lines.append(f"Sponsorship: {j['sponsorship']}")
    return "\n".join(lines)


class SimplifyAdapter(Adapter):
    ats = "simplify"
    aggregator = True
    min_interval_minutes = 30

    async def fetch(self, client: httpx.AsyncClient, slug: str, **_) -> list[NormalizedJob]:
        resp = await client.get(_url(slug))
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, list):
            raise ValueError("unexpected Simplify listings file")
        jobs = []
        for j in data:
            if not isinstance(j, dict) or not j.get("id") or not j.get("active") or not j.get("is_visible", True):
                continue
            locations = [loc for loc in j.get("locations") or [] if isinstance(loc, str)]
            posted = j.get("date_posted")
            jobs.append(
                NormalizedJob(
                    external_id=j["id"],
                    title=(j.get("title") or "").strip(),
                    url=j.get("url") or "",
                    locations=locations,
                    remote=any("remote" in loc.lower() for loc in locations),
                    description=_description(j),
                    department=j.get("category") or "",
                    posted_at=datetime.fromtimestamp(posted, tz=timezone.utc) if isinstance(posted, (int, float)) else None,
                    company_name=(j.get("company_name") or "").strip(),
                )
            )
        return jobs
