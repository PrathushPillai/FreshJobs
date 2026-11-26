"""Radancy (formerly TMP Worldwide, "TalentBrew") career sites, e.g. jobs.spectrum.com, careers.arm.com.

slug: the site's host, plus its language prefix when the site has one: "careers.arm.com",
"careers.moodys.com/en".

Verified live (Nov 2026):
- Every Radancy site's robots.txt is "User-agent: * / Disallow: /search-jobs/", so the search page and its
  /search-jobs/results JSON endpoint are off limits. Job pages (/job/...) and /sitemap.xml are allowed.
- /sitemap.xml lists every open job as /job/{city-slug}/{title-slug}/{org id}/{job id} (Spectrum: 1,480
  job URLs against 1,477 US results on its search page, so the sitemap is complete). Its <lastmod> is the
  time the sitemap was generated, not a job date.
- The same sitemap lists location landing pages, /location/{place-slug}-jobs/{org id}/{geonames ids}/{level},
  e.g. "austin-texas-united-states-jobs/33099/6252001-4736286-4671654/4". The first GeoNames id is the
  country (6252001 = United States). Matching each job's city slug against those place slugs tells us,
  without any request, which jobs are outside the US: on Arm 359 of 468, Moody's 225 of 337, Synopsys
  493 of 673. Those are skipped. Jobs whose city matches no place (Spectrum: 39 of 1,480) are kept.
- Each job page embeds a schema.org JobPosting (JSON-LD) with title, HTML description, datePosted
  (a date) and jobLocation, so a new job costs one request; known jobs cost nothing beyond the sitemap.
"""

import asyncio
import html as htmllib
import re
from collections.abc import Set
from urllib.parse import urlsplit

import httpx

from app.pipeline.normalize import html_to_text
from app.sources import http_util
from app.sources.base import Adapter, NormalizedJob, parse_iso

US_GEONAME = "6252001"
DETAIL_CONCURRENCY = 4
# New jobs whose page is read in one fetch; the rest are saved from the sitemap (title, city) and enrichment fills
# their descriptions later. Without a cap, a first scan of a big site (jobs.aus.com: ~10,000 jobs) read every job
# page in one go (2026-11-19, when the Radancy finder began adding big Workday employers' sites).
FIRST_SCAN_DETAILS = 300
DETAILS_PER_FETCH = 600

_JOB_URL = re.compile(r"<loc>\s*(https?://[^<\s]+?/job/([^/<]+)/([^/<]+)/(\d+)/(\d+))\s*</loc>", re.I)
_PLACE_URL = re.compile(r"<loc>\s*https?://[^<\s]+?/location/([^/<]+)-jobs/\d+/([\d-]+)/\d+\s*</loc>", re.I)
_LANG = re.compile(r"^[a-z]{2}(?:-[a-z]{2})?$", re.I)
_DETECT = re.compile(
    r"^https?://([^/?#]+)(/[a-z]{2}(?:-[a-z]{2})?)?/(?:job/[^/]+/[^/]+/\d+/\d+|search-jobs(?:/|$|\?)|"
    r"category/[^/]+/\d+/\d+|location/[^/]+/\d+/[\d-]+)", re.I)
_H1 = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S | re.I)
# Job pages with no JSON-LD (Jabil, The University of Kansas Health System, 2026-11-26): the description is the
# "ats-description" block inside the job-description section, and some templates list the places in a
# "job-detail-location" field ("Albuquerque, New Mexico, United States of America; Baja, Mexico; Remote - USA").
_ATS_DESCRIPTION = re.compile(r'<div class="ats-description">(.*?)</section>', re.S | re.I)
_DETAIL_LOCATION = re.compile(r'<dd class="[^"]*job-detail-location[^"]*">(.*?)</dd>', re.S | re.I)

# Radancy's CDN and its site id attribute appear on every page of a Radancy career site.
PAGE_MARKER = re.compile(r"tbcdn\.talentbrew\.com|data-company-site-id=|radancy\.net", re.I)


def base_url(slug: str) -> str:
    slug = slug.strip().strip("/")
    if "://" in slug:
        slug = slug.split("://", 1)[1]
    return f"https://{slug}"


def detect(url: str) -> str | None:
    """'careers.arm.com' for a Radancy job, search, category or location URL; None otherwise.

    Radancy sites live on the employer's own domain, so only Radancy's URL shapes identify one.
    """
    m = _DETECT.match(url.strip())
    if not m:
        return None
    host = m.group(1).lower()
    return host + (m.group(2).lower() if m.group(2) else "")


def slug_from_page(url: str, html: str) -> str | None:
    """The slug of a careers page that is itself a Radancy site (PAGE_MARKER), else None."""
    if not PAGE_MARKER.search(html or ""):
        return None
    found = detect(url)
    if found:
        return found
    parts = urlsplit(url if "://" in url else f"https://{url}")
    if not parts.hostname:
        return None
    first = parts.path.strip("/").split("/", 1)[0]
    return parts.hostname.lower() + (f"/{first.lower()}" if first and _LANG.match(first) else "")


def _slug_words(text: str) -> str:
    return " ".join(w.capitalize() if w.islower() else w for w in text.replace("-", " ").split())


