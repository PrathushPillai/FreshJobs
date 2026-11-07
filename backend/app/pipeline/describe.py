"""Fetch a job's description on demand, when someone opens a job that was saved without one.

Background enrichment (poller, `enrich_per_poll`) fills in descriptions slowly: about 340k older Workday jobs
were listed without one on purpose (`BASELINE_DETAIL_DAYS`), and lists like SimplifyJobs carry only a stub.
Opening such a job asks its source directly:
- Workday: the CXS job API (`liveness._workday_api`), field jobPostingInfo.jobDescription
- Lever: api.lever.co/v0/postings/{company}/{id}, built with every section (`lever_description`)
- Greenhouse: boards-api.greenhouse.io/v1/boards/{board}/jobs/{id}
- anything else: the page's schema.org JobPosting (JSON-LD), which most career sites and ATSs embed
"""

import html
import re
from urllib.parse import urlparse

import httpx

from app.pipeline.liveness import _workday_api
from app.pipeline.normalize import html_to_text
from app.sources.http_util import json_ld_jobs
from app.sources.lever import lever_description

_LEVER = re.compile(r"^/([^/]+)/([0-9a-f-]{36})")
_GREENHOUSE = re.compile(r"^/([^/]+)/jobs/(\d+)")
# LinkedIn, Indeed and Glassdoor are never fetched (their terms).
_SKIP = ("linkedin.com", "indeed.com", "glassdoor.com")


async def fetch_description(client: httpx.AsyncClient, url: str) -> str | None:
    """The job's description as plain text, or None if the source gave none."""
    p = urlparse(url or "")
    host = (p.hostname or "").lower()
    if not host or any(host == h or host.endswith("." + h) for h in _SKIP):
        return None
    try:
        api = _workday_api(url)
        if api:
            r = await client.get(api, headers={"Accept": "application/json"})
            if r.status_code == 200:
                return html_to_text((r.json().get("jobPostingInfo") or {}).get("jobDescription") or "") or None
            return None
        if host == "jobs.lever.co" and (m := _LEVER.match(p.path)):
            r = await client.get(f"https://api.lever.co/v0/postings/{m.group(1)}/{m.group(2)}")
            return (lever_description(r.json()) or None) if r.status_code == 200 else None
        if host.endswith("greenhouse.io") and (m := _GREENHOUSE.match(p.path)):
            r = await client.get(f"https://boards-api.greenhouse.io/v1/boards/{m.group(1)}/jobs/{m.group(2)}")
            if r.status_code == 200:
                return html_to_text(html.unescape(r.json().get("content") or "")) or None  # it's HTML-escaped
        r = await client.get(url)
        if r.status_code != 200:
            return None
        for posting in json_ld_jobs(r.text):
            text = html_to_text(str(posting.get("description") or ""))
            if text.strip():
                return text
    except (httpx.HTTPError, ValueError):
        return None
    return None
