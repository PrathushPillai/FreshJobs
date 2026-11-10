"""Re-read saved jobs with the current visa and experience rules (no_sponsor, student_visa, everify_text,
sponsors_text, min_years, exp_level). New and enriched jobs get them from apply_flags; this is for jobs saved
before a rule changed.

It reads each open, described job's description once, in id batches (For you jobs first, since those are the
ones shown most), and writes only the rows whose answers change, so it can run while the backend polls:

    python -m app.pipeline.visa_signals            # open jobs: For you first, then the rest
    python -m app.pipeline.visa_signals --for-you  # only For you jobs (about an hour)
    python -m app.pipeline.visa_signals --others --resume   # only the rest, continuing where the last run stopped

Progress (the pass and the last job id written) is saved to visa_signals.json next to the database after every
batch, so --resume continues after a crash, a restart or the laptop sleeping.
"""

import argparse
import json
import logging
import time
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.exc import OperationalError

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


def _write(updates: list[tuple[int, dict]], attempts: int = 20) -> None:
    for attempt in range(attempts):
        try:
            with SessionLocal() as session:
                for job_id, values in updates:
                    session.execute(update(Job).where(Job.id == job_id).values(**values))
                session.commit()
            return
        except OperationalError as e:
            if "locked" not in str(e.orig) or attempt == attempts - 1:
                raise
            log.info("Database busy, retrying in %ds", 5 * (attempt + 1))
            time.sleep(5 * (attempt + 1))


def _progress_path() -> Path:
    from app.matching import _models_dir

    return Path(_models_dir()).parent / "visa_signals.json"  # next to the database, like autodiscover.json


def _load_progress() -> dict:
    try:
        return json.loads(_progress_path().read_text())
    except (OSError, ValueError):
        return {}


def _save_progress(data: dict) -> None:
    try:
        _progress_path().write_text(json.dumps(data))
    except OSError:
        log.warning("couldn't save progress", exc_info=True)


def refresh(for_you_only: bool = False, others_only: bool = False, resume: bool = False,
            batch: int = 2000) -> dict[str, int]:
    counts = {"read": 0, "changed": 0, "newly_no_sponsor": 0, "no_longer_no_sponsor": 0, "sponsors_text": 0, "years_changed": 0}
    started = time.time()
    passes = [True] if for_you_only else [False] if others_only else [True, False]
    saved = _load_progress() if resume else {}
    if saved.get("counts"):
        counts = {**counts, **saved["counts"]}
    for role_match in passes:
        key = "for_you" if role_match else "others"
        last_id = saved.get(key, 0)
        if last_id == -1:  # this pass finished in an earlier run
            continue
        updates: list[tuple[int, dict]] = []
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
                        updates.append((row[0], new))
            # Written in a short transaction of its own, retried while a poll holds the write lock: one failed
            # commit used to end the whole run ("database is locked", 2026-11-10).
            _write(updates)
            updates = []
            saved[key] = last_id
            _save_progress({**saved, "counts": counts, "updated": time.strftime("%Y-%m-%d %H:%M:%S")})
            if counts["read"] and counts["read"] % 50_000 < batch:
                log.info("%s jobs: read %d (%.0fs): %s", "For you" if role_match else "other", counts["read"],
                         time.time() - started, counts)
        saved[key] = -1
        _save_progress({**saved, "counts": counts, "updated": time.strftime("%Y-%m-%d %H:%M:%S")})
    return counts


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--for-you", action="store_true", help="only the For you jobs")
    ap.add_argument("--others", action="store_true", help="only the jobs outside For you")
    ap.add_argument("--resume", action="store_true", help="continue from the last run's saved position")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    init_db()
    print(refresh(for_you_only=args.for_you, others_only=args.others, resume=args.resume))
