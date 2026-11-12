"""One-off (2026-11-12): re-read the places of jobs saved as "City, XX" in a US state when XX is also a country's
code and the city is that country's (filters.FOREIGN_CITIES): "Hyderabad, IN" had been saved as Indiana.

Run on the server: PYTHONPATH=. .venv/bin/python scripts/fix_foreign_places.py [--dry-run]. Safe beside the running backend:
small batches, each in its own short transaction, retried while the database is locked.
"""

import sys
import time

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import OperationalError

from app.db import SessionLocal
from app.models import Job, JobLocation
from app.pipeline.filters import FOREIGN_CITIES, NEVER_US, is_us_location
from app.pipeline.locations import parse_locations


def main(dry_run: bool) -> None:
    with SessionLocal() as session:
        conds = [and_(JobLocation.state == code, func.lower(JobLocation.city).in_(sorted(cities)))
                 for code, (_, cities) in FOREIGN_CITIES.items()]
        conds.append(func.lower(JobLocation.city).in_(sorted(NEVER_US)))  # "Bangalore, CA"
        ids = sorted({jid for (jid,) in session.execute(
            select(JobLocation.job_id).where(JobLocation.country == "United States", or_(*conds)))})
    print(f"{len(ids)} jobs to re-read", flush=True)
    changed = no_longer_us = 0
    for start in range(0, len(ids), 200):
        batch = ids[start:start + 200]
        for attempt in range(10):
            try:
                with SessionLocal() as session:
                    for job in session.scalars(select(Job).where(Job.id.in_(batch))):
                        places = parse_locations(job.location)
                        us = is_us_location(job.location, job.remote)
                        old = sorted((p.city or "", p.state or "", p.country or "") for p in job.places)
                        if old == sorted((p.city or "", p.state or "", p.country or "") for p in places) and us == job.us_eligible:
                            continue  # already right
                        no_longer_us += bool(job.us_eligible and not us)
                        changed += 1
                        if not dry_run:
                            job.places = [JobLocation(city=p.city, state=p.state, country=p.country) for p in places]
                            job.us_eligible = us
                    if not dry_run:
                        session.commit()
                break
            except OperationalError as e:
                if "locked" not in str(e) or attempt == 9:
                    raise
                time.sleep(2 * (attempt + 1))
    print(f"changed {changed} jobs; {no_longer_us} no longer count as US{' (dry run, nothing saved)' if dry_run else ''}")


if __name__ == "__main__":
    main("--dry-run" in sys.argv)
