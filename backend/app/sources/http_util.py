"""Small HTTP helpers shared by the big-company career-site adapters."""

import asyncio
import json
import re
from datetime import datetime, timezone

import httpx

MAX_RETRIES = 3
MAX_BACKOFF_SECONDS = 60


def _retry_after(resp: httpx.Response, attempt: int) -> float:
    value = resp.headers.get("Retry-After", "")
    try:
        return min(float(value), MAX_BACKOFF_SECONDS)
    except ValueError:
        return min(2.0 * 2**attempt, MAX_BACKOFF_SECONDS)


async def request(client: httpx.AsyncClient, method: str, url: str, **kwargs) -> httpx.Response:
    """client.request(), retried with backoff on 429 and 503 (Retry-After is honoured, capped at a minute).

    The final response is returned as is (with its status raised), so callers decide what an error means.
    """
    for attempt in range(MAX_RETRIES + 1):
        resp = await client.request(method, url, **kwargs)
        if resp.status_code not in (429, 503) or attempt == MAX_RETRIES:
            resp.raise_for_status()
            return resp
        await asyncio.sleep(_retry_after(resp, attempt))
    raise AssertionError("unreachable")


async def get(client: httpx.AsyncClient, url: str, **kwargs) -> httpx.Response:
    return await request(client, "GET", url, **kwargs)


async def post(client: httpx.AsyncClient, url: str, **kwargs) -> httpx.Response:
    return await request(client, "POST", url, **kwargs)


def midnight_utc(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, tzinfo=timezone.utc)


_LD_JSON = re.compile(r"<script[^>]*application/ld\+json[^>]*>(.*?)</script>", re.S | re.I)


def json_ld_jobs(html: str) -> list[dict]:
    """Every schema.org JobPosting embedded in a page as JSON-LD."""
    found = []
    for block in _LD_JSON.findall(html):
        try:
            data = json.loads(block.strip())
        except ValueError:
            continue
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            items = data.get("@graph") or data.get("itemListElement") or [data]
        else:
            items = []
        for item in items:
            if isinstance(item, dict) and isinstance(item.get("item"), dict):  # ItemList entries
                item = item["item"]
            kind = item.get("@type") if isinstance(item, dict) else None
            if kind == "JobPosting" or (isinstance(kind, list) and "JobPosting" in kind):
                found.append(item)
    return found


def json_ld_job(html: str) -> dict:
    """The first schema.org JobPosting embedded in a page as JSON-LD ({} if none)."""
    jobs = json_ld_jobs(html)
    return jobs[0] if jobs else {}


def ld_locations(posting: dict) -> list[str]:
    """'City, Region, Country' strings from a JobPosting's jobLocation."""
    raw = posting.get("jobLocation") or []
    out = []
    for loc in raw if isinstance(raw, list) else [raw]:
        addr = (loc or {}).get("address") or {}
        if isinstance(addr, str):
            parts = [addr]
        else:
            parts = [addr.get("addressLocality"), addr.get("addressRegion"), addr.get("addressCountry")]
            if isinstance(parts[2], dict):
                parts[2] = parts[2].get("name")
        text = ", ".join(p.strip() for p in parts if isinstance(p, str) and p.strip() and p.strip() != "UNAVAILABLE")
        if text and text not in out:
            out.append(text)
    return out
