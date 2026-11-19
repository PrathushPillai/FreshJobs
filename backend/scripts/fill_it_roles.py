"""Label every open job IT or not (Job.it_role, pipeline/tech_roles.classify_job), once, after the column is added.

Jobs not labelled yet (NULL) are shown, so the feed is never wrong while this runs; non-IT jobs disappear from it
as their labels land. Titles come from the covering index ix_jobs_company_titles; only titles that look non-IT need
their description read (the description second look), in id batches. Each batch is its own short transaction,
retried on "database is locked", so it's safe beside the running backend. Progress is saved (fill_it_roles.json next
to the database), so --resume continues a cut-off run.

    PYTHONPATH=. nice -n 15 .venv/bin/python scripts/fill_it_roles.py [--resume] [--closed]
"""

import argparse
import json
import time
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.db import SessionLocal
from app.matching import _models_dir
from app.pipeline.tech_roles import classify, classify_job

BATCH = 500
PROGRESS = Path(_models_dir()).parent / "fill_it_roles.json"


def _write(updates: list[tuple[int, str]]) -> None:
    for attempt in range(8):
        try:
            with SessionLocal() as s:
                for label in {lab for _, lab in updates}:
                    ids = [i for i, lab in updates if lab == label]
                    s.execute(text(f"UPDATE jobs SET it_role = :lab WHERE it_role IS NOT 'kept' AND id IN "
                                   f"({','.join(map(str, ids))})"), {"lab": label})
                s.commit()
            return
        except OperationalError as e:
            if "locked" not in str(e) or attempt == 7:
                raise
            time.sleep(5 * (attempt + 1))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--closed", action="store_true", help="closed jobs too (not shown in the feed)")
    args = ap.parse_args()
    done_up_to = json.loads(PROGRESS.read_text()).get("last_id", 0) if args.resume and PROGRESS.exists() else 0
    t0 = time.time()
    with SessionLocal() as s:
        where = "" if args.closed else "WHERE closed_at IS NULL"
        rows = sorted(s.execute(text(f"SELECT id, title FROM jobs INDEXED BY ix_jobs_company_titles {where}")).all())
    rows = [r for r in rows if r[0] > done_up_to]
    print(f"{len(rows):,} jobs to label ({time.time() - t0:.0f} s)", flush=True)
    counts = {"tech": 0, "edge": 0, "other": 0, "rescued": 0}
    for start in range(0, len(rows), BATCH):
        chunk = rows[start:start + BATCH]
        labels = {i: classify(t or "") for i, t in chunk}
        maybe_other = [i for i, lab in labels.items() if lab == "other"]
        if maybe_other:
            with SessionLocal() as s:
                for i, title, desc in s.execute(text(
                        f"SELECT id, title, description FROM jobs WHERE id IN ({','.join(map(str, maybe_other))})")):
                    lab = classify_job(title or "", desc or "")
                    if lab != "other":
                        counts["rescued"] += 1
                    labels[i] = lab
        _write(list(labels.items()))
        for lab in labels.values():
            counts[lab] = counts.get(lab, 0) + 1
        PROGRESS.write_text(json.dumps({"last_id": chunk[-1][0], **counts}))
        if (start // BATCH) % 100 == 0:
            print(f"{start + len(chunk):,}/{len(rows):,} {counts} ({time.time() - t0:.0f} s)", flush=True)
    print(f"done: {counts} in {time.time() - t0:.0f} s", flush=True)


if __name__ == "__main__":
    main()
