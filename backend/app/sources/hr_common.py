"""Helpers shared by the HR/payroll-suite adapters (Taleo, ADP, Paylocity)."""

import re
from datetime import datetime

from app.pipeline.locations import parse_locations
from app.sources import http_util

US = "United States"
_DATE_FORMATS = ("%m/%d/%Y", "%m/%d/%y", "%b %d, %Y", "%B %d, %Y", "%d-%b-%Y", "%Y-%m-%d")
_DATE_PREFIX = re.compile(r"^\s*([A-Za-z]{3,9} \d{1,2}, \d{4}|\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{2}-\d{2}|\d{1,2}-[A-Za-z]{3}-\d{4})")


def parse_day(value: str | None) -> datetime | None:
    """'11/09/2026', 'Nov 9, 2026', '11/09/2026, 2:52:46 PM' or '2026-11-09' -> midnight UTC that day."""
    m = _DATE_PREFIX.match(value or "")
    if not m:
        return None
    for fmt in _DATE_FORMATS:
        try:
            d = datetime.strptime(m.group(1), fmt)
        except ValueError:
            continue
        return http_util.midnight_utc(d.year, d.month, d.day)
    return None


def outside_us(locations: list[str]) -> bool:
    """True only if every location clearly names a country other than the US.

    Unknown or empty locations count as possibly US (the poller decides us_eligible later).
    """
    countries = set()
    for loc in locations:
        places = parse_locations(loc)
        if not places or any(p.country in (None, US) for p in places):
            return False
        countries.update(p.country for p in places)
    return bool(countries)