def parse_sitemap(xml: str) -> tuple[list[dict], dict[str, set[str]]]:
    """([{id, url, city, title}], {place slug: country geoname ids}) from a Radancy /sitemap.xml."""
    places: dict[str, set[str]] = {}
    for place, geonames in _PLACE_URL.findall(xml):
        places.setdefault(place.lower(), set()).add(geonames.split("-")[0])
    jobs: dict[str, dict] = {}
    for url, city, title, _org, job_id in _JOB_URL.findall(xml):
        jobs.setdefault(job_id, {"id": job_id, "url": htmllib.unescape(url), "city": city.lower(), "title": title})
    return list(jobs.values()), places


def city_countries(city: str, places: dict[str, set[str]]) -> set[str]:
    """Country geoname ids of every listed place whose slug is this city, or starts with it."""
    found: set[str] = set()
    for place, countries in places.items():
        if place == city or place.startswith(city + "-"):
            found |= countries
    return found


def us_place_names(xml: str) -> dict[str, str]:
    """{city slug: "City, State, United States"} from a sitemap's US city pages
    (/location/olathe-kansas-united-states-jobs/38901/6252001-4273857-4276614/4). A job's own URL carries only the
    city ("olathe"), which the place parser can't place, so such jobs left the US feed: 561 of KU Health System's
    977 on its first scan. A city slug found in two states is left alone."""
    found: dict[str, set[str]] = {}
    for place, geonames in _PLACE_URL.findall(xml):
        ids = geonames.split("-")
        place = place.lower()
        if len(ids) != 3 or ids[0] != US_GEONAME or not place.endswith("-united-states"):
            continue
        words = place[: -len("-united-states")].split("-")
        for n in range(1, len(words)):  # the city is some leading words, the state the rest
            found.setdefault("-".join(words[:n]), set()).add(" ".join(words[n:]))
    out = {}
    for city, states in found.items():
        if len(states) == 1:
            state = next(iter(states))
            out[city] = f"{_slug_words(city)}, {_slug_words(state)}, United States"
    return out


def maybe_us(city: str, places: dict[str, set[str]]) -> bool:
    """False only when the city matches listed places and none of them is in the US."""
    countries = city_countries(city, places)
    return not countries or US_GEONAME in countries


class RadancyAdapter(Adapter):
    ats = "radancy"
    name = "radancy"
    # One sitemap request per poll (1-2 MB on big sites), plus one page per new job.
    min_interval_minutes = 30
    lane = "slow"
    max_concurrency = 4

    async def fetch(
        self, client: httpx.AsyncClient, slug: str, *, full: bool = True, known_ids: Set[str] = frozenset(),
        first_scan: bool = False,
    ) -> list[NormalizedJob]:
        base = base_url(slug)
        prefix = urlsplit(base).path.rstrip("/")
        for path in ("/sitemap.xml", f"{prefix}/job/"):
            if not await http_util.robots_allows(client, f"https://{urlsplit(base).hostname}", path):
                raise ValueError(f"robots.txt on {urlsplit(base).hostname} disallows {path}: not read")
        resp = await http_util.get(client, f"{base}/sitemap.xml", follow_redirects=True)
        if "<urlset" not in resp.text[:2000]:
            # An error or challenge page: treat it as a failed fetch rather than "no jobs", which would close them all.
            raise ValueError(f"{base}/sitemap.xml is not a sitemap")
        listed, places = parse_sitemap(resp.text)
        us_names = us_place_names(resp.text)
        sem = asyncio.Semaphore(DETAIL_CONCURRENCY)
        budget = [FIRST_SCAN_DETAILS if first_scan else DETAILS_PER_FETCH]

        async def build(j: dict) -> NormalizedJob | None:
            city = us_names.get(j["city"]) or _slug_words(j["city"])
            job = NormalizedJob(
                external_id=j["id"],
                title=_slug_words(j["title"]),
                url=j["url"],
                locations=[city] if city else [],
                remote="remote" in j["city"],
            )
            if j["id"] in known_ids:
                return job
            if budget[0] <= 0:
                return job  # saved from the sitemap; enrichment reads its page in a later poll
            budget[0] -= 1
            async with sem:
                try:
                    page = await http_util.get(client, j["url"], follow_redirects=True)
                except httpx.HTTPError:
                    return None  # still new next poll, so it gets another try
            posting = http_util.json_ld_job(page.text)
            if not posting:
                h1 = _H1.search(page.text)
                described = _ATS_DESCRIPTION.search(page.text)
                if not h1 and not described:
                    return None
                if h1:
                    job.title = html_to_text(h1.group(1)) or job.title
                if described:
                    job.description = html_to_text(described.group(1))
                listed_places = _DETAIL_LOCATION.search(page.text)
                if listed_places:
                    names = [p.strip() for p in html_to_text(listed_places.group(1)).split(";") if p.strip()]
                    job.locations = names or job.locations
                return job
            job.title = htmllib.unescape(posting.get("title") or "").strip() or job.title
            job.description = html_to_text(htmllib.unescape(posting.get("description") or ""))
            job.locations = http_util.ld_locations(posting) or job.locations
            job.posted_at = parse_iso(posting.get("datePosted"))
            job.remote = job.remote or posting.get("jobLocationType") == "TELECOMMUTE"
            return job

        wanted = [j for j in listed if maybe_us(j["city"], places)]
        jobs = await asyncio.gather(*(build(j) for j in wanted))
        return [j for j in jobs if j is not None]
