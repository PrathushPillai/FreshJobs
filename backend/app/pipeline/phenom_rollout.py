"""Add the Phenom and Radancy careers sites that `scripts/find_phenom_sites.py find` found, a few at a time, by itself.

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


PLATFORMS = ("phenom", "radancy")  # careers-site platforms that carry big Workday employers' jobs


def results_file(platform: str = "phenom", review: bool = False) -> Path:
    """{platform}_sites.json next to the database (what the rollout adds from), or the *_review.json a find run
    writes for a person to look at first (`find_phenom_sites.py merge` moves it over)."""
    from app.matching import _models_dir

    return Path(_models_dir()).parent / f"{platform}_sites{'_review' if review else ''}.json"


def candidates(results: dict, tracked: set[str]) -> list[tuple[str, dict]]:
    """Usable sites not tracked yet, most US jobs first. careers.circle.com (39 jobs) isn't Circle K's (6,106 on
    Workday), hence the share test."""
    usable = [(name, v) for name, v in results.items()
              if v.get("us_jobs") and v.get("slug") and v["slug"].lower() not in tracked
              and v["us_jobs"] >= MIN_SHARE * (v.get("workday_jobs") or 0)]
    # One site can belong to two DOL names ("Abbott" and "ABBOTT LABORATORIES", "Mercy" and "Mercy Clinic
    # Springfield Communities"): each site once, under the name with the bigger Workday board. Adding both in one
    # batch broke the (ats, slug) unique key and failed every hourly run from 2026-11-18 17:33 UTC.
    usable.sort(key=lambda nv: (-nv[1]["us_jobs"], -(nv[1].get("workday_jobs") or 0)))
    seen: set[str] = set()
    out = []
    for name, v in usable:
        if v["slug"].lower() not in seen:
            seen.add(v["slug"].lower())
            out.append((name, v))
    return out


def waiting_on(session, platform: str = "phenom", results: dict | None = None) -> list[str]:
    """Why the next batch can't go in yet (empty: it can). Only the sites this rollout adds count: older boards of
    the platform (Radancy's, found by discovery since 2026-11-09) failing shouldn't hold the new ones."""
    from app.lanes import recent_restarts

    ours = {v["slug"].lower() for v in (results or {}).values() if v.get("slug")}
    out = []
    for name, slug, polls, streak in session.execute(
        select(Company.name, Company.slug, Company.poll_count, Company.fail_streak).where(
            Company.ats == platform, Company.enabled.is_(True))
    ):
        if results is not None and slug.lower() not in ours:
            continue
        if not polls:
            out.append(f"{name} not checked yet")
        elif streak:
            out.append(f"{name} failing ({streak} in a row)")
    if any(e["lane"] == "slow" for e in recent_restarts(datetime.now(timezone.utc))):
        out.append("the slow lane was restarted in the last 12 hours")
    return out


def step(session, max_add: int = BATCH, dry_run: bool = False, force: bool = False, platform: str = "phenom") -> dict:
    path = results_file(platform)
    if not path.exists():
        return {"added": [], "left": 0, "waiting_on": [f"no {path.name} (run find_phenom_sites.py find)"]}
    results = json.loads(path.read_text())
    tracked = {s.lower() for (s,) in session.execute(select(Company.slug).where(Company.ats == platform))}
    todo = candidates(results, tracked)
    blocked = [] if force else waiting_on(session, platform, results)
    if not todo or blocked:
        return {"added": [], "left": len(todo), "waiting_on": blocked}
    batch = todo[:max_add]
    for name, v in batch:
        log.info("%s %s site for %s: %s (%s US jobs)", "would add" if dry_run else "adding", platform, name,
                 v["slug"], v["us_jobs"])
        if not dry_run:
            session.add(Company(name=name, ats=platform, slug=v["slug"], enabled=True))
    if not dry_run:
        session.commit()
    return {"added": [name for name, _ in batch], "left": len(todo) - len(batch), "waiting_on": []}


def main() -> None:
    from app.db import SessionLocal

    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--max", type=int, default=BATCH)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="add even if the last batch hasn't loaded cleanly")
    ap.add_argument("--platform", choices=PLATFORMS, default="phenom")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    with SessionLocal() as session:
        print(step(session, args.max, args.dry_run, args.force, args.platform))


if __name__ == "__main__":
    main()
