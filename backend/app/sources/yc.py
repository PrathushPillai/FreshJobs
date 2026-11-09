"""Y Combinator startups' job pages ("Work at a Startup"), www.ycombinator.com/companies/{slug}/jobs.

slug: the company's YC slug, e.g. "circuithub". Hiring YC companies are added as sources automatically
(pipeline/startups.py), so these rarely need adding by hand.

The jobs page embeds its data as JSON (the `data-page` attribute of the React root): title, location,
salary, minimum experience and, usefully for us, the visa policy ("US citizen/visa only", "Will
sponsor"...). The description, posting date and hiring manager come from each job's own page, fetched
once for jobs not already known. robots.txt allows these pages (it only disallows /companies?...).
"""

import asyncio
import html
import json
import re
from collections.abc import Set

import httpx

from app.sources.base import Adapter, NormalizedJob, parse_iso
from app.sources.http_util import json_ld_job

BASE = "https://www.ycombinator.com"
DETAIL_CONCURRENCY = 3
_DATA_PAGE = re.compile(r'data-page="([^"]+)"')


def page_props(page: str) -> dict:
    m = _DATA_PAGE.search(page)
    if not m:
        raise ValueError("no data-page JSON on the YC page")
    return json.loads(html.unescape(m.group(1))).get("props") or {}


def visa_line(visa: str | None) -> str:
    """The posting's visa field as a sentence the no-sponsorship detector understands."""
    v = (visa or "").strip()
    low = v.lower()
    if not v or low in ("unknown", "none"):
        return ""
    if "citizen" in low or "only" in low:
        return f"No visa sponsorship ({v})."
    if "sponsor" in low or "will" in low:
        return f"Visa sponsorship: {v}."
    return f"Visa: {v}."


def _description(listing: dict, job: dict) -> str:
    facts = [
        visa_line(listing.get("visa")),
        f"Experience: {listing['minExperience']}." if listing.get("minExperience") else "",
        f"Salary: {listing['salaryRange']}." if listing.get("salaryRange") else "",
        f"Equity: {listing['equityRange']}." if listing.get("equityRange") else "",
        f"Skills: {', '.join(listing['skills'])}." if listing.get("skills") else "",
    ]
    manager = job.get("hiringManager") or {}
    if isinstance(manager, dict) and manager.get("first_name"):
        facts.append(f"Hiring manager: {manager['first_name']} {manager.get('last_name') or ''}".strip() + ".")
    head = "\n".join(f for f in facts if f)
    return f"{head}\n\n{job.get('description') or ''}".strip()


class YCAdapter(Adapter):
    ats = "yc"
    min_interval_minutes = 120

    async def fetch(
        self, client: httpx.AsyncClient, slug: str, *, known_ids: Set[str] = frozenset(), **_
    ) -> list[NormalizedJob]:
        resp = await client.get(f"{BASE}/companies/{slug}/jobs")
        resp.raise_for_status()
        listed = [j for j in page_props(resp.text).get("jobPostings") or [] if isinstance(j, dict) and j.get("id")]
        sem = asyncio.Semaphore(DETAIL_CONCURRENCY)

        async def detail(j: dict) -> tuple[dict, str | None]:
            if str(j["id"]) in known_ids or not j.get("url"):
                return {}, None
            async with sem:
                r = await client.get(BASE + j["url"])
            if r.status_code != 200:
                return {}, None
            try:
                return page_props(r.text).get("job") or {}, json_ld_job(r.text).get("datePosted")
            except ValueError:
                return {}, None

        details = await asyncio.gather(*(detail(j) for j in listed))
        jobs = []
        for j, (job, date_posted) in zip(listed, details):
            location = (j.get("location") or "").strip()
            jobs.append(
                NormalizedJob(
                    external_id=str(j["id"]),
                    title=(j.get("title") or "").strip(),
                    url=BASE + j["url"],
                    locations=[part.strip() for part in location.split(" / ") if part.strip()],
                    remote="remote" in location.lower(),
                    description=_description(j, job) if job else visa_line(j.get("visa")),
                    department=j.get("prettyRole") or "",
                    posted_at=parse_iso(date_posted),
                )
            )
        return jobs
