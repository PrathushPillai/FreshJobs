"""Google careers (www.google.com/about/careers/applications/jobs/results/).

Google has no public JSON API any more (careers.google.com/api/v3/search/ answers 404), but each
results page embeds its 20 jobs, with full descriptions, as JSON in an
`AF_initDataCallback({key: 'ds:1', ... data: [...]})` script. We read that.

slug: a query string of the results page's filters, e.g. "location=United States" or
"location=United States&target_level=EARLY"; "" or "all" for every job.

Verified live (Nov 2026):
- `sort_by=date` is newest-first by the publish time (field 13); `page=N` is 1-based, 20 jobs a page,
  and a page past the end has `null` instead of the job list. ds:1 = [jobs, None, total, page_size].
- Job fields by position: 0 id, 1 title, 3 [_, responsibilities html], 4 [_, qualifications html],
  7 company ("Google", "YouTube", "Waymo", ...), 9 [[location, [address], city, zip, state, country]],
  10 [_, about-the-job html], 12/13/14 [epoch seconds, nanos] (created / published / updated).
- Each page is ~1.4 MB of HTML, so full scans (~90 pages for the US) are kept rare.
"""

import json
import re
from collections.abc import Set
from urllib.parse import parse_qsl

import httpx

from app.pipeline.normalize import html_to_text
from app.sources import http_util
from app.sources.base import Adapter, NormalizedJob, from_epoch_ms

RESULTS = "https://www.google.com/about/careers/applications/jobs/results/"
MAX_PAGES = 200
MAX_PARTIAL_PAGES = 8
_DS1 = re.compile(r"AF_initDataCallback\(\{key: 'ds:1', hash: '[^']*', data:(.*?), sideChannel: \{\}\}\);</script>", re.S)


def parse_slug(slug: str) -> list[tuple[str, str]]:
    slug = (slug or "").strip()
    if slug.lower() in ("", "all"):
        return []
    return [(k, v) for k, v in parse_qsl(slug) if k not in ("page", "sort_by")]


def parse_page(html: str) -> tuple[list[list], int]:
    """(jobs, total) from a results page. Raises ValueError if the embedded data is missing."""
    m = _DS1.search(html)
    if not m:
        raise ValueError("Google careers: no ds:1 job data on the results page")
    data = json.loads(m.group(1))
    jobs = data[0] if data and isinstance(data[0], list) else []
    total = data[2] if len(data) > 2 and isinstance(data[2], int) else 0
    return jobs, total


def _at(row: list, i: int):
    return row[i] if len(row) > i else None


def _html(field) -> str:
    return html_to_text(field[1]) if isinstance(field, list) and len(field) > 1 and field[1] else ""


def _ts(field):
    return from_epoch_ms(field[0] * 1000) if isinstance(field, list) and field and field[0] else None


def _slugify(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def to_job(row: list) -> NormalizedJob:
    job_id, title = str(row[0]), (row[1] or "").strip()
    locations = [loc[0] for loc in (_at(row, 9) or []) if isinstance(loc, list) and loc and loc[0]]
    parts = []
    for heading, idx in (("", 10), ("Responsibilities", 3), ("Qualifications", 4)):
        text = _html(_at(row, idx))
        if text:
            parts.append(f"{heading}\n{text}" if heading else text)
    company = _at(row, 7) or ""
    return NormalizedJob(
        external_id=job_id,
        title=title,
        url=f"{RESULTS}{job_id}-{_slugify(title)}",
        locations=locations,
        remote=any("remote" in loc.lower() for loc in locations),
        description="\n\n".join(parts),
        department=company if company and company != "Google" else "",
        posted_at=_ts(_at(row, 13)) or _ts(_at(row, 12)),
    )


class GoogleAdapter(Adapter):
    ats = "google"
    min_interval_minutes = 20
    full_scan_every = 24  # full scans (~125 MB for the US list) about every 8 hours
    lane = "slow"

    async def fetch(
        self, client: httpx.AsyncClient, slug: str, *, full: bool = True, known_ids: Set[str] = frozenset()
    ) -> list[NormalizedJob]:
        filters = parse_slug(slug)
        rows: dict[str, list] = {}
        for page in range(1, (MAX_PAGES if full else MAX_PARTIAL_PAGES) + 1):
            resp = await http_util.get(client, RESULTS, params=filters + [("sort_by", "date"), ("page", str(page))])
            jobs, total = parse_page(resp.text)
            fresh = [r for r in jobs if r and str(r[0]) not in rows]
            if not fresh:
                break
            rows.update((str(r[0]), r) for r in fresh)
            if len(rows) >= total:
                break
            if not full and all(str(r[0]) in known_ids for r in fresh):
                break
        return [to_job(r) for r in rows.values()]
