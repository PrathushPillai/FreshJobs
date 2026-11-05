"""Google for Jobs, via the JSearch API on RapidAPI.

Google indexes postings from LinkedIn, Indeed, Glassdoor, ZipRecruiter and company career sites, so this is
how LinkedIn-only jobs get into the feed without scraping LinkedIn. Each source row is one saved search:

    - name: "Google Jobs: data analyst"
      ats: jsearch
      slug: "query=data analyst jobs in USA&date_posted=today"

Any JSearch /search parameter can go in the slug (query, date_posted, employment_types, job_requirements,
remote_jobs_only, country, num_pages...). Needs JSEARCH_API_KEY (free tier: 200 requests/month).
"""

from urllib.parse import parse_qsl

import httpx

from app.config import settings
from app.sources.base import Adapter, NormalizedJob, from_epoch_ms, parse_iso

API = "https://jsearch.p.rapidapi.com/search-v2"  # the old "/search" endpoint was retired


def _apply_url(j: dict) -> str:
    # Prefer applying on the employer's own site over LinkedIn/Indeed when Google knows it.
    for option in j.get("apply_options") or []:
        if option.get("is_direct") and option.get("apply_link"):
            return option["apply_link"]
    return j.get("job_apply_link") or j.get("job_google_link") or ""


def _description(j: dict) -> str:
    text = j.get("job_description") or ""
    highlights = j.get("job_highlights") or {}
    for section in ("Qualifications", "Responsibilities"):
        items = highlights.get(section) or []
        if items and not all(item in text for item in items):
            text += f"\n\n{section}:\n" + "\n".join(f"- {item}" for item in items)
    return text.strip()


class JSearchAdapter(Adapter):
    ats = "jsearch"
    complete_feed = False  # a search, not a full listing
    aggregator = True

    @property
    def min_interval_minutes(self) -> int:  # type: ignore[override]
        return settings.jsearch_min_interval_minutes

    def is_configured(self) -> bool:
        return bool(settings.jsearch_api_key)

    async def fetch(self, client: httpx.AsyncClient, slug: str, **_) -> list[NormalizedJob]:
        params = {"date_posted": "today", "num_pages": "1", "country": "us", **dict(parse_qsl(slug))}
        resp = await client.get(
            API,
            params=params,
            headers={"X-RapidAPI-Key": settings.jsearch_api_key, "X-RapidAPI-Host": "jsearch.p.rapidapi.com"},
        )
        resp.raise_for_status()
        body = resp.json()
        data = body.get("data") if isinstance(body, dict) else None
        if isinstance(data, dict):  # some responses nest the list
            data = data.get("jobs") or data.get("data")
        if not isinstance(data, list):
            raise ValueError(f"unexpected JSearch response: {str(body)[:200]}")
        jobs = []
        for j in data:
            if not isinstance(j, dict) or not j.get("job_id"):
                continue
            place = ", ".join(x for x in (j.get("job_city"), j.get("job_state"), j.get("job_country")) if x)
            publisher = j.get("job_publisher") or ""
            jobs.append(
                NormalizedJob(
                    external_id=j["job_id"],
                    title=(j.get("job_title") or "").strip(),
                    url=_apply_url(j),
                    locations=[place or j.get("job_location") or ""],
                    remote=bool(j.get("job_is_remote")),
                    description=_description(j),
                    department=f"via {publisher}" if publisher else "",
                    posted_at=parse_iso(j.get("job_posted_at_datetime_utc")) or from_epoch_ms(
                        j["job_posted_at_timestamp"] * 1000 if j.get("job_posted_at_timestamp") else None
                    ),
                    company_name=(j.get("employer_name") or "").strip(),
                )
            )
        return jobs
