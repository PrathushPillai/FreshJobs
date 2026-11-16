"""Avature career portals ({tenant}.avature.net/{portal}, or a company's own host in front of one).

slug: "{host}/{portal}", optionally with the locale: "koch.avature.net/careers",
"bloomberg.avature.net/careers", "koch.avature.net/en_US/careers", "apply.deloittece.com/careers".

Verified live (Nov 2026) on Koch (behind gp.com/careers, molex.com...), Bloomberg and Synopsys:
- robots.txt ends with "Disallow: /" but allows each public portal ("Allow: /careers", "Allow: /*/careers")
  apart from URLs with `qtvc=`, and names "Sitemap: https://{host}/{portal}/sitemap_index.xml".
- That index lists one sitemap per locale (Koch: en_US, es_ES, de_DE...; Bloomberg and Synopsys: one).
  The locale sitemap lists every open job as .../{portal}/JobDetail/{title-slug}/{id} with a <lastmod>
  (Koch: 2,689 jobs). <lastmod> equals the RSS feed's pubDate on Koch, but on Synopsys it's later than
  the job's own "Date Posted", so a "Date Posted" field wins when the page has one.
- Not used: the RSS feed (SearchJobs/feed/) lists oldest first, 20 per page, and answers 406 past an
  offset of about 2,000, so it never reaches the newest Koch jobs; SearchJobs HTML pages hold 6 jobs each,
  and their filter field ids differ per tenant, so the US can't be filtered on the server.
- Job pages have no JSON-LD. Every tenant uses the same "article__content__view__field" blocks, each an
  optional __label and a __value ("Location(s)", "Country", "City", "Date Posted", "Company", plus the
  description in unlabeled or "Job Description" blocks), so a new job costs one request.
"""

import asyncio
import time
import html as htmllib
import re
from collections.abc import Set
from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx

from app.pipeline.normalize import html_to_text
from app.sources import http_util

# Avature's shared servers answer 406 to a burst of requests (15 boards in one poll, 2026-11-10; the same
# boards worked minutes later): a sign to slow down, like 429.
_RETRY = (406, 429, 503)
from app.sources.base import Adapter, NormalizedJob, parse_iso

DETAIL_CONCURRENCY = 3
# When a portal's locale sitemap comes back empty (Deloitte's since 2026-11-10, 1,400 open jobs), its
# SearchJobs pages are read instead: 10 jobs a page whatever jobRecordsPerPage asks, one page at a time.
SEARCH_PAGE_SIZE = 10
SEARCH_MAX_PAGES = 300
# The search fallback reads every page at most this often, and only the newest pages in between: Avature's
# servers are shared, and after one live read of Deloitte's 150 pages 24 other Avature boards got 406 (2026-11-11).
# Decided here, not through full_scan_every, which would make sitemap boards close jobs only on full scans.
SEARCH_FULL_EVERY_SECONDS = 6 * 3600
SEARCH_PAGE_DELAY = 1.5  # seconds between pages: back to back, Deloitte answered 406 (slow down) in a live poll
MIN_DESCRIPTION_BLOCK = 150  # unlabeled blocks shorter than this are titles, buttons or notes

_LOCALE = re.compile(r"^[a-z]{2}_[A-Z]{2}$")
_SITEMAP_LOC = re.compile(r"<sitemap>\s*<loc>\s*([^<\s]+)\s*</loc>", re.I)
_JOB = re.compile(
    r"<loc>\s*(https?://[^<\s]+?/JobDetail/(?:([^<\s]*)/)?(\d+))\s*</loc>(?:\s*<lastmod>\s*([^<\s]+)\s*</lastmod>)?",
    re.I)
_SEARCH_JOB = re.compile(r'href="(https?://[^"]+?/JobDetail/(?:([^"/]*)/)?(\d+))"', re.I)
_DETECT = re.compile(
    r"^https?://([^/?#]+)/(?:([a-z]{2}_[A-Z]{2})/)?([A-Za-z0-9_-]+)/(?:JobDetail|SearchJobs|Login|Register|"
    r"ApplicationMethods|sitemap)\b", re.I)
_AVATURE_LINK = re.compile(
    r"https?://([a-z0-9-]+\.avature\.net)/(?:([a-z]{2}_[A-Z]{2})/)?([A-Za-z0-9_-]+)(?=[/?\"'#&\s])", re.I)
