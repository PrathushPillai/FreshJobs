"""Re-read saved jobs with the current visa and experience rules (no_sponsor, student_visa, everify_text,
sponsors_text, min_years, exp_level). New and enriched jobs get them from apply_flags; this is for jobs saved
before a rule changed.

It reads each open, described job's description once, in id batches (For you jobs first, since those are the
ones shown most), and writes only the rows whose answers change, so it can run while the backend polls:

    python -m app.pipeline.visa_signals            # open jobs: For you first, then the rest
    python -m app.pipeline.visa_signals --for-you  # only For you jobs (minutes)
"""

import argparse
import logging
import time

from sqlalchemy import select, update

from app.db import SessionLocal, init_db
from app.models import Job
from app.pipeline.normalize import experience_level, experience_years
from app.pipeline.sponsorship import find_no_sponsor_phrase, mentions_everify, says_sponsors, student_visa_signal

log = logging.getLogger(__name__)
FIELDS = ("no_sponsor", "student_visa", "everify_text", "sponsors_text", "min_years", "exp_level")


def derive(description: str, seniority: str) -> dict:
    """The rule-based fields of a job, exactly as apply_flags computes them."""
    student = student_visa_signal(description)
    no_sponsor = find_no_sponsor_phrase(description) is not None or student == "excluded"
    years = experience_years(description)
    return {
        "no_sponsor": no_sponsor, "student_visa": student, "everify_text": mentions_everify(description),
        "sponsors_text": not no_sponsor and says_sponsors(description),
        "min_years": years, "exp_level": experience_level(years, seniority or ""),
    }


def refresh(for_you_only: bool = False, batch: int = 2000) -> dict[str, int]:
    counts = {"read": 0, "changed": 0, "newly_no_sponsor": 0, "no_longer_no_sponsor": 0, "sponsors_text": 0, "years_changed": 0}
    started = time.time()
    passes = [True] if for_you_only else [True, False]
    for role_match in passes:
        last_id = 0
        while True:
            with SessionLocal() as session:
                ids = list(session.scalars(
                    select(Job.id).where(Job.id > last_id, Job.closed_at.is_(None), Job.role_match.is_(role_match))
                    .order_by(Job.id).limit(batch)))
                if not ids:
                    break
                last_id = ids[-1]
                rows = session.execute(
                    select(Job.id, Job.description, Job.seniority, *(getattr(Job, f) for f in FIELDS))
                    .where(Job.id.in_(ids), Job.described.is_(True))).all()
                for row in rows:
                    old = dict(zip(FIELDS, row[3:]))
                    new = derive(row[1], row[2])
                    counts["read"] += 1
                    counts["sponsors_text"] += new["sponsors_text"]
                    if any((new[f] or None) != (old[f] or None) for f in FIELDS):
                        counts["changed"] += 1
                        counts["newly_no_sponsor"] += new["no_sponsor"] and not old["no_sponsor"]
                        counts["no_longer_no_sponsor"] += old["no_sponsor"] and not new["no_sponsor"]
                        counts["years_changed"] += new["min_years"] != old["min_years"]
                        session.execute(update(Job).where(Job.id == row[0]).values(**new))
                session.commit()
            if counts["read"] and counts["read"] % 50_000 < batch:
                log.info("%s jobs: read %d (%.0fs): %s", "For you" if role_match else "other", counts["read"],
                         time.time() - started, counts)
    return counts


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--for-you", action="store_true", help="only the For you jobs")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    init_db()
    print(refresh(for_you_only=args.for_you))
