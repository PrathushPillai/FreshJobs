"""Track every Y Combinator startup that's hiring in the US or remotely.

The yc-oss project (github.com/yc-oss/api) republishes YC's public company directory daily, with an
`isHiring` flag. Each hiring company we don't track yet becomes a `yc` source (sources/yc.py), so its
"Work at a Startup" postings are polled directly. Runs from the fast lane once every `yc_sync_hours`.

CLI:  python -m app.pipeline.startups
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Company
from app.pipeline.normalize import company_key

log = logging.getLogger(__name__)

YC_HIRING = "https://yc-oss.github.io/api/companies/hiring.json"
US_REGIONS = {"United States of America", "America / Canada", "Remote", "Fully Remote", "Partly Remote"}

_last_sync: datetime | None = None


async def sync_yc(session: Session, client: httpx.AsyncClient) -> list[Company]:
    """Add hiring YC companies (US or remote) not tracked yet, on any source. Doesn't commit."""
    resp = await client.get(YC_HIRING)
    resp.raise_for_status()
    tracked_boards = {(c.ats, c.slug) for c in session.scalars(select(Company))}
    tracked_names = {company_key(c.name) for c in session.scalars(select(Company))}
    added = []
    for c in resp.json():
        if not isinstance(c, dict) or not c.get("isHiring") or not c.get("slug") or c.get("status") not in ("Active", "Public"):
            continue
        if not US_REGIONS & set(c.get("regions") or []):
            continue
        key = company_key(c.get("name") or "")
        # A startup already tracked through its own ATS board posts the same jobs there, minutes earlier.
        if ("yc", c["slug"]) in tracked_boards or key in tracked_names:
            continue
        company = Company(name=c.get("name") or c["slug"], ats="yc", slug=c["slug"])
        session.add(company)
        added.append(company)
        tracked_names.add(key)
    log.info("YC sync: %d hiring startups added", len(added))
    return added


async def maybe_sync_yc(session: Session, client: httpx.AsyncClient) -> list[Company]:
    """sync_yc() if it hasn't run in this process for `yc_sync_hours` (0 = never). Errors are logged."""
    global _last_sync
    now = datetime.now(timezone.utc)
    if settings.yc_sync_hours <= 0 or (_last_sync and now - _last_sync < timedelta(hours=settings.yc_sync_hours)):
        return []
    _last_sync = now
    try:
        return await sync_yc(session, client)
    except (httpx.HTTPError, ValueError) as e:
        log.warning("YC sync failed: %s", e)
        return []


def main() -> None:
    from app.db import SessionLocal, init_db

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    init_db()

    async def run():
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            with SessionLocal() as session:
                added = await sync_yc(session, client)
                session.commit()
        print(f"Added {len(added)} YC startups")

    asyncio.run(run())


if __name__ == "__main__":
    main()
