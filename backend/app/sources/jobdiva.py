"""JobDiva candidate portals (www1/www2.jobdiva.com/portal/?a={key}), used by many IT staffing firms.

slug: "{host}/{key}", e.g. "www2/sfjdnwo10b4yndh294ykxqcwq1eg1u0b38rztezound0kwviecrtyjw5qp9pv17v". The host
is the JobDiva environment the portal lives on (www1, www2, ...); a bare key means www2.

Verified live (Nov 2026):
- robots.txt on www1/www2.jobdiva.com allows everything and lists a sitemap index of portal keys
  (/candidates/myjobs/sitemapindex.jsp?env=N), but every per-portal sitemap answers "Cannot request more
  than 200 rows at a time", so it can't be used for jobs.
- The portal's own search (job/searchjobsportal) needs a Cloudflare Turnstile token, so it's not used.
- The portal's public "RSS feed" button builds /candidates/myjobs/getjobsrssfeed.jsp?a={key}&noofjobs=N:
  one request returns up to 200 newest jobs with the full HTML description and the posting time.
  Titles look like "Release Train Engineer (RTE) (26-12502) - NJ - New Jersey": the title, the firm's
  job reference, then the place ("ST - City", "ST", or blank).
- The employer shown is the staffing firm (the client is rarely named), which is who sponsors.
- A portal with 200+ open jobs only shows its newest 200, so such a fetch is marked incomplete and closes
  nothing (`last_fetch_complete`); those jobs are closed by the liveness checks instead.
"""

import asyncio
import html as htmllib
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import httpx

from app.pipeline.normalize import html_to_text
from app.sources import http_util
from app.sources.base import Adapter, NormalizedJob
from app.sources.staffing_common import outside_us, page_urls

FEED_LIMIT = 200  # the most the feed returns
DEFAULT_HOST = "www2"
BLANK_RETRIES = 3
BLANK_RETRY_SECONDS = 3.0  # waits 3, 6, 9 s: blank answers come in bursts (6 portals in one poll still failed after 2 + 4 s)

_ITEM = re.compile(r"<item>(.*?)</item>", re.S)
_TITLE_REF = re.compile(r"^(.*\S)\s*\(([^()]*)\)\s+-\s*(.*)$", re.S)
_STATE_CITY = re.compile(r"^([A-Z]{2})\s+-\s+(.+)$")
_TZ = {"EST": -5, "EDT": -4, "CST": -6, "CDT": -5, "MST": -7, "MDT": -6, "PST": -8, "PDT": -7, "UTC": 0, "GMT": 0}
_DETECT = re.compile(r"^https?://(www\d*)\.jobdiva\.com/(?:portal/?|candidates/[^?#]*)(?:[?#].*)?$", re.I)

PAGE_MARKER = re.compile(r"(?:www\d*\.)?jobdiva\.com/(?:portal|candidates/myjobs)", re.I)


def parse_slug(slug: str) -> tuple[str, str]:
    host, _, key = slug.strip().rpartition("/")
    return (host.split(".")[0].lower() or DEFAULT_HOST), key


def detect(url: str) -> str | None:
    """The slug for a JobDiva portal or job URL (needs its ?a= key), else None."""
    url = htmllib.unescape(url or "")
    m = _DETECT.match(url)
    if not m:
        return None
    parsed = urlparse(url)
    key = (parse_qs(parsed.query).get("a") or parse_qs(parsed.fragment.partition("?")[2]).get("a") or [""])[0]
    key = key.strip()
    if not re.fullmatch(r"[a-z0-9]{20,}", key):
        return None
    return f"{m.group(1).lower()}/{key}"


def slug_from_page(url: str, html: str) -> str | None:
    for link in page_urls(html):
        slug = detect(link)
        if slug:
            return slug
    return None


