"""Small HTTP helpers shared by the big-company career-site adapters."""

import asyncio
import http.cookiejar
import json
import re
from datetime import datetime, timezone

import httpx

MAX_RETRIES = 3
MAX_BACKOFF_SECONDS = 60

COOKIE_DOMAINS = 50  # sites whose cookies a long-lived client keeps (the most recently set)


class BoundedCookieJar(http.cookiejar.CookieJar):
    """A cookie jar that keeps only the most recent COOKIE_DOMAINS sites' cookies.

    httpx copies the client's whole jar into a new one on every request (Client._merge_cookies). A poll visits
    thousands of employers' sites and each sets a few cookies, so every request got slower as the pass went
    on: on the 2-vCPU cloud server (2026-11-11) about three quarters of the slow lane's CPU went to copying
    cookies (cookiejar.deepvalues and set_cookie). A board's own requests all go to one site, so its cookies
    survive; old sites' cookies are dropped.
    """

    def __init__(self, max_domains: int = COOKIE_DOMAINS) -> None:
        super().__init__()
        self.max_domains = max_domains

    def set_cookie(self, cookie: http.cookiejar.Cookie) -> None:
        with self._cookies_lock:
            super().set_cookie(cookie)
            domain = cookie.domain
            if domain in self._cookies:
                self._cookies[domain] = self._cookies.pop(domain)  # most recently set last
            while len(self._cookies) > self.max_domains:
                self._cookies.pop(next(iter(self._cookies)))


# Connection pool for long runs. httpx keeps 20 idle connections by default; a poll runs 25 requests at a time
# per platform (Greenhouse, Lever, Ashby... each one API host), so connections were closed and reopened all the
# time, and the new HTTPS handshakes were the fast lane's biggest CPU cost on the cloud server (2026-11-12 profile:
# ssl.do_handshake first, then socket setup and teardown).
POOL_LIMITS = httpx.Limits(max_connections=300, max_keepalive_connections=200, keepalive_expiry=60)


def client(**kwargs) -> httpx.AsyncClient:
    """An AsyncClient for long runs over many sites (polls, discovery): a BoundedCookieJar and a bigger pool."""
    kwargs.setdefault("limits", POOL_LIMITS)
    return httpx.AsyncClient(cookies=BoundedCookieJar(), **kwargs)


def _retry_after(resp: httpx.Response, attempt: int) -> float:
    value = resp.headers.get("Retry-After", "")
    try:
        return min(float(value), MAX_BACKOFF_SECONDS)
    except ValueError:
        return min(2.0 * 2**attempt, MAX_BACKOFF_SECONDS)


RETRY_STATUSES = (429, 503)
# A dropped or refused connection is tried again too. A big Workday board is up to 100 list requests in a row,
# and one connection reset failed the whole board: on the cloud server (2026-11-11) 109 Workday boards that work
# (CVS Health 20k jobs, Lowe's, RTX, Micron...) failed a pass with ReadError, "Server disconnected" or ConnectError.
# Read timeouts aren't retried: a slow server would just take three times as long.
TRANSIENT_ERRORS = (httpx.ReadError, httpx.WriteError, httpx.RemoteProtocolError, httpx.ConnectError,
                    httpx.ConnectTimeout)
TRANSIENT_RETRIES = 2
TRANSIENT_BACKOFF_SECONDS = 2.0


async def request(client: httpx.AsyncClient, method: str, url: str, *, retry_statuses: tuple[int, ...] = RETRY_STATUSES,
                  **kwargs) -> httpx.Response:
    """client.request(), retried with backoff on 429 and 503 (Retry-After is honoured, capped at a minute), and
    on a dropped or refused connection (TRANSIENT_ERRORS, twice). Every caller only reads, so a retry is safe.

    retry_statuses: more answers that mean "slow down" for a given platform (Avature's 406).
    The final response is returned as is (with its status raised), so callers decide what an error means.
    """
    for attempt in range(MAX_RETRIES + 1):
        for tries in range(TRANSIENT_RETRIES + 1):
            try:
                resp = await client.request(method, url, **kwargs)
                break
            except TRANSIENT_ERRORS:
                if tries == TRANSIENT_RETRIES:
                    raise
                await asyncio.sleep(TRANSIENT_BACKOFF_SECONDS * (tries + 1))
        if resp.status_code not in retry_statuses or attempt == MAX_RETRIES:
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
