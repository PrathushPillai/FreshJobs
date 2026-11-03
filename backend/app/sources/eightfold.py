"""Eightfold AI career sites ({tenant}.eightfold.ai, or a company's own host such as apply.careers.microsoft.com).

slug: "{tenant}|{domain}" or "{tenant}|{domain}|{location}", e.g. "paypal|paypal.com",
"qualcomm|qualcomm.com|United States". `tenant` may also be a full host name (anything with a dot).

Eightfold serves two incompatible public APIs, and each tenant has exactly one of them (verified live,
Nov 2026): the other answers 403 ("Not authorized for PCSX" / "PCSX is not enabled for this user").
- v2: GET /api/apply/v2/jobs?domain=&start=&num=&sort_by=timestamp -> {"count", "positions": [...]},
  detail GET /api/apply/v2/jobs/{id}?domain= -> {"job_description", ...}. Used by Netflix, Bayer.
- PCSX: GET /api/pcsx/search?domain=&query=&location=&start=&sort_by=timestamp ->
  {"data": {"count", "positions": [...]}}, detail GET /api/pcsx/position_details?position_id=&domain= ->
  {"data": {"jobDescription", "publicUrl", ...}}. Used by Microsoft, PayPal, Qualcomm, Micron, Starbucks.
Both return 10 positions per page whatever `num` asks for, newest-first with sort_by=timestamp (a
"hot" job can be pinned on top). Descriptions need one request per job, made only for new IDs.
"""

import asyncio
from collections.abc import Set

import httpx

from app.pipeline.normalize import html_to_text
from app.sources import http_util
from app.sources.base import Adapter, NormalizedJob, from_epoch_ms

PAGE_SIZE = 10  # fixed by the API
MAX_PAGES = 400  # 4000 jobs
MIN_PARTIAL_PAGES = 2  # a pinned "hot" job can sit above the newest ones
MAX_PARTIAL_PAGES = 15
DETAIL_CONCURRENCY = 4


def parse_slug(slug: str) -> tuple[str, str, str]:
    parts = [p.strip() for p in slug.split("|")]
    if len(parts) not in (2, 3) or not parts[0] or not parts[1]:
        raise ValueError(f"Eightfold slug must look like 'tenant|domain.com[|location]', got {slug!r}")
    host = parts[0] if "." in parts[0] else f"{parts[0]}.eightfold.ai"
    return host, parts[1], parts[2] if len(parts) == 3 else ""


def clean_location(loc: str) -> str:
    """'United States, Multiple Locations, Multiple Locations' -> 'United States'."""
    parts = [p.strip() for p in loc.split(",")]
    kept = [p for p in parts if p and p.lower() != "multiple locations"]
    return ", ".join(kept)


def _epoch(value) -> object:
    return from_epoch_ms(int(value) * 1000) if value else None


class EightfoldAdapter(Adapter):
    ats = "eightfold"
    # A full scan is one request per 10 jobs (a 2,000-job tenant is 200 requests), plus one per new job.
    min_interval_minutes = 15
    full_scan_every = 8
    lane = "slow"

    def __init__(self) -> None:
        self._flavor: dict[str, str] = {}  # host -> "v2" | "pcsx", learned on first use

    def _resolve(self, slug: str) -> tuple[str, str, str]:
        return parse_slug(slug)

    # --- listing -------------------------------------------------------------------------------

    async def _page(self, client, host: str, domain: str, location: str, start: int) -> tuple[list[dict], int]:
        flavor = self._flavor.get(host)
        if flavor in (None, "pcsx"):
            try:
                resp = await http_util.get(client, f"https://{host}/api/pcsx/search", params={
                    "domain": domain, "query": "", "location": location, "start": start, "sort_by": "timestamp",
                })
                data = resp.json().get("data") or {}
                self._flavor[host] = "pcsx"
                return data.get("positions") or [], data.get("count") or 0
            except httpx.HTTPStatusError as e:
                if flavor == "pcsx" or e.response.status_code not in (403, 404):
                    raise
        params = {"domain": domain, "start": start, "num": PAGE_SIZE, "sort_by": "timestamp"}
        if location:
            params["location"] = location
        data = (await http_util.get(client, f"https://{host}/api/apply/v2/jobs", params=params)).json()
        self._flavor[host] = "v2"
        return data.get("positions") or [], data.get("count") or 0

    async def _list(self, client, host, domain, location, full: bool, known_ids: Set[str]) -> list[dict]:
        positions: list[dict] = []
        seen: set[str] = set()
        start, total = 0, None
        for page_no in range(MAX_PAGES if full else MAX_PARTIAL_PAGES):
            page, count = await self._page(client, host, domain, location, start)
            total = count if total is None else total
            fresh = [p for p in page if p.get("id") is not None and str(p["id"]) not in seen]
            if not fresh:
                break
            seen.update(str(p["id"]) for p in fresh)
            positions += fresh
            start += PAGE_SIZE
            if start >= (total or 0):
                break
            if not full and page_no + 1 >= MIN_PARTIAL_PAGES and all(str(p["id"]) in known_ids for p in fresh):
                break
        return positions

    # --- jobs ----------------------------------------------------------------------------------

    def _summary(self, host: str, p: dict) -> NormalizedJob:
        raw = p.get("locations") or ([p["location"]] if p.get("location") else [])
        locations = list(dict.fromkeys(c for c in (clean_location(loc) for loc in raw) if c))
        path = p.get("positionUrl") or f"/careers/job/{p['id']}"
        url = p.get("canonicalPositionUrl") or p.get("publicUrl") or f"https://{host}{path}"
        work = (p.get("workLocationOption") or p.get("work_location_option") or "").lower()
        return NormalizedJob(
            external_id=str(p["id"]),
            title=(p.get("name") or p.get("posting_name") or "").strip(),
            url=url,
            locations=locations,
            remote=work == "remote" or any("remote" in loc.lower() for loc in locations),
            department=p.get("department") or "",
            posted_at=_epoch(p.get("postedTs") or p.get("t_create")),
        )

    async def _detail(self, client, host: str, domain: str, pid: str) -> dict:
        if self._flavor.get(host) == "pcsx":
            resp = await http_util.get(client, f"https://{host}/api/pcsx/position_details",
                                       params={"position_id": pid, "domain": domain, "hl": "en"})
            return resp.json().get("data") or {}
        return (await http_util.get(client, f"https://{host}/api/apply/v2/jobs/{pid}", params={"domain": domain})).json()

    async def fetch(
        self, client: httpx.AsyncClient, slug: str, *, full: bool = True, known_ids: Set[str] = frozenset()
    ) -> list[NormalizedJob]:
        host, domain, location = self._resolve(slug)
        positions = await self._list(client, host, domain, location, full, known_ids)
        sem = asyncio.Semaphore(DETAIL_CONCURRENCY)

        async def build(p: dict) -> NormalizedJob | None:
            job = self._summary(host, p)
            if job.external_id in known_ids:
                return job
            async with sem:
                try:
                    detail = await self._detail(client, host, domain, job.external_id)
                except (httpx.HTTPError, ValueError):
                    return None  # still new next poll, so it gets another try
            if not detail:
                return None
            merged = self._summary(host, {**p, **detail})
            merged.description = html_to_text(detail.get("jobDescription") or detail.get("job_description") or "")
            merged.url = detail.get("publicUrl") or detail.get("canonicalPositionUrl") or job.url
            return merged

        jobs = await asyncio.gather(*(build(p) for p in positions))
        return [j for j in jobs if j is not None]
