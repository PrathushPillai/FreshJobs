"""Recompute the visa fields of every job the AI visa reader has read, with today's rules and today's guard in
visa_reader.flags_from. Run after changing either: a verdict already applied keeps its old effect otherwise
(2026-11-16: ASML and INSPYR got "No sponsorship" from answers the new guard rejects).

    PYTHONPATH=. .venv/bin/python scripts/reapply_visa_reads.py [--dry-run]
"""

import sys
import time

from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app.db import SessionLocal
from app.models import Job, VisaRead
from app.pipeline.visa_reader import overlay
from app.pipeline.visa_signals import derive

FIELDS = ("no_sponsor", "sponsors_text", "student_visa")


def main(dry_run: bool) -> None:
    with SessionLocal() as session:
        reads = session.execute(select(VisaRead.job_id, VisaRead.verdict).where(VisaRead.verdict.is_not(None))).all()
    changed = 0
    for job_id, verdict in reads:
        for attempt in range(10):
            try:
                with SessionLocal() as session:
                    job = session.get(Job, job_id)
                    if job is None:
                        break
                    rules = derive(job.description or "", job.seniority or "")
                    new = overlay({k: rules[k] for k in FIELDS}, verdict)
                    old = {k: getattr(job, k) for k in FIELDS}
                    if {k: new[k] or None for k in FIELDS} != {k: old[k] or None for k in FIELDS}:
                        changed += 1
                        print(f"job {job_id} ({job.company_name}): {old} -> {new}")
                        if not dry_run:
                            for k in FIELDS:
                                setattr(job, k, new[k])
                            read = session.get(VisaRead, job_id)
                            read.changed = any((new[k] or None) != (rules[k] or None) for k in FIELDS)
                            session.commit()
                break
            except OperationalError as e:
                if "locked" not in str(e) or attempt == 9:
                    raise
                time.sleep(3 * (attempt + 1))
    print(f"{len(reads)} read jobs checked, {changed} changed{' (dry run)' if dry_run else ''}")


if __name__ == "__main__":
    main("--dry-run" in sys.argv)
