"""One-off (2026-11-15): re-check every open US job's places with the current rules.

filters.is_us_location used to judge a place by state-like codes, so "Madrid, MD, Spain", "Cork, CO, Ireland",
"Bangalore, KA, IN", "Remote - Colombia" and "Latin America" were in the US feed; "Remote in USA" was saved as
Indiana, and Walmart's "(USA) SC ROCK HILL 06236 SAM'S CLUB" had no place at all. This reads (id, location, remote)
of every open us_eligible job, and for the ones whose verdict changed, or whose place format was fixed, saves the
new verdict and places.

Run on the server: PYTHONPATH=. .venv/bin/python scripts/fix_us_places.py [--dry-run]. Safe beside the running
backend: small batches, each in its own short transaction, retried while the database is locked.
"""

import re
import sys
import time

from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError

from app.db import SessionLocal
from app.models import Job, JobLocation
from app.pipeline.filters import is_us_location
from app.pipeline.locations import parse_locations

REPARSE = re.compile(r"\b(in|within|across) (usa|us|united states)\b|^\(USA\) [A-Z]{2} |Ontario, (CA|California)\b", re.I)


def _write(fn) -> None:
    for attempt in range(10):
        try:
            with SessionLocal() as session:
                fn(session)
                session.commit()
            return
        except OperationalError as e:
            if "locked" not in str(e) or attempt == 9:
                raise
            time.sleep(2 * (attempt + 1))


def main(dry_run: bool) -> None:
    with SessionLocal() as session:
        top = session.execute(text("SELECT max(id) FROM jobs")).scalar() or 0
    abroad: list[int] = []
    reparse: list[int] = []
    examples: list[str] = []
    step = 20_000
    for lo in range(0, top + 1, step):
        with SessionLocal() as session:
            rows = session.execute(text(
                "SELECT id, location, remote FROM jobs WHERE id >= :lo AND id < :hi AND closed_at IS NULL "
                "AND us_eligible = 1"), {"lo": lo, "hi": lo + step}).all()
        for jid, location, remote in rows:
            if not is_us_location(location or "", bool(remote)):
                abroad.append(jid)
                if len(abroad) % 400 == 1:  # a sample spread over the whole table, not just its start
                    examples.append(location)
            elif location and REPARSE.search(location):
                reparse.append(jid)
        print(f"read up to id {lo + step:,}: {len(abroad):,} abroad, {len(reparse):,} to re-parse", flush=True)
    for e in examples:
        print("  abroad:", e)
    if dry_run:
        print("dry run: nothing saved")
        return
    for ids, us in ((abroad, False), (reparse, True)):
        for i in range(0, len(ids), 200):
            batch = ids[i:i + 200]

            def save(session, batch=batch, us=us):
                for job in session.scalars(select(Job).where(Job.id.in_(batch))):
                    job.us_eligible = us
                    job.places = [JobLocation(city=p.city, state=p.state, country=p.country)
                                  for p in parse_locations(job.location)]
            _write(save)
    print(f"saved: {len(abroad):,} jobs no longer count as US, {len(reparse):,} re-parsed")


if __name__ == "__main__":
    main("--dry-run" in sys.argv)
