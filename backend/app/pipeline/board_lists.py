"""Job boards from public lists of ATS board slugs, each checked live before it's added.

    python -m app.pipeline.bulk_discovery board-lists [--dry-run] [--ats greenhouse lever ...] [--limit N]

Researched 2026-11-12 (both MIT-licensed, raw GitHub files):
- Riley-Dorrington/job-board-aggregator `data/{ats}_companies.json`: JSON arrays of slugs, refreshed daily by its
  own scraper: Greenhouse ~8,300, Lever ~4,400, Ashby ~3,200, Workday ~11,900 (`tenant|wdN|site`, our format),
  BambooHR ~11,300, iCIMS ~10,100 (portal subdomains such as `careers-acme`). Some slugs are junk.
- veresk06/open-ats-feed `actor/data/companies.json` (as of 2026-09): slugs found in Common Crawl and checked
  against each vendor's API, with a `live` list per provider: Greenhouse, Ashby, Lever, Breezy, Recruitee
  (and Teamtailor, which we have no adapter for).

Against our sources on 2026-11-12 that was ~45,000 untracked slugs, too many to add blindly: dead or non-US
boards would cost polls. So each is fetched once (`vet`), with the ATS's own adapter where one list request
gives every job's place (Greenhouse, Lever, Ashby, BambooHR, Breezy, Recruitee, iCIMS: passed `ALL_KNOWN`, so
no per-job detail request is made), and with a single 20-job list call for Workday. A board is added only if
at least one job is in the US (`filters.is_us_location`). Each verdict is kept in `probe_cache`
(`bl:{ats}:{slug}`, `found` = "us:N" or "no"), so later runs skip slugs checked in the last `RECHECK_DAYS`.
"""

import asyncio
import logging
import re
from collections.abc import Set
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Company, ProbeCache
from app.pipeline.filters import is_us_location
from app.pipeline.harvest import board_key, name_from_slug

log = logging.getLogger(__name__)

RAW = "https://raw.githubusercontent.com"
RILEY = f"{RAW}/Riley-Dorrington/job-board-aggregator/main/data/" + "{ats}_companies.json"
RILEY_ATS = ("greenhouse", "lever", "ashby", "workday", "bamboohr", "icims")
OPEN_ATS = f"{RAW}/veresk06/open-ats-feed/main/actor/data/companies.json"
OPEN_ATS_PROVIDERS = ("greenhouse", "ashby", "lever", "breezy", "recruitee")
SUPPORTED = ("greenhouse", "lever", "ashby", "bamboohr", "breezy", "recruitee", "icims", "workday")
CONCURRENCY = {"greenhouse": 20, "lever": 20, "ashby": 20}  # others: DEFAULT_CONCURRENCY (their own small servers)
DEFAULT_CONCURRENCY = 6
RECHECK_DAYS = 30
_SLUG_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._|-]{1,120}$")


class _AllKnown(Set):
    """known_ids that contains every id: adapters then skip per-job detail requests."""

    def __contains__(self, item) -> bool:
        return True

    def __iter__(self):
        return iter(())

    def __len__(self) -> int:
        return 1  # truthy: Greenhouse's "first scan" (content=true) is for an empty known_ids


ALL_KNOWN = _AllKnown()


def normalize_slug(ats: str, slug: str) -> str | None:
    """The list's slug in our form, or None for one we can't use."""
    slug = (slug or "").strip().strip("/")
    if not _SLUG_OK.match(slug):
        return None
    if ats == "icims":
        slug = slug.lower().removesuffix(".icims.com")
        if slug.startswith("careers-"):
            return slug.removeprefix("careers-")  # our slug: careers-{slug}.icims.com
        return f"{slug}.icims.com"  # another prefix (uscareers-acme...): the full host works too
    if ats == "workday":
        parts = slug.split("|")
        if len(parts) != 3 or not all(parts) or "." in parts[2]:
            return None  # a site named after a file (robots.txt) is no career site
        return slug
    if "|" in slug:
        return None
    return slug


def parse_lists(riley: dict[str, list], open_ats: dict) -> list[tuple[str, str]]:
    """(ats, slug) from both lists, in our slug form, without duplicates."""
    out: dict[tuple[str, str], tuple[str, str]] = {}
    for ats, slugs in riley.items():
        for s in slugs or []:
            if isinstance(s, str) and (n := normalize_slug(ats, s)):
                out.setdefault(board_key(ats, n), (ats, n))
    for ats, data in ((open_ats or {}).get("providers") or {}).items():
        if ats not in OPEN_ATS_PROVIDERS:
            continue
        for row in data.get("live") or []:
            s = row[0] if isinstance(row, list) and row else row
            if isinstance(s, str) and (n := normalize_slug(ats, s)):
                out.setdefault(board_key(ats, n), (ats, n))
    return list(out.values())


async def fetch_lists(client: httpx.AsyncClient) -> list[tuple[str, str]]:
    riley: dict[str, list] = {}
    for ats in RILEY_ATS:
        try:
            r = await client.get(RILEY.format(ats=ats))
            riley[ats] = r.json() if r.status_code == 200 else []
        except (httpx.HTTPError, ValueError):
            riley[ats] = []
        log.info("Riley-Dorrington %s: %d slugs", ats, len(riley[ats]))
    try:
        r = await client.get(OPEN_ATS)
        open_ats = r.json() if r.status_code == 200 else {}
    except (httpx.HTTPError, ValueError):
        open_ats = {}
    return parse_lists(riley, open_ats)