_FIELD = re.compile(r'<div\s+class="article__content__view__field(?:\s[^"]*)?"', re.I)
_LABEL = re.compile(r'<div\s+class="article__content__view__field__label[^"]*"[^>]*>(.*?)</div>', re.S | re.I)
_DIV = re.compile(r"<(/?)div\b[^>]*>", re.I)
# Deloitte's template: og:title is "Check out this job at Deloitte, {title}", places are paragraphs in the
# header, and the description is only in the page's JSON-LD (all 1,421 Deloitte jobs were saved without one).
_SHARE_PREFIX = re.compile(r"^Check out this job at [^,]{1,60},\s*", re.I)
_HEADER_PLACES = re.compile(r'<div class="article__header--locations[^"]*"[^>]*>(.*?)</div>\s*</div>', re.S | re.I)
_PARAGRAPH = re.compile(r'<p class="paragraph">(.*?)</p>', re.S | re.I)
_OG_TITLE = re.compile(r'<meta[^>]+property="og:title"[^>]+content="([^"]*)"', re.I)
_TITLE_TAG = re.compile(r"<title>(.*?)</title>", re.S | re.I)
_NOT_PORTAL = {"portal", "_cms", "favicon.ico", "sitemap.xml", "robots.txt", "image", "images"}

# Avature's portal assets and field markup.
PAGE_MARKER = re.compile(r"avature\.net|article__content__view__field|/portal/\d+/(?:images|favicon)", re.I)

_DATE_FORMATS = ("%d-%b-%Y", "%d-%B-%Y", "%m/%d/%Y", "%d/%m/%Y", "%B %d, %Y", "%b %d, %Y", "%d %B %Y",
                 "%d %b %Y", "%Y/%m/%d", "%d.%m.%Y")


def parse_slug(slug: str) -> tuple[str, str, str]:
    """(host, locale or "", portal) from a slug."""
    slug = slug.strip().strip("/")
    if "://" in slug:
        slug = slug.split("://", 1)[1]
    parts = [p for p in slug.split("/") if p]
    if len(parts) == 2:
        return parts[0].lower(), "", parts[1]
    if len(parts) == 3 and _LOCALE.match(parts[1]):
        return parts[0].lower(), parts[1], parts[2]
    raise ValueError(f"Avature slug must look like 'host/portal' or 'host/xx_XX/portal', got {slug!r}")


def detect(url: str) -> str | None:
    """'koch.avature.net/careers' for an Avature portal URL; None otherwise.

    Any *.avature.net URL with a portal path counts. On a company's own host, only Avature's page names
    (JobDetail, SearchJobs...) identify one.
    """
    url = url.strip()
    m = _DETECT.match(url)
    if m:
        return f"{m.group(1).lower()}/{m.group(3)}"
    parts = urlsplit(url if "://" in url else f"https://{url}")
    host = (parts.hostname or "").lower()
    if not host.endswith(".avature.net"):
        return None
    segments = [s for s in parts.path.split("/") if s]
    if segments and _LOCALE.match(segments[0]):
        segments = segments[1:]
    if not segments or segments[0].lower() in _NOT_PORTAL:
        return None
    return f"{host}/{segments[0]}"


def slug_from_page(url: str, html: str) -> str | None:
    """The Avature portal a careers page is, or links to (gp.com/careers links koch.avature.net/careers)."""
    found = detect(url)
    if found:
        return found
    html = html or ""
    counts: dict[str, int] = {}
    for host, _locale, portal in _AVATURE_LINK.findall(html):
        if portal.lower() not in _NOT_PORTAL:
            key = f"{host.lower()}/{portal}"
            counts[key] = counts.get(key, 0) + 1
    if counts:
        return max(counts, key=counts.get)
    if "article__content__view__field" in html:  # an Avature portal on the company's own host
        parts = urlsplit(url if "://" in url else f"https://{url}")
        segments = [s for s in parts.path.split("/") if s and not _LOCALE.match(s)]
        if parts.hostname and segments:
            return f"{parts.hostname.lower()}/{segments[0]}"
    return None


def parse_date(text: str) -> datetime | None:
    text = (text or "").strip()
    if not text:
        return None
    iso = parse_iso(text)
    if iso:
        return iso
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _block(html: str, start: int) -> str:
    """The <div> starting at `start`, through its matching </div>."""
    depth = 0
    for m in _DIV.finditer(html, start):
        depth += -1 if m.group(1) else 1
        if depth == 0:
            return html[start:m.end()]
    return html[start:]


