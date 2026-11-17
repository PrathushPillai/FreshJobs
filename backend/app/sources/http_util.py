"""Small HTTP helpers shared by the big-company career-site adapters."""

import asyncio
import http.cookiejar
import json
import re
import time
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


def platform_limits(concurrency: int) -> httpx.Limits:
    """A pool for one platform's fetches: as many connections as it runs requests at once, plus a few for
    redirects and side requests. httpcore checks every pooled connection on each request, so a small pool per
    platform stays cheap where one big shared pool (POOL_LIMITS) didn't once thousands of hosts were in it."""
    return httpx.Limits(max_connections=concurrency + 5, max_keepalive_connections=concurrency, keepalive_expiry=60)


def platform_client(concurrency: int, **kwargs) -> httpx.AsyncClient:
    kwargs.setdefault("limits", platform_limits(concurrency))
    return client(**kwargs)


def _retry_after(resp: httpx.Response, attempt: int, backoff: float = 2.0) -> float:
    value = resp.headers.get("Retry-After", "")
    try:
        return min(float(value), MAX_BACKOFF_SECONDS)
    except ValueError:
        return min(backoff * 2**attempt, MAX_BACKOFF_SECONDS)


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
                  retries: int = MAX_RETRIES, backoff: float = 2.0, **kwargs) -> httpx.Response:
    """client.request(), retried with backoff on 429 and 503 (Retry-After is honoured, capped at a minute), and
    on a dropped or refused connection (TRANSIENT_ERRORS, twice). Every caller only reads, so a retry is safe.

    retry_statuses: more answers that mean "slow down" for a given platform (Avature's 406).
    retries, backoff: how many times a "slow down" answer is retried, and the first wait (doubled each time, at
    most a minute) when the answer has no Retry-After.
    The final response is returned as is (with its status raised), so callers decide what an error means.
    """
    for attempt in range(retries + 1):
        for tries in range(TRANSIENT_RETRIES + 1):
            try:
                resp = await client.request(method, url, **kwargs)
                break
            except TRANSIENT_ERRORS:
                if tries == TRANSIENT_RETRIES:
                    raise
                await asyncio.sleep(TRANSIENT_BACKOFF_SECONDS * (tries + 1))
        if resp.status_code not in retry_statuses or attempt == retries:
            resp.raise_for_status()
            return resp
        await asyncio.sleep(_retry_after(resp, attempt, backoff))
    raise AssertionError("unreachable")


async def get(client: httpx.AsyncClient, url: str, **kwargs) -> httpx.Response:
    return await request(client, "GET", url, **kwargs)


async def post(client: httpx.AsyncClient, url: str, **kwargs) -> httpx.Response:
    return await request(client, "POST", url, **kwargs)


class Spacer:
    """Spaces one platform's requests at least `seconds` apart, and holds them all during a pause (after a 429).

    One lock per event loop: each poll runs in its own asyncio.run(), and a lock made in an earlier poll's loop
    raises "bound to a different event loop" (2,361 Workable boards failed that way from 2026-11-04 to 11-09).
    The lanes are separate processes, so this paces one lane's requests, which is where a platform's boards are.
    """

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds
        self._locks: dict[int, asyncio.Lock] = {}
        self._last = 0.0
        self._paused_until = 0.0

    def pause(self, seconds: float) -> None:
        """Send nothing for `seconds` (a 429 from a platform that blocks a whole address for a while)."""
        self._paused_until = max(self._paused_until, time.monotonic() + seconds)

    async def wait(self) -> None:
        loop = id(asyncio.get_running_loop())
        lock = self._locks.get(loop)
        if lock is None:
            self._locks = {loop: asyncio.Lock()}  # drop the old loops' locks
            lock = self._locks[loop]
        async with lock:
            wait = max(self._last + self.seconds, self._paused_until) - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()


def bot_user_agent() -> str:
    """Our crawler's own, stable User-Agent, pointing at the public page that describes it (frontend/public/bot.html),
    as Cloudflare's Verified Bots program asks (2026-11-17). Used by the Workday and Phenom readers."""
    from app.config import settings

    return f"FreshJobsBot/1.0 (+{settings.public_url.rstrip('/')}/bot.html)"


ROBOTS_TOKEN = "FreshJobsBot"
ROBOTS_CACHE_SECONDS = 24 * 3600
ROBOTS_RETRY_SECONDS = 3600  # robots.txt couldn't be read: assume allowed, ask again in an hour
_robots: dict[str, tuple[float, object]] = {}  # host -> (fetched at, RobotFileParser or None)


async def robots_allows(client: httpx.AsyncClient, host: str, path: str) -> bool:
    """Whether the site's robots.txt lets FreshJobsBot read `path` on `host` (a scheme and host, e.g.
    "https://nvidia.wd5.myworkdayjobs.com"). Workday's robots.txt allows each
    public career site and disallows private ones per employer (NVIDIA: /NVIDIAExternalCareerSiteResearch/,
    /talentcommunity/), so a disallowed site is never read. When robots.txt can't be read, reading is allowed (the
    usual convention) and it's asked again within the hour."""
    import time
    from urllib.robotparser import RobotFileParser

    cached = _robots.get(host)
    if cached and time.time() - cached[0] < (ROBOTS_CACHE_SECONDS if cached[1] is not None else ROBOTS_RETRY_SECONDS):
        parser = cached[1]
    else:
        parser = None
        try:
            resp = await client.get(f"{host}/robots.txt", headers={"User-Agent": bot_user_agent()}, timeout=20)
            if resp.status_code == 200:
                parser = RobotFileParser()
                parser.parse(resp.text.splitlines())
        except httpx.HTTPError:
            pass
        _robots[host] = (time.time(), parser)
    return True if parser is None else parser.can_fetch(ROBOTS_TOKEN, f"{host}{path}")


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
