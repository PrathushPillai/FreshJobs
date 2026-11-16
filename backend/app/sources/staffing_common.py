"""Small helpers shared by the staffing-ATS adapters (JobDiva, Jobvite, Zoho Recruit, Manatal)."""

import html as htmllib
import re

from app.pipeline.locations import parse_locations

_URL = re.compile(r"""https?://[^\s"'<>()\\]+""", re.I)


def outside_us(locations: list[str]) -> bool:
    """True only if every listed place is recognisably outside the US (unknown places keep the job)."""
    places = [p for loc in locations for p in parse_locations(loc)]
    return bool(places) and all(p.country and p.country != "United States" for p in places)


def page_urls(html: str) -> list[str]:
    """Every absolute URL in a page, with HTML entities (&amp;, &#038;) decoded."""
    return [htmllib.unescape(u) for u in _URL.findall(htmllib.unescape(html or ""))]


def url_slug(title: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-") or "job"