def page_fields(html: str) -> list[tuple[str, str]]:
    """[(label or "", value text)] for every article__content__view__field block on a job page."""
    out = []
    for m in _FIELD.finditer(html):
        block = _block(html, m.start())
        label_match = _LABEL.search(block)
        label = html_to_text(label_match.group(1)).strip() if label_match else ""
        value_html = block.replace(label_match.group(0), "", 1) if label_match else block
        value = html_to_text(value_html).strip()
        if value:
            out.append((label, value))
    return out


def _title_from_slug(slug_text: str, job_id: str) -> str:
    text = re.sub(rf"-{job_id}$", "", slug_text or "")
    return text.replace("-", " ").strip()


def parse_job_page(html: str, job_id: str) -> dict:
    """{title, locations, description, posted_at, remote, company} from a JobDetail page."""
    fields = page_fields(html)
    by_label: dict[str, str] = {}
    for label, value in fields:
        if label:
            by_label.setdefault(label.lower().rstrip(":"), value)

    def first(*patterns: str) -> str:
        for pattern in patterns:
            for label, value in by_label.items():
                if re.fullmatch(pattern, label):
                    return value
        return ""

    title = first(r"job title", r"title", r"position title")
    if not title:
        og = _OG_TITLE.search(html)
        tag = _TITLE_TAG.search(html)
        title = og.group(1) if og and og.group(1).strip() else (
            html_to_text(tag.group(1)).split(" - ")[0] if tag else "")
    # og:title is escaped twice on some tenants ("Analytics &amp;amp; Sales"); titles may end in "-{id}".
    title = htmllib.unescape(htmllib.unescape(title)).strip()
    title = re.sub(rf"\s*-\s*{job_id}\s*$", "", title)
    title = _SHARE_PREFIX.sub("", title)

    location = first(r"locations?(\(s\))?", r"job locations?", r"work locations?", r"primary location")
    if location:
        locations = [p.strip() for p in re.split(r"\s*\|\s*|\n+", location) if p.strip()]
    else:
        place = ", ".join(p for p in (first(r"city"), first(r"state|state/province|province|region"),
                                      first(r"country|country/region|jurisdiction / country")) if p)
        locations = [place] if place else []
    if not locations:
        header = _HEADER_PLACES.search(html)
        if header:
            locations = [t for t in (html_to_text(p).strip() for p in _PARAGRAPH.findall(header.group(1))) if t]

    description_parts = [
        value for label, value in fields
        if (not label and len(value) >= MIN_DESCRIPTION_BLOCK)
        or re.search(r"description|responsibilit|qualification|requirement|about the (role|job)", label, re.I)
    ]
    description = "\n\n".join(dict.fromkeys(description_parts))
    posted_at = parse_date(first(r"date posted", r"posted( date| on)?", r"posting date", r"publish(ed)? date"))
    if not description:  # Deloitte's template has the description only in its JSON-LD
        ld = http_util.json_ld_job(html)
        description = html_to_text(ld.get("description") or "")
        posted_at = posted_at or parse_iso(ld.get("datePosted"))
    remote_value = first(r"(eligible for )?remote( eligible| jobs?| work)?", r"remote eligible", r"workplace type")
    return {
        "title": title,
        "locations": locations,
        "description": description,
        "posted_at": posted_at,
        "remote": remote_value.lower().startswith(("yes", "remote", "fully remote"))
        or any("remote" in loc.lower() for loc in locations),
        "company": first(r"company", r"business", r"legal entity"),
    }


def parse_sitemap(xml: str) -> list[dict]:
    """[{id, url, slug, lastmod}] for every JobDetail URL in a locale sitemap."""
    jobs: dict[str, dict] = {}
    for url, slug_text, job_id, lastmod in _JOB.findall(xml):
        jobs.setdefault(job_id, {"id": job_id, "url": htmllib.unescape(url), "slug": slug_text, "lastmod": lastmod})
    return list(jobs.values())


def pick_locale(sitemaps: list[str]) -> list[str]:
    """The English locale's sitemap (en_US first), or the first one."""
    for pattern in (r"/en_US/", r"/en_[A-Z]{2}/"):
        hits = [s for s in sitemaps if re.search(pattern, s)]
        if hits:
            return hits[:1]
    return sitemaps[:1]


