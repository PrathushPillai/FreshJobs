"""Fill the F-1 student and E-Verify signals (Job.student_visa, Job.everify_text) for jobs saved before they existed.

New and enriched jobs get them from apply_flags. This reads every description once, in id batches,
and writes only the rows whose signals change, so it can run while the backend is polling:

    python -m app.pipeline.visa_signals
"""

import logging
import time

from sqlalchemy import or_, select, update

from app.db import SessionLocal, init_db
from app.models import Job
from app.pipeline.sponsorship import mentions_everify, student_visa_signal

log = logging.getLogger(__name__)


def refresh(batch: int = 5000) -> dict[str, int]:
    counts = {"read": 0, "welcome": 0, "excluded": 0, "everify": 0, "newly_hidden": 0}
    last_id = 0
    started = time.time()
    while True:
        with SessionLocal() as session:
            # A cheap LIKE (case-insensitive in SQLite) skips the ~97% of descriptions that can't match.
            hints = or_(*(Job.description.like(f"%{w}%") for w in ("opt", "cpt", "f-1", "f1", "practical training", "verify")))
            ids = list(session.scalars(select(Job.id).where(Job.id > last_id).order_by(Job.id).limit(batch)))
            if not ids:
                break
            last_id = ids[-1]
            counts["read"] += len(ids)
            rows = session.execute(
                select(Job.id, Job.description, Job.no_sponsor, Job.student_visa, Job.everify_text)
                .where(Job.id.in_(ids), Job.described.is_(True), hints)
            ).all()
            for job_id, description, no_sponsor, student, everify in rows:
                new_student = student_visa_signal(description)
                new_everify = mentions_everify(description)
                hide = no_sponsor or new_student == "excluded"
                counts["welcome"] += new_student == "welcome"
                counts["excluded"] += new_student == "excluded"
                counts["everify"] += new_everify
                if (new_student, new_everify, hide) != (student, bool(everify), no_sponsor):
                    counts["newly_hidden"] += hide and not no_sponsor
                    session.execute(update(Job).where(Job.id == job_id).values(
                        student_visa=new_student, everify_text=new_everify, no_sponsor=hide))
            session.commit()
        if counts["read"] % 100_000 < batch:
            log.info("read %d jobs (%.0fs): %s", counts["read"], time.time() - started, counts)
    return counts


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    init_db()
    print(refresh())
