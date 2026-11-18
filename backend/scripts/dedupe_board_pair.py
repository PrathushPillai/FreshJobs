"""Mark one board's open jobs as duplicates of another board's, for two sources of one employer found after both
were already saved (2026-11-18: Allied Universal's own site jobs.aus.com, tracked as "MSA Security", against its
iCIMS portal). The live check (`dedupe.find_original`) only compares jobs as they arrive, and only within one
employer name, so copies saved under a wrong name stay in the feed until a pass like this one.

A copy matches a kept job with the same normalized title that's still open and is the same posting by
`dedupe.same_posting` (the same apply link or the same city), the rule the live check uses for cross-source
copies. Each match gets is_repost, repost_of_id and a "duplicate" event, as process_company writes them.

    PYTHONPATH=. .venv/bin/python scripts/dedupe_board_pair.py --keep 26809 --copy 38458 [--dry-run]
"""

import argparse
from datetime import datetime, timezone

from sqlalchemy import text

from app.db import SessionLocal
from app.models import JobEvent
from app.pipeline.dedupe import same_posting
from app.pipeline.normalize import normalize_title


def open_jobs(session, company_id: int) -> list:
    return session.execute(text(
        "SELECT id, title, url, location FROM jobs INDEXED BY ix_jobs_company_sync "
        "WHERE company_id = :c AND closed_at IS NULL AND is_repost = 0"), {"c": company_id}).all()


def pairs(kept: list, copies: list) -> list[tuple[int, int]]:
    """(copy id, kept id) for every copy that matches a kept job."""
    by_title: dict[str, list] = {}
    for k in kept:
        by_title.setdefault(normalize_title(k.title or ""), []).append(k)
    out = []
    for c in copies:
        match = next((k for k in by_title.get(normalize_title(c.title or ""), ())
                      if same_posting(c.url, c.location, k.url, k.location)), None)
        if match is not None:
            out.append((c.id, match.id))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--keep", type=int, required=True, help="the board whose jobs stay in the feed")
    ap.add_argument("--copy", type=int, required=True, help="the board whose matching jobs become duplicates")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    with SessionLocal() as session:
        kept_name = session.execute(text("SELECT name FROM companies WHERE id = :c"), {"c": args.keep}).scalar_one()
        kept, copies = open_jobs(session, args.keep), open_jobs(session, args.copy)
        found = pairs(kept, copies)
        print(f"{len(found):,} of {len(copies):,} open jobs match one of {len(kept):,} on {kept_name}")
        if args.dry_run:
            for copy_id, kept_id in found[:5]:
                print("  e.g. job", copy_id, "->", kept_id)
            return
        now = datetime.now(timezone.utc)
        for i in range(0, len(found), 500):
            chunk = found[i:i + 500]
            for copy_id, kept_id in chunk:
                session.execute(text("UPDATE jobs SET is_repost = 1, repost_of_id = :k WHERE id = :c"),
                                {"k": kept_id, "c": copy_id})
                session.add(JobEvent(job_id=copy_id, kind="duplicate", at=now,
                                     detail=f"also listed via {kept_name} (job {kept_id})"))
            session.commit()
        print(f"marked {len(found):,} duplicates")


if __name__ == "__main__":
    main()
