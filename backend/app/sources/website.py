"""A company's own careers page, for employers with no ATS board behind it (e.g. wizer-training.com/careers).

slug: the careers page URL. Three ways of finding the jobs, tried in order:

1. schema.org JobPosting data (JSON-LD) on the page itself. Sites add it so Google Jobs lists them.
2. Links to one page per job (/careers/data-analyst, /jobs/123, ...): each new one is fetched once, and
   read from its JobPosting data, or else from its <h1> and text if it reads like a job description.
3. Jobs written out on the page: a heading naming a role ("Support Specialist (Hybrid)"), followed by a
   section that reads like a job description (location, responsibilities, requirements, apply...).

It's a complete feed: a job that disappears from the page is closed. Polled hourly, since small
companies post rarely and these pages aren't APIs.
"""

import asyncio
import hashlib
import html as htmllib
import re
from collections.abc import Set
from urllib.parse import urljoin, urlparse

import httpx

from app.pipeline.detect import detect_board
from app.pipeline.normalize import html_to_text
from app.sources.base import Adapter, NormalizedJob, parse_iso
from app.sources.http_util import json_ld_jobs, ld_locations

MAX_JOB_PAGES = 60
DETAIL_CONCURRENCY = 3

_ROLE = re.compile(
    r"\b(engineer|developer|scientist|analyst|designer|manager|specialist|associate|coordinator|representative|"
    r"consultant|architect|administrator|intern|internship|lead|director|officer|technician|assistant|"
    r"accountant|recruiter|writer|marketer|strategist|researcher|advocate|agent|executive|head of)\b",
    re.I,
)
# Words that show a block of text is a job description rather than a product blurb.
_JOB_MARKERS = re.compile(
    r"responsibilit|requirement|qualification|what you.ll do|who you are|about the role|role overview|"
    r"experience level|years of experience|location:|salary|compensation|apply|benefits|full[- ]time",
    re.I,
)
_JOB_PATH = re.compile(r"/(jobs?|careers?|positions?|openings?|vacanc(?:y|ies)|opportunit(?:y|ies)|roles?|join-us|job-openings)/[^/?#]+", re.I)
_HEADING = re.compile(r"<(h[1-4])\b[^>]*>(.*?)</\1>", re.I | re.S)
_H1 = re.compile(r"<h1\b[^>]*>(.*?)</h1>", re.I | re.S)
_ANCHOR = re.compile(r"<a\b[^>]*href=[\"']([^\"'#]+)[\"'][^>]*>(.*?)</a>", re.I | re.S)
_LOCATION_LINE = re.compile(r"location\s*:?\s*\n?\s*([^\n]{2,80})", re.I)


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def _same_site(a: str, b: str) -> bool:
    ha, hb = (urlparse(a).hostname or "").removeprefix("www."), (urlparse(b).hostname or "").removeprefix("www.")
    return ha == hb or ha.endswith("." + hb) or hb.endswith("." + ha)