async def _workday_jobs(client: httpx.AsyncClient, slug: str) -> list[tuple[str, bool]]:
    from app.sources.workday import board_host

    tenant, wd, site = slug.split("|")
    r = await client.post(f"{board_host(tenant, wd)}/wday/cxs/{tenant}/{site}/jobs",
                          json={"limit": 20, "offset": 0, "searchText": "", "appliedFacets": {}})
    r.raise_for_status()
    out = []
    for p in r.json().get("jobPostings") or []:
        text = p.get("locationsText") or ""
        if not re.fullmatch(r"\d+ Locations", text):  # a count, not a place
            out.append((text, "remote" in (p.get("remoteType") or text).lower()))
    return out


async def vet(client: httpx.AsyncClient, ats: str, slug: str) -> tuple[int, int] | None:
    """(jobs seen, US jobs) on the board, or None if it can't be read (dead, wrong slug, blocked)."""
    from app.sources import ADAPTERS

    try:
        if ats == "workday":
            places = await _workday_jobs(client, slug)
        else:
            jobs = await ADAPTERS[ats].fetch(client, slug, full=True, known_ids=ALL_KNOWN, first_scan=False)
            places = [(" | ".join(j.locations), j.remote) for j in jobs]
    except Exception:  # noqa: BLE001 - any failure means "not a board we can use"
        return None
    return len(places), sum(1 for text, remote in places if is_us_location(text, remote))


def _us_jobs(found: str | None) -> int:
    try:
        return int((found or "").removeprefix("us:"))
    except ValueError:
        return 0


async def from_board_lists(client: httpx.AsyncClient, only: list[str] | None = None, dry_run: bool = False,
                           limit: int | None = None, max_add: int | None = None) -> int:
    """Check every untracked slug on the lists and add the boards with US jobs.

    `max_add` adds only that many (the most US jobs first): a new board's first scan saves its whole list, and
    2,459 at once (2026-11-13) left the fast lane saving first scans for about an hour while existing boards
    waited. The rest keep their verdicts, so the next run adds the next batch without checking again."""
    candidates = await fetch_lists(client)
    since = datetime.now(timezone.utc) - timedelta(days=RECHECK_DAYS)
    with SessionLocal() as session:
        tracked = {board_key(c.ats, c.slug) for c in session.scalars(select(Company))}
        recent = {k: found for k, at, found in session.execute(
            select(ProbeCache.company_key, ProbeCache.probed_at, ProbeCache.found)
            .where(ProbeCache.company_key.like("bl:%"))) if at and at >= since}
    untracked = [(a, s) for a, s in candidates
                 if a in SUPPORTED and (not only or a in only) and board_key(a, s) not in tracked]
    # Checked recently (a dry run, or a run cut short): its verdict stands, and a board with US jobs is added.
    cached = [(a, s, _us_jobs(recent.get(f"bl:{a}:{s.lower()}"))) for a, s in untracked
              if (recent.get(f"bl:{a}:{s.lower()}") or "").startswith("us:")]
    todo = [(a, s) for a, s in untracked if f"bl:{a}:{s.lower()}" not in recent]
    if limit:
        todo = todo[:limit]
    log.info("board lists: %d slugs, %d untracked and not checked in %d days", len(candidates), len(todo), RECHECK_DAYS)

    sems = {a: asyncio.Semaphore(CONCURRENCY.get(a, DEFAULT_CONCURRENCY)) for a in SUPPORTED}
    verdicts: list[tuple[str, str, tuple[int, int] | None]] = []
    done = 0

    async def one(ats: str, slug: str) -> None:
        nonlocal done
        async with sems[ats]:
            result = await vet(client, ats, slug)
        verdicts.append((ats, slug, result))
        done += 1
        if done % 1000 == 0:
            log.info("board lists: %d / %d checked, %d with US jobs so far", done, len(todo),
                     sum(1 for *_, r in verdicts if r and r[1]))

    await asyncio.gather(*(one(a, s) for a, s in todo))
    good = sorted([(a, s, r[1]) for a, s, r in verdicts if r and r[1] > 0] + cached, key=lambda g: -g[2])
    waiting = 0
    if max_add is not None and len(good) > max_add:
        good, waiting = good[:max_add], len(good) - max_add
    by_ats: dict[str, list[int]] = {}
    for a, s, r in verdicts:
        row = by_ats.setdefault(a, [0, 0, 0, 0])  # checked, readable, with US jobs, US jobs
        row[0] += 1
        if r:
            row[1] += 1
            if r[1]:
                row[2] += 1
                row[3] += r[1]
    for a, (n, ok, us, jobs) in sorted(by_ats.items()):
        log.info("  %-12s checked %6d  readable %6d  with US jobs %6d  (%d US jobs)", a, n, ok, us, jobs)

    now = datetime.now(timezone.utc)
    with SessionLocal() as session:
        for a, s, r in verdicts:
            session.merge(ProbeCache(company_key=f"bl:{a}:{s.lower()}"[:200], probed_at=now,
                                     found=f"us:{r[1]}" if r and r[1] else "no"))
        if not dry_run:
            for a, s, _ in good:
                session.add(Company(name=name_from_slug(s.split("|")[0].removesuffix(".icims.com")), ats=a, slug=s,
                                    enabled=True))
        session.commit()
    log.info("board lists: %d boards with US jobs (%d from earlier checks)%s%s", len(good), len(cached),
             " (dry run: not added)" if dry_run else " added", f"; {waiting} more wait for the next run" if waiting else "")
    return len(good)
