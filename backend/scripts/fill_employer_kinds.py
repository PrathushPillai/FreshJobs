"""One-off (2026-11-12): fill Job.employer_kind (consultancy / staffing) on jobs saved before it existed.

New jobs get it in apply_flags. Reading every job's employer name would scan the whole 15 GB jobs table on the
cloud server's slow disk, so it works per source instead:
- a single employer's board: its jobs share the board's employer, so one UPDATE by company_id per labelled board;
- multi-employer sources (aggregators, JazzHR, Workable search...): their open jobs, read in id batches.
Only consultancies and staffing firms are written; everyone else stays NULL (= not one).

Run on the server: PYTHONPATH=. nice -n 10 .venv/bin/python scripts/fill_employer_kinds.py [--dry-run]
Safe beside the running backend: short transactions, retried while the database is locked.
"""

import sys
import time
from collections import Counter

from sqlalchemy import select, update
from sqlalchemy.exc import OperationalError

from app.db import SessionLocal
from app.h1b import job_employer_kind, sponsor_index
from app.models import Company, Job
from app.sources import ADAPTERS


def _write(fn, tries: int = 10):
    for attempt in range(tries):
        try:
            with SessionLocal() as session:
                result = fn(session)
                session.commit()
                return result
        except OperationalError as e:
            if "locked" not in str(e) or attempt == tries - 1:
                raise
            time.sleep(2 * (attempt + 1))


def main(dry_run: bool) -> None:
    with SessionLocal() as session:
        index = sponsor_index(session)
        sources = list(session.execute(select(Company.id, Company.name, Company.ats)))
    boards = [(cid, name, ats) for cid, name, ats in sources if not getattr(ADAPTERS.get(ats), "aggregator", False)]
    multi = [(cid, name, ats) for cid, name, ats in sources if getattr(ADAPTERS.get(ats), "aggregator", False)]
    counts: Counter = Counter()

    labelled = [(cid, kind) for cid, name, ats in boards if (kind := job_employer_kind(name or "", ats, index))]
    print(f"{len(boards)} single-employer boards, {len(labelled)} of them consultancies or staffing firms", flush=True)
    for cid, kind in labelled:
        n = 0 if dry_run else _write(lambda s: s.execute(
            update(Job).where(Job.company_id == cid, Job.closed_at.is_(None)).values(employer_kind=kind)
            .execution_options(synchronize_session=False)).rowcount)
        counts[kind] += n

    print(f"{len(multi)} multi-employer sources", flush=True)
    for cid, name, ats in multi:
        last = 0
        while True:
            with SessionLocal() as session:
                rows = list(session.execute(
                    select(Job.id, Job.company_name).where(Job.company_id == cid, Job.closed_at.is_(None), Job.id > last)
                    .order_by(Job.id).limit(2000)))
            if not rows:
                break
            last = rows[-1][0]
            by_kind: dict[str, list[int]] = {}
            for jid, employer in rows:
                if kind := job_employer_kind(employer or "", ats, index):
                    by_kind.setdefault(kind, []).append(jid)
            for kind, ids in by_kind.items():
                counts[kind] += len(ids)
                if not dry_run:
                    _write(lambda s, ids=ids, kind=kind: s.execute(
                        update(Job).where(Job.id.in_(ids)).values(employer_kind=kind)
                        .execution_options(synchronize_session=False)))
        print(f"  {name} ({ats}) done", flush=True)
    print(f"labelled {dict(counts)}{' (dry run: counts from boards are 0)' if dry_run else ''}")


if __name__ == "__main__":
    main("--dry-run" in sys.argv)
