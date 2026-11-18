"""Add the Phenom careers sites that `scripts/find_phenom_sites.py find` found, a few at a time, by itself.

Each new site's first scan saves thousands of jobs in the slow lane, so they go in batches: the next batch is added
only when every Phenom site already tracked has finished a clean check (at least one poll, no failure streak) and
the slow lane hasn't needed a restart in the last 12 hours. Until 2026-11-17 this was done by hand after looking at
each batch; it runs with the hourly upkeep now (`upkeep.run`).

    python -m app.pipeline.phenom_rollout [--max N] [--dry-run]     # one step by hand
"""

import argparse
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from app.models import Company

log = logging.getLogger(__name__)

BATCH = 5
MIN_SHARE = 0.1  # a site with under a tenth of the employer's Workday jobs is probably another company's


def results_file() -> Path:
    from app.matching import _models_dir

    return Path(_models_dir()).parent / "phenom_sites.json"


def candidates(results: dict, tracked: set[str]) -> list[tuple[str, dict]]:
    """Usable sites not tracked yet, most US jobs first. careers.circle.com (39 jobs) isn't Circle K's (6,106 on
    Workday), hence the share test."""
    usable = [(name, v) for name, v in results.items()
              if v.get("us_jobs") and v.get("slug") and v["slug"].lower() not in tracked
              and v["us_jobs"] >= MIN_SHARE * (v.get("workday_jobs") or 0)]
    return sorted(usable, key=lambda nv: -nv[1]["us_jobs"])


def waiting_on(session) -> list[str]:
    """Why the next batch can't go in yet (empty: it can)."""
    from app.lanes import recent_restarts

    out = []
    for name, polls, streak in session.execute(
        select(Company.name, Company.poll_count, Company.fail_streak).where(Company.ats == "phenom",
                                                                            Company.enabled.is_(True))
    ):
        if not polls:
            out.append(f"{name} not checked yet")
        elif streak:
            out.append(f"{name} failing ({streak} in a row)")
    if any(e["lane"] == "slow" for e in recent_restarts(datetime.now(timezone.utc))):
        out.append("the slow lane was restarted in the last 12 hours")
    return out


def step(session, max_add: int = BATCH, dry_run: bool = False, force: bool = False) -> dict:
    path = results_file()
    if not path.exists():
        return {"added": [], "left": 0, "waiting_on": ["no phenom_sites.json (run find_phenom_sites.py find)"]}
    results = json.loads(path.read_text())
    tracked = {s.lower() for (s,) in session.execute(select(Company.slug).where(Company.ats == "phenom"))}
    todo = candidates(results, tracked)
    blocked = [] if force else waiting_on(session)
    if not todo or blocked:
        return {"added": [], "left": len(todo), "waiting_on": blocked}
    batch = todo[:max_add]
    for name, v in batch:
        log.info("%s Phenom site for %s: %s (%s US jobs)", "would add" if dry_run else "adding", name, v["slug"],
                 v["us_jobs"])
        if not dry_run:
            session.add(Company(name=name, ats="phenom", slug=v["slug"], enabled=True))
    if not dry_run:
        session.commit()
    return {"added": [name for name, _ in batch], "left": len(todo) - len(batch), "waiting_on": []}


def main() -> None:
    from app.db import SessionLocal

    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--max", type=int, default=BATCH)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="add even if the last batch hasn't loaded cleanly")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    with SessionLocal() as session:
        print(step(session, args.max, args.dry_run, args.force))


if __name__ == "__main__":
    main()
