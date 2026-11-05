"""Workable, through the public job-widget API.

slug: the account name in apply.workable.com/{slug}, e.g. "huggingface".

`?details=true` returns every published job with its full description (requirements and benefits
included) in one request, so no per-job calls are needed. Workable rate-limits hard (HTTP 429 after
roughly 100 quick requests from one IP), hence the long poll interval.
"""

import httpx

from app.pipeline.normalize import html_to_text
from app.sources import http_util
from app.sources.base import Adapter, NormalizedJob, parse_iso


def _place(loc: dict) -> str:
    parts = [loc.get("city"), loc.get("region") or loc.get("state"), loc.get("country")]
    return ", ".join(p for p in parts if p)


class WorkableAdapter(Adapter):
    ats = "workable"
    # New jobs come in within minutes through Workable's own job search (workable_search.py); polling
    # each board, for closings and full listings, every 3 hours keeps under Workable's rate limit.
    min_interval_minutes = 180
    max_concurrency = 2  # thousands of discovered Workable boards at 10 at once got HTTP 429 for most
    # At 2 at a time, ~4,400 boards take hours: in their own lane, so they never hold up the fast boards.
    lane = "bulk"

    async def fetch(self, client: httpx.AsyncClient, slug: str, **_) -> list[NormalizedJob]:
        # Retries 429s with backoff (Retry-After honoured).
        resp = await http_util.get(client, f"https://apply.workable.com/api/v1/widget/accounts/{slug}", params={"details": "true"})
        jobs = []
        for j in resp.json().get("jobs") or []:
            locs = j.get("locations") or []
            locations = [_place(loc) for loc in locs if not loc.get("hidden")] or [_place(j)]
            locations = list(dict.fromkeys(loc for loc in locations if loc))
            remote = bool(j.get("telecommuting")) or any("remote" in loc.lower() for loc in locations)
            jobs.append(
                NormalizedJob(
                    external_id=j["shortcode"],
                    title=(j.get("title") or "").strip(),
                    url=j.get("url") or j.get("shortlink") or f"https://apply.workable.com/{slug}/j/{j['shortcode']}/",
                    locations=locations,
                    remote=remote,
                    description=html_to_text(j.get("description") or ""),
                    department=j.get("department") or j.get("function") or "",
                    posted_at=parse_iso(j.get("published_on") or j.get("created_at")),
                )
            )
        return jobs