class AvatureAdapter(Adapter):
    ats = "avature"
    name = "avature"
    # One sitemap request per poll (up to ~4 MB with its hreflang alternates), plus one page per new job.
    min_interval_minutes = 60
    lane = "slow"
    max_concurrency = 3

    def __init__(self) -> None:
        self._incomplete: set[str] = set()
        self._full_search_at: dict[str, float] = {}  # slug -> time.monotonic() of its last full search

    def last_fetch_complete(self, slug: str) -> bool:
        return slug not in self._incomplete

    async def _search(
        self, client: httpx.AsyncClient, search_url: str, known_ids: Set[str] | None = None,
    ) -> tuple[list[dict], bool]:
        """Jobs on the portal's SearchJobs pages, and whether the list reached its end.

        With known_ids (a partial scan) it stops after the first page with no unknown job, newest first.
        """
        jobs: dict[str, dict] = {}
        for page in range(SEARCH_MAX_PAGES):
            resp = await http_util.get(
                client, search_url, params={"jobRecordsPerPage": SEARCH_PAGE_SIZE, "jobOffset": page * SEARCH_PAGE_SIZE},
                follow_redirects=True, retry_statuses=_RETRY)
            fresh = 0
            for url, slug_text, job_id in _SEARCH_JOB.findall(resp.text):
                if job_id not in jobs:
                    jobs[job_id] = {"id": job_id, "url": htmllib.unescape(url), "slug": slug_text, "lastmod": ""}
                    fresh += 1
            if not fresh:
                return list(jobs.values()), True
            if known_ids is not None and all(j in known_ids for j in list(jobs)[-fresh:]):
                return list(jobs.values()), False
            await asyncio.sleep(SEARCH_PAGE_DELAY)
        return list(jobs.values()), False

    async def _sitemap(self, client: httpx.AsyncClient, host: str, locale: str, portal: str) -> tuple[str | None, str]:
        """(the locale sitemap, its URL); the text is None when the sitemap is empty."""
        if locale:
            url = f"https://{host}/{locale}/{portal}/sitemap.xml"
        else:
            url = f"https://{host}/{portal}/sitemap_index.xml"
        text = (await http_util.get(client, url, follow_redirects=True, retry_statuses=_RETRY)).text
        for _depth in range(2):
            if "<sitemapindex" not in text[:2000]:
                break
            chosen = pick_locale([htmllib.unescape(s) for s in _SITEMAP_LOC.findall(text)])
            if not chosen:
                break
            url = chosen[0]
            text = (await http_util.get(client, url, follow_redirects=True, retry_statuses=_RETRY)).text
        if not text.strip() and url.endswith("/sitemap.xml"):
            return None, url
        if "<urlset" not in text[:2000]:
            # An error or challenge page: a failed fetch, not "no jobs" (which would close every job).
            raise ValueError(f"no Avature job sitemap for {host}/{portal}")
        return text, url

    async def fetch(
        self, client: httpx.AsyncClient, slug: str, *, full: bool = True, known_ids: Set[str] = frozenset(),
        first_scan: bool = False,
    ) -> list[NormalizedJob]:
        host, locale, portal = parse_slug(slug)
        text, sitemap_url = await self._sitemap(client, host, locale, portal)
        if text is None:
            last_full = self._full_search_at.get(slug)
            read_all = first_scan or last_full is None or time.monotonic() - last_full >= SEARCH_FULL_EVERY_SECONDS
            listed, complete = await self._search(
                client, sitemap_url[: -len("sitemap.xml")] + "SearchJobs/", None if read_all else known_ids)
            if read_all and complete:
                self._full_search_at[slug] = time.monotonic()
            if not listed:  # an empty sitemap and no search results: not trusted as "no jobs"
                raise ValueError(f"no Avature job sitemap or search results for {host}/{portal}")
            (self._incomplete.discard if complete else self._incomplete.add)(slug)
        else:
            listed = parse_sitemap(text)
            self._incomplete.discard(slug)
        sem = asyncio.Semaphore(DETAIL_CONCURRENCY)

        async def build(j: dict) -> NormalizedJob | None:
            job = NormalizedJob(
                external_id=j["id"],
                title=_title_from_slug(j["slug"], j["id"]) or j["id"],
                url=j["url"],
                posted_at=parse_date(j["lastmod"]),
            )
            if j["id"] in known_ids:
                return job
            async with sem:
                try:
                    page = await http_util.get(client, j["url"], follow_redirects=True, retry_statuses=_RETRY)
                except httpx.HTTPError:
                    return None  # still new next poll, so it gets another try
            info = parse_job_page(page.text, j["id"])
            if not info["title"] and not info["description"]:
                return None
            job.title = info["title"] or job.title
            job.locations = info["locations"]
            job.description = info["description"]
            job.posted_at = info["posted_at"] or job.posted_at
            job.remote = info["remote"]
            job.company_name = info["company"]
            return job

        jobs = await asyncio.gather(*(build(j) for j in listed))
        return [j for j in jobs if j is not None]
