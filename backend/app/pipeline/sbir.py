"""US small businesses with recent federal R&D awards (SBIR/STTR): health (NIH), defense and robotics (DoD), deep
tech (NSF, DOE, NASA). Few of them advertise widely, but they hire engineers, scientists and data people.

    python -m app.pipeline.bulk_discovery sbir [--since-year 2023] [--min-employees 5] [--dry-run]

Source (researched 2026-11-12; the user approved the download): SBIR.gov's public award file,
https://data.www.sbir.gov/awarddatapublic_no_abstract/award_data_no_abstract.csv (about 87 MB, one row per award
since 1983; no robots.txt on that host). SBIR.gov's API answers 403, so the file is the way in. It's streamed to a
temporary file next to the database, read once and deleted. Only Company, Award Year, Agency, Number Employees,
State and Company Url are read: the contact and principal investigator names, phones and emails in the same rows
are never stored.

Firms with an award since `since_year` and at least `min_employees` staff (tiny R&D shops rarely have a job
board), one per website, go through bulk_discovery._boards_behind, which adds the boards that list a US job.
`maybe_import()` runs with the hourly discovery and re-reads the file only when its Last-Modified changes.
"""

import csv
import logging
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import httpx

from app.db import SessionLocal
from app.models import ProbeCache

log = logging.getLogger(__name__)

URL = "https://data.www.sbir.gov/awarddatapublic_no_abstract/award_data_no_abstract.csv"
USER_AGENT = "FreshJobs personal job feed (board discovery)"
COLUMNS = ("Company", "Award Year", "Agency", "Number Employees", "State", "Company Url")
_LEGAL = re.compile(r"[,.]?\s+(inc|llc|l\.l\.c|corp|corporation|co|company|ltd|lp|llp|pllc|pc|incorporated)\.?$", re.I)


def _int(value: str) -> int:
    try:
        return int(float((value or "0").replace(",", "")))
    except ValueError:
        return 0


def _site(url: str) -> str | None:
    url = (url or "").strip()
    if not url or " " in url or "@" in url:
        return None
    if not url.lower().startswith(("http://", "https://")):
        url = "https://" + url
    host = (urlparse(url).hostname or "").lower()
    if "." not in host or host.endswith((".gov", ".edu", ".mil")):
        return None
    return f"https://{host}"


def _name(company: str) -> str:
    name = re.sub(r"\s+", " ", company or "").strip()
    for _ in range(2):
        name = _LEGAL.sub("", name).strip(" ,.")
    return name.title() if name.isupper() else name


def firms(rows, since_year: int, min_employees: int) -> list[tuple[str, str]]:
    """(name, website) per distinct website, from award rows (dicts keyed by the file's column names)."""
    out: dict[str, tuple[str, str]] = {}
    for row in rows:
        if _int(row.get("Award Year")) < since_year or _int(row.get("Number Employees")) < min_employees:
            continue
        site = _site(row.get("Company Url"))
        name = _name(row.get("Company"))
        if site and name:
            out.setdefault(site.removeprefix("https://").removeprefix("www."), (name, site))
    return list(out.values())


async def _download(client: httpx.AsyncClient, path: Path) -> str:
    """Stream the file to `path`; returns its Last-Modified."""
    async with client.stream("GET", URL) as r:
        r.raise_for_status()
        with open(path, "wb") as f:
            async for chunk in r.aiter_bytes(1 << 20):
                f.write(chunk)
        return r.headers.get("Last-Modified", "")


def _read(path: Path, since_year: int, min_employees: int) -> list[tuple[str, str]]:
    with open(path, encoding="utf-8", errors="replace", newline="") as f:
        reader = csv.DictReader(f)
        rows = ({k: row.get(k) for k in COLUMNS} for row in reader)  # contact columns are dropped here
        return firms(rows, since_year, min_employees)


async def from_sbir(client: httpx.AsyncClient, since_year: int = 2023, min_employees: int = 5,
                    dry_run: bool = False) -> tuple[int, str]:
    """(boards added, the file's Last-Modified)."""
    from app.config import settings
    from app.pipeline.bulk_discovery import _boards_behind

    folder = Path(settings.database_url.removeprefix("sqlite:///")).parent if settings.database_url.startswith("sqlite") \
        else Path(tempfile.gettempdir())
    fd, name = tempfile.mkstemp(prefix="sbir-", suffix=".csv", dir=folder if folder.is_dir() else None)
    os.close(fd)
    path = Path(name)
    try:
        modified = await _download(client, path)
        sites = _read(path, since_year, min_employees)
    finally:
        path.unlink(missing_ok=True)
    log.info("SBIR awards since %d, firms with %d+ staff: %d websites", since_year, min_employees, len(sites))
    return await _boards_behind(client, sites, "SBIR awardees", dry_run=dry_run), modified


async def maybe_import() -> int:
    """Read the award file again only when SBIR.gov has published a new one (its Last-Modified changed)."""
    from app.sources import http_util

    async with http_util.client(timeout=120, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
        try:
            head = await client.head(URL)
        except httpx.HTTPError:
            return 0
        modified = head.headers.get("Last-Modified", "") if head.status_code == 200 else ""
        if not modified:
            return 0
        key = f"sbir:{modified}"[:200]
        with SessionLocal() as session:
            if session.get(ProbeCache, key):
                return 0
        added, _ = await from_sbir(client)
    with SessionLocal() as session:
        session.merge(ProbeCache(company_key=key, found=str(added)))
        session.commit()
    return added
