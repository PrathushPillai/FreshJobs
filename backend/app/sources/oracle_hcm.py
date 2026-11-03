"""Oracle Recruiting Cloud (Oracle Fusion HCM "Candidate Experience") career sites.

slug: "{host}|{siteNumber}" or "{host}|{siteNumber}|{finder filters}", taken from the career-site URL
https://{host}/hcmUI/CandidateExperience/en/sites/{siteNumber}/..., e.g. "jpmc.fa.oraclecloud.com|CX_1001".
The optional third part adds finder filters, e.g. "locationId=300000000289738" (JPMorgan's "United
States" location facet id; the ids come from the `locationsFacet` of an unfiltered search).

Public REST API (verified live, Nov 2026, no auth):
- List: GET /hcmRestApi/resources/latest/recruitingCEJobRequisitions?onlyData=true
  &expand=requisitionList.secondaryLocations&finder=findReqs;siteNumber=..,limit=200,offset=N,
  sortBy=POSTING_DATES_DESC -> {"items": [{"TotalJobsCount", "requisitionList": [...]}]}.
  `limit` 200 works; results are newest-first by PostedDate (a date, no time). `facetsList=LOCATIONS`
  keeps the (large) facet payload small.
- Detail: GET recruitingCEJobRequisitionDetails?expand=all&onlyData=true
  &finder=ById;Id="{id}",siteNumber=.. -> {"items": [{ExternalDescriptionStr,
  ExternalResponsibilitiesStr, ExternalQualificationsStr, ExternalPostedStartDate, ...}]}, one per new job.
- Public job URL: https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{id}
"""

import asyncio
from collections.abc import Set
from datetime import datetime

import httpx

from app.pipeline.normalize import html_to_text
from app.sources import http_util
from app.sources.base import Adapter, NormalizedJob, parse_iso

API = "hcmRestApi/resources/latest"
PAGE_SIZE = 200
MAX_PAGES = 100
MAX_PARTIAL_PAGES = 3
DETAIL_CONCURRENCY = 4


def parse_slug(slug: str) -> tuple[str, str, str]:
    parts = [p.strip() for p in slug.split("|")]
    if len(parts) not in (2, 3) or not parts[0] or not parts[1]:
        raise ValueError(f"Oracle HCM slug must look like 'host|siteNumber[|filters]', got {slug!r}")
    host = parts[0].removeprefix("https://").removeprefix("http://").rstrip("/")
    return host, parts[1], parts[2] if len(parts) == 3 else ""


def _date(value: str | None) -> datetime | None:
    """'2026-11-03' or an ISO timestamp -> tz-aware UTC."""
    return parse_iso(value)


def _description(d: dict) -> str:
    parts = []
    for heading, key in (("", "ExternalDescriptionStr"), ("Responsibilities", "ExternalResponsibilitiesStr"),
                         ("Qualifications", "ExternalQualificationsStr")):
        text = html_to_text(d.get(key) or "")
        if text:
            parts.append(f"{heading}\n{text}" if heading else text)
    return "\n\n".join(parts)


class OracleHCMAdapter(Adapter):
    ats = "oracle_hcm"
    # A 7,000-job tenant is ~36 list requests of 200; descriptions are one request per new job.
    min_interval_minutes = 15
    full_scan_every = 6
    lane = "slow"

    async def _page(self, client, host: str, site: str, filters: str, offset: int) -> tuple[list[dict], int]:
        finder = f"findReqs;siteNumber={site},facetsList=LOCATIONS,limit={PAGE_SIZE},offset={offset},sortBy=POSTING_DATES_DESC"
        if filters:
            finder += "," + filters.replace("&", ",")
        resp = await http_util.get(client, f"https://{host}/{API}/recruitingCEJobRequisitions", params={
            "onlyData": "true", "expand": "requisitionList.secondaryLocations", "finder": finder,
        })
        items = resp.json().get("items") or [{}]
        return items[0].get("requisitionList") or [], items[0].get("TotalJobsCount") or 0

    async def fetch(
        self, client: httpx.AsyncClient, slug: str, *, full: bool = True, known_ids: Set[str] = frozenset()
    ) -> list[NormalizedJob]:
        host, site, filters = parse_slug(slug)
        reqs: dict[str, dict] = {}
        offset = 0
        for _ in range(MAX_PAGES if full else MAX_PARTIAL_PAGES):
            page, total = await self._page(client, host, site, filters, offset)
            fresh = [r for r in page if r.get("Id") and str(r["Id"]) not in reqs]
            if not fresh:
                break
            reqs.update((str(r["Id"]), r) for r in fresh)
            offset += PAGE_SIZE
            if offset >= total:
                break
            if not full and all(str(r["Id"]) in known_ids for r in fresh):
                break

        sem = asyncio.Semaphore(DETAIL_CONCURRENCY)
        base = f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job"

        async def build(rid: str, r: dict) -> NormalizedJob | None:
            locations = [r.get("PrimaryLocation") or ""] + [
                loc.get("Name") or "" for loc in r.get("secondaryLocations") or []
            ]
            locations = list(dict.fromkeys(loc for loc in locations if loc))
            workplace = (r.get("WorkplaceType") or r.get("WorkplaceTypeCode") or "").lower()
            job = NormalizedJob(
                external_id=rid,
                title=(r.get("Title") or "").strip(),
                url=f"{base}/{rid}",
                locations=locations,
                remote="remote" in workplace or any("remote" in loc.lower() for loc in locations),
                department=r.get("JobFamily") or r.get("JobFunction") or "",
                posted_at=_date(r.get("PostedDate")),
            )
            if rid in known_ids:
                return job
            async with sem:
                try:
                    resp = await http_util.get(client, f"https://{host}/{API}/recruitingCEJobRequisitionDetails", params={
                        "expand": "all", "onlyData": "true", "finder": f'ById;Id="{rid}",siteNumber={site}',
                    })
                    items = resp.json().get("items") or []
                except (httpx.HTTPError, ValueError):
                    return None  # still new next poll
            if not items:
                return None  # no longer posted
            d = items[0]
            job.description = _description(d)
            job.posted_at = _date(d.get("ExternalPostedStartDate")) or job.posted_at
            job.department = d.get("Category") or job.department
            workplace = (d.get("WorkplaceType") or "").lower()
            job.remote = job.remote or "remote" in workplace
            return job

        jobs = await asyncio.gather(*(build(rid, r) for rid, r in reqs.items()))
        return [j for j in jobs if j is not None]
