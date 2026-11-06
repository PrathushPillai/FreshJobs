"""Company boards from community-maintained GitHub job lists, refreshed every `list_sync_hours`.

    python -m app.pipeline.startup_lists      # sync now

Two kinds of list (all checked Nov 2026; robots.txt and licences allow reading them):

- Board lists (BOARD_LISTS): datasets of companies with their ATS and board slug, imported as sources
  directly. zshah101's internship tracker (MIT licence) keeps ~5,300 boards, ~1,900 of them Workday.
- Job tables (JOB_TABLES): READMEs listing fresh new-grad and AI jobs with direct apply links to the
  company's ATS. The links go through harvest(), so every employer they mention becomes a tracked board;
  the jobs themselves then come from the boards, with full descriptions.

Left out on purpose: zapply's lists (their terms forbid feeding listings into other job feeds), Jobright's
and vanshb03's (their links go through redirect pages that hide the board, or that robots.txt blocks).
"""

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Company
from app.pipeline.harvest import board_key, clean_name, harvest
from app.sources import ADAPTERS

log = logging.getLogger(__name__)

RAW = "https://raw.githubusercontent.com"
BOARD_LISTS = [f"{RAW}/zshah101/Automated-List-Of-Summer-2027-and-Fall-2026-Tech-Internships/main/data/companies.json"]
JOB_TABLES = [
    f"{RAW}/speedyapply/2027-AI-College-Jobs/main/{f}" for f in ("NEW_GRAD_USA.md", "README.md")
] + [f"{RAW}/speedyapply/2027-SWE-College-Jobs/main/{f}" for f in ("NEW_GRAD_USA.md", "README.md")]
_HREF = re.compile(r"""href=["'](https?://[^"']+)["']|\]\((https?://[^)\s]+)\)""")
# Singletons tracked once by their own adapters (one source covers the whole company).
_SINGLETONS = {"amazon", "microsoft", "google", "apple"}

_last_sync: datetime | None = None


def board_from_row(row: dict) -> tuple[str, str] | None:
    """(ats, slug) in our format for one row of a board list, or None if we can't poll it."""
    ats, slug = (row.get("ats") or "").lower(), (row.get("slug") or "").strip()
    if not slug or ats in _SINGLETONS:
        return None
    if ats == "workday":
        return ("workday", f"{slug}|{row['wd']}|{row['site']}") if row.get("wd") and row.get("site") else None
    if ats == "oracle":
        return ("oracle_hcm", f"{row['host']}|{row['site']}") if row.get("host") and row.get("site") else None
    return (ats, slug) if ats in ADAPTERS and not ADAPTERS[ats].aggregator else None


def links_in(markdown: str) -> list[str]:
    return [a or b for a, b in _HREF.findall(markdown)]


async def sync_lists(session: Session, client: httpx.AsyncClient) -> list[Company]:
    """Add every untracked board from the lists. Commits."""
    tracked = {board_key(c.ats, c.slug) for c in session.scalars(select(Company))}
    added: list[Company] = []
    for url in BOARD_LISTS:
        resp = await client.get(url)
        resp.raise_for_status()
        for row in resp.json():
            board = board_from_row(row)
            if board and board_key(*board) not in tracked:
                tracked.add(board_key(*board))
                company = Company(name=clean_name(row.get("name")) or board[1], ats=board[0], slug=board[1])
                session.add(company)
                added.append(company)
    pages = await asyncio.gather(*(client.get(u) for u in JOB_TABLES), return_exceptions=True)
    links = [("", link) for page in pages if isinstance(page, httpx.Response) and page.status_code == 200
             for link in links_in(page.text)]
    added += harvest(session, links, limit=5_000)
    session.commit()
    log.info("GitHub job lists: %d new boards (%d job links read)", len(added), len(links))
    return added


async def maybe_sync_lists(session: Session, client: httpx.AsyncClient) -> list[Company]:
    """sync_lists() if it hasn't run in this process for `list_sync_hours` (0 = never). Errors are logged."""
    global _last_sync
    now = datetime.now(timezone.utc)
    if settings.list_sync_hours <= 0 or (_last_sync and now - _last_sync < timedelta(hours=settings.list_sync_hours)):
        return []
    _last_sync = now
    try:
        return await sync_lists(session, client)
    except (httpx.HTTPError, ValueError, KeyError) as e:
        session.rollback()
        log.warning("GitHub job list sync failed: %s", e)
        return []


def main() -> None:
    from app.db import SessionLocal, init_db
    from app.pipeline.poller import USER_AGENT

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    init_db()

    async def run() -> int:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
            with SessionLocal() as session:
                return len(await sync_lists(session, client))

    print(f"Added {asyncio.run(run())} new boards")


if __name__ == "__main__":
    main()
