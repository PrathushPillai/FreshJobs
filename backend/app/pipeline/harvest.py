"""Find new company boards in aggregator job links.

Most jobs on aggregators (SimplifyJobs, Google Jobs, The Muse...) link to the employer's own ATS
board, e.g. https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite/job/... . Each such link
tells us a board we can poll directly, so every job that employer posts reaches us minutes after
posting instead of whenever the aggregator picks it up. No network requests: only URL parsing.
"""

import logging
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Company
from app.pipeline.detect import detect_board
from app.sources import ADAPTERS

log = logging.getLogger(__name__)

# Big-company sites tracked by one hand-made source each: a link to them is never a new board.
_SINGLETONS = {"amazon", "microsoft", "google", "apple"}


def board_key(ats: str, slug: str) -> tuple[str, str]:
    return ats, slug.lower()


def name_from_slug(slug: str) -> str:
    """"acme-labs" -> "Acme Labs"; a Workday "tenant|wd5|Site" -> its tenant; a website URL -> its host."""
    base = slug.split("|")[0]
    if "://" in base:
        base = base.split("://", 1)[1].split("/")[0].removeprefix("www.").split(".")[0]
    return base.replace("-", " ").replace("_", " ").title()


def clean_name(name: str | None) -> str:
    name = (name or "").strip()
    return name if 0 < len(name) <= 80 else ""


def harvest(session: Session, links: Iterable[tuple[str, str]], limit: int) -> list[Company]:
    """Add up to `limit` untracked boards found in (company name, job URL) pairs. Doesn't commit.

    Boards linked by more jobs go first, so a capped run adds the most active employers.
    """
    if limit <= 0:
        return []
    tracked = {board_key(c.ats, c.slug) for c in session.scalars(select(Company))}
    found: dict[tuple[str, str], tuple[str, str, str]] = {}  # key -> (ats, slug, company name)
    counts: dict[tuple[str, str], int] = {}  # key -> how many jobs link to it
    for name, url in links:
        hit = detect_board(url)
        if not hit or hit[0] in _SINGLETONS or hit[0] not in ADAPTERS or ADAPTERS[hit[0]].aggregator:
            continue
        key = board_key(*hit)
        if key in tracked:
            continue
        counts[key] = counts.get(key, 0) + 1
        found.setdefault(key, (hit[0], hit[1], clean_name(name) or name_from_slug(hit[1])))

    added = []
    for key in sorted(found, key=lambda k: -counts[k])[:limit]:
        ats, slug, name = found[key]
        company = Company(name=name, ats=ats, slug=slug)
        session.add(company)
        added.append(company)
        log.info("Harvested %s board for %s: %s", ats, name, slug)
    return added