def _field(item: str, tag: str) -> str:
    m = re.search(rf"<{tag}>\s*(?:<!\[CDATA\[(.*?)\]\]>|(.*?))\s*</{tag}>", item, re.S)
    if not m:
        return ""
    return m.group(1) if m.group(1) is not None else htmllib.unescape(m.group(2) or "")


def parse_date(value: str) -> datetime | None:
    """'November 9, 2026 2:35:10 PM EST' -> aware datetime."""
    parts = (value or "").strip().rsplit(" ", 1)
    if len(parts) != 2:
        return None
    try:
        dt = datetime.strptime(parts[0], "%B %d, %Y %I:%M:%S %p")
    except ValueError:
        return None
    offset = _TZ.get(parts[1].upper())
    if offset is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.replace(tzinfo=timezone(timedelta(hours=offset))).astimezone(timezone.utc)


def split_title(raw: str) -> tuple[str, str]:
    """('Release Train Engineer (RTE)', 'New Jersey, NJ') from 'Release Train Engineer (RTE) (26-12502) - NJ - New Jersey'."""
    raw = " ".join(htmllib.unescape(raw or "").split())
    m = _TITLE_REF.match(raw)
    if not m:
        return raw, ""
    title, place = m.group(1).strip(), m.group(3).strip(" -")
    sc = _STATE_CITY.match(place)
    if sc:
        place = f"{sc.group(2).strip()}, {sc.group(1)}"
    return title, place


def parse_feed(xml: str, host: str, key: str) -> list[NormalizedJob]:
    jobs = []
    for item in _ITEM.findall(xml or ""):
        link = htmllib.unescape(_field(item, "link"))
        job_id = (parse_qs(urlparse(link).query).get("id") or [""])[0]
        title, place = split_title(_field(item, "title"))
        if not job_id or not title:
            continue
        description = html_to_text(_field(item, "description"))
        jobs.append(
            NormalizedJob(
                external_id=job_id,
                title=title,
                url=f"https://{host}.jobdiva.com/candidates/myjobs/openjob_outside.jsp?a={key}&from=COMP&id={job_id}",
                locations=[place] if place else [],
                remote=bool(re.search(r"\bremote\b", f"{title} {place}", re.I)),
                description=description,
                posted_at=parse_date(_field(item, "pubDate")),
            )
        )
    return jobs


class JobDivaAdapter(Adapter):
    ats = "jobdiva"
    min_interval_minutes = 15
    capped_ages_out = True  # the RSS feed shows the newest 200 jobs only
    max_concurrency = 2  # 4 at a time drew bursts of blank answers (2026-11-11)

    def __init__(self) -> None:
        self._capped: dict[str, bool] = {}

    def last_fetch_complete(self, slug: str) -> bool:
        return not self._capped.get(slug, False)

    async def fetch(self, client: httpx.AsyncClient, slug: str, **_) -> list[NormalizedJob]:
        host, key = parse_slug(slug)
        for attempt in range(BLANK_RETRIES + 1):
            resp = await http_util.get(
                client,
                f"https://{host}.jobdiva.com/candidates/myjobs/getjobsrssfeed.jsp",
                params={"a": key, "noofjobs": FEED_LIMIT, "versionid": 2},
            )
            # Under load JobDiva sometimes answers 200 with a page of blank lines; asked again a moment later it
            # returns the feed (2026-11-11: 18 of 276 portals failed a poll this way, 5 of 24 in a burst of 4 at a time).
            if resp.text.strip() or attempt == BLANK_RETRIES:
                break
            await asyncio.sleep(BLANK_RETRY_SECONDS * (attempt + 1))
        if "<rss" not in resp.text[:500]:
            raise httpx.HTTPStatusError(f"JobDiva portal {slug} returned no feed", request=resp.request, response=resp)
        jobs = parse_feed(resp.text, host, key)
        self._capped[slug] = resp.text.count("<item>") >= FEED_LIMIT
        return [j for j in jobs if not outside_us(j.locations)]