def _short_id(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()[:16]


def _from_ld(p: dict, page_url: str) -> NormalizedJob | None:
    title = _text(str(p.get("title") or p.get("name") or ""))
    if not title:
        return None
    ident = p.get("identifier")
    ident = ident.get("value") if isinstance(ident, dict) else ident
    url = p.get("url") if isinstance(p.get("url"), str) else page_url
    locations = ld_locations(p)
    remote = "TELECOMMUTE" in str(p.get("jobLocationType") or "").upper()
    return NormalizedJob(
        external_id=str(ident or url if url != page_url else ident or _short_id(title)),
        title=title,
        url=urljoin(page_url, url),
        locations=locations,
        remote=remote or any("remote" in loc.lower() for loc in locations),
        description=html_to_text(htmllib.unescape(str(p.get("description") or ""))),
        posted_at=parse_iso(str(p.get("datePosted") or "")),
    )


def inline_jobs(page: str, page_url: str) -> list[NormalizedJob]:
    """Jobs written out on the page itself: a role heading followed by a job-description section."""
    heads = list(_HEADING.finditer(page))
    jobs = []
    for i, m in enumerate(heads):
        # Headings often carry a badge ("Support Specialist (Hybrid)   Boston, MA"): the title is the first part.
        parts = [p.strip() for p in re.split(r"\s{3,}|\n", htmllib.unescape(re.sub(r"<[^>]+>", "\n", m.group(2)))) if p.strip()]
        title = parts[0] if parts else ""
        if not title or len(title) > 120 or not _ROLE.search(title):
            continue
        # The section runs to the next heading of the same or a higher level (sub-headings like
        # "What you'll do" belong to it).
        level = int(m.group(1)[1])
        end = next((h.start() for h in heads[i + 1 :] if int(h.group(1)[1]) <= level), min(len(page), m.end() + 20_000))
        section = html_to_text(page[m.end() : end])
        if len(section) < 200 or len(_JOB_MARKERS.findall(section)) < 2:
            continue
        loc = _LOCATION_LINE.search(section)
        location = (loc.group(1).strip() if loc else "") or (parts[1] if len(parts) > 1 else "")
        jobs.append(
            NormalizedJob(
                external_id=_short_id(title.lower()),
                title=title,
                url=page_url,
                locations=[location] if location else [],
                remote="remote" in (title + " " + location).lower(),
                description=section[:20_000],
            )
        )
    return jobs


def job_links(page: str, page_url: str) -> dict[str, str]:
    """Same-site links that look like one page per job: {url: link text}."""
    links: dict[str, str] = {}
    base_path = urlparse(page_url).path.rstrip("/")
    for href, inner in _ANCHOR.findall(page):
        url = urljoin(page_url, htmllib.unescape(href)).split("#")[0]
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not _same_site(url, page_url) or detect_board(url):
            continue
        if parsed.path.rstrip("/") == base_path or not _JOB_PATH.search(parsed.path):
            continue
        text = _text(inner)
        if url not in links and 2 <= len(text) <= 150:
            links[url] = text
        if len(links) >= MAX_JOB_PAGES:
            break
    return links


def _page_job(page: str, url: str, link_text: str) -> NormalizedJob | None:
    ld = json_ld_jobs(page)
    if ld:
        job = _from_ld(ld[0], url)
        if job:
            job.external_id = url
        return job
    h1 = _H1.search(page)
    # The page's <h1>, or the link text, whichever names a role; navigation or team pages name none.
    title = next((t for t in (_text(h1.group(1)) if h1 else "", link_text) if t and len(t) <= 150 and _ROLE.search(t)), "")
    body = html_to_text(re.sub(r"<(script|style|nav|header|footer)\b.*?</\1>", " ", page, flags=re.S | re.I))
    if not title or len(_JOB_MARKERS.findall(body)) < 2:
        return None  # a blog post or team page, not a job
    loc = _LOCATION_LINE.search(body)
    return NormalizedJob(
        external_id=url, title=title[:300], url=url, locations=[loc.group(1).strip()] if loc else [],
        remote="remote" in (title + " " + (loc.group(1) if loc else "")).lower(), description=body[:20_000],
    )


class WebsiteAdapter(Adapter):
    ats = "website"
    min_interval_minutes = 60

    async def fetch(
        self, client: httpx.AsyncClient, slug: str, *, known_ids: Set[str] = frozenset(), **_
    ) -> list[NormalizedJob]:
        url = slug if "://" in slug else "https://" + slug
        resp = await client.get(url, follow_redirects=True)
        resp.raise_for_status()
        page, page_url = resp.text, str(resp.url)

        jobs = [j for j in (_from_ld(p, page_url) for p in json_ld_jobs(page)) if j]
        if jobs:
            return jobs

        links = job_links(page, page_url)
        sem = asyncio.Semaphore(DETAIL_CONCURRENCY)

        async def one(link: str, text: str) -> NormalizedJob | None:
            if link in known_ids:  # already saved with its full description
                return NormalizedJob(external_id=link, title=text, url=link)
            async with sem:
                try:
                    r = await client.get(link, follow_redirects=True)
                except httpx.HTTPError:
                    return None
            return _page_job(r.text, link, text) if r.status_code == 200 else None

        jobs = [j for j in await asyncio.gather(*(one(u, t) for u, t in links.items())) if j]
        return jobs or inline_jobs(page, page_url)
