"""Hacker News "Ask HN: Who is hiring?" threads, through the public Algolia HN API (no key).

slug: unused (e.g. "whoishiring"). Each month's thread has a few hundred top-level comments, one per
company, mostly startups that post nowhere else. By convention a comment starts with a line like
"Company | Role | Location | Remote | ...", which is parsed into title, company and locations.

It's a complete feed for the current month's thread: when a new thread starts, last month's posts close.
A comment linking to a known ATS board gets that link as its URL (so the board is harvested and polled
directly from then on); otherwise the URL is the HN comment.
"""

import html
import re

import httpx

from app.pipeline.detect import detect_board
from app.pipeline.locations import parse_locations
from app.pipeline.normalize import html_to_text
from app.sources.base import Adapter, NormalizedJob, parse_iso

SEARCH = "https://hn.algolia.com/api/v1/search_by_date"
ITEM = "https://hn.algolia.com/api/v1/items/{id}"

_ROLE = re.compile(
    r"\b(engineer|engineering|developer|scientist|analyst|designer|manager|lead|architect|intern|researcher|"
    r"sre|devops|swe|cto|founding|head of|director|specialist|recruiter|consultant|administrator|"
    r"programmer|associate|marketer|writer|ops|operations|sales|product|data|ml|ai)\b",
    re.I,
)
_HREF = re.compile(r'href="([^"]+)"')


# Header fields that are neither the role nor a place.
_META = re.compile(
    r"\b(onsite|on-site|in-office|hybrid|full[- ]?time|part[- ]?time|contract|contractor|freelance|visa|"
    r"sponsor|salary|equity|stipend|required|series [a-z]|seed|funded|hourly|openings?)\b|https?://|[$€£]|\d+k\b",
    re.I,
)


def _is_place(part: str) -> bool:
    """A real place ("Austin, TX", "REMOTE (US)"), not text the gazetteer merely took as a city."""
    return "remote" in part.lower() or any(p.country for p in parse_locations(part))


def parse_header(text: str) -> tuple[str, str, list[str], bool]:
    """(company, title, locations, remote) from a comment's first line, "Company | Role | Place | ..."."""
    first = html_to_text(text.split("<p>", 1)[0]).strip().split("\n", 1)[0]
    parts = [p.strip() for p in first.split("|") if p.strip() and len(p.strip()) <= 120]
    if len(parts) < 2:
        return "", "", [], False
    company, rest = parts[0], parts[1:]
    places = [p for p in rest if _is_place(p) and not (_META.search(p) and "remote" not in p.lower())]
    candidates = [p for p in rest if p not in places and not _META.search(p)]
    # Some posts name no role in the header ("Shepherd | ONSITE | San Francisco, CA"); the roles are in the text.
    title = next((p for p in candidates if _ROLE.search(p)), candidates[0] if candidates else "Open roles (see post)")
    remote = any("remote" in p.lower() for p in rest)
    return company[:200], title[:300], places, remote


def _apply_url(text: str) -> str | None:
    for href in _HREF.findall(text):
        url = html.unescape(href)
        if detect_board(url):
            return url
    return None


class HackerNewsAdapter(Adapter):
    ats = "hackernews"
    aggregator = True
    min_interval_minutes = 60

    async def fetch(self, client: httpx.AsyncClient, slug: str, **_) -> list[NormalizedJob]:
        resp = await client.get(SEARCH, params={"tags": "story,author_whoishiring", "hitsPerPage": 10})
        resp.raise_for_status()
        story = next((h for h in resp.json().get("hits") or [] if "who is hiring" in (h.get("title") or "").lower()), None)
        if story is None:
            raise ValueError("no 'Who is hiring?' thread found")
        resp = await client.get(ITEM.format(id=story["objectID"]))
        resp.raise_for_status()
        jobs = []
        for c in resp.json().get("children") or []:
            text = c.get("text") or ""
            if not text or not c.get("id"):
                continue  # deleted or flagged
            company, title, locations, remote = parse_header(text)
            if not company:
                continue
            jobs.append(
                NormalizedJob(
                    external_id=str(c["id"]),
                    title=title,
                    url=_apply_url(text) or f"https://news.ycombinator.com/item?id={c['id']}",
                    locations=locations,
                    remote=remote,
                    description=html_to_text(text),
                    department="via Hacker News",
                    posted_at=parse_iso(c.get("created_at")),
                    company_name=company,
                )
            )
        return jobs
