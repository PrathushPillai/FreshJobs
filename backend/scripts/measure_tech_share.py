"""How much of the database is IT jobs? Read-only measurement for the IT-only decision (2026-11-19).

Every job's title (open and closed) is read from the covering index ix_jobs_company_titles, never the job rows,
and labelled by `tech_roles.classify`. Reports: counts by label (open and all), by platform, the boards that are
almost all non-IT, the size of a random sample's descriptions per label (a proxy for space), and random sample titles
per label for a hand check (written to tech_share_sample.json next to the database).

    PYTHONPATH=. nice -n 15 .venv/bin/python scripts/measure_tech_share.py
"""

import collections
import json
import random
import time
from pathlib import Path

from sqlalchemy import text

from app.db import SessionLocal
from app.matching import _models_dir
from app.pipeline.tech_roles import classify

SAMPLE = 300
SIZE_SAMPLE = 2000


def main() -> None:
    t0 = time.time()
    random.seed(19)
    with SessionLocal() as session:
        ats = dict(session.execute(text("SELECT id, ats FROM companies")).all())
        names = dict(session.execute(text("SELECT id, name FROM companies")).all())
        rows = session.execute(text(
            "SELECT id, company_id, title, closed_at IS NULL FROM jobs INDEXED BY ix_jobs_company_titles")).all()
        print(f"read {len(rows):,} titles in {time.time() - t0:.0f} s", flush=True)

        cache: dict[str, str] = {}
        counts = collections.Counter()
        by_ats = collections.defaultdict(collections.Counter)
        by_board = collections.defaultdict(collections.Counter)
        ids = collections.defaultdict(list)
        examples = collections.defaultdict(list)
        for job_id, company_id, title, is_open in rows:
            label = cache.get(title or "")
            if label is None:
                label = cache[title or ""] = classify(title or "")
            counts[(label, "all")] += 1
            if is_open:
                counts[(label, "open")] += 1
                by_ats[ats.get(company_id, "?")][label] += 1
                by_board[company_id][label] += 1
                examples[label].append(title)
            ids[label].append(job_id)

        total_all = sum(v for (lab, k), v in counts.items() if k == "all")
        total_open = sum(v for (lab, k), v in counts.items() if k == "open")
        print("\nlabel   open jobs            all jobs (open + closed)")
        for lab in ("tech", "edge", "other"):
            o, a = counts[(lab, "open")], counts[(lab, "all")]
            print(f"{lab:6} {o:>10,} ({o / total_open:5.1%})   {a:>10,} ({a / total_all:5.1%})")

        print("\nopen jobs by platform (biggest 15): tech / edge / other")
        for name, c in sorted(by_ats.items(), key=lambda kv: -sum(kv[1].values()))[:15]:
            n = sum(c.values())
            print(f"  {name:16} {n:>9,}  tech {c['tech'] / n:5.1%}  edge {c['edge'] / n:5.1%}  other {c['other'] / n:5.1%}")

        big = [(cid, c) for cid, c in by_board.items() if sum(c.values()) >= 200]
        almost_none = [(cid, c) for cid, c in big if (c["tech"] + c["edge"]) / sum(c.values()) < 0.02]
        print(f"\nboards with 200+ open jobs: {len(big):,}; under 2% IT or edge: {len(almost_none):,}, "
              f"holding {sum(sum(c.values()) for _, c in almost_none):,} open jobs")
        for cid, c in sorted(almost_none, key=lambda kv: -sum(kv[1].values()))[:15]:
            print(f"  {names.get(cid, cid)!s:40} {sum(c.values()):>7,} open, {c['tech'] + c['edge']} IT/edge")

        print("\ndescription size per job (random sample of each label):")
        for lab in ("tech", "edge", "other"):
            pick = random.sample(ids[lab], min(SIZE_SAMPLE, len(ids[lab])))
            sizes = [s or 0 for (s,) in session.execute(
                text(f"SELECT length(description) FROM jobs WHERE id IN ({','.join(map(str, pick))})"))]
            avg = sum(sizes) / max(len(sizes), 1)
            print(f"  {lab:6} {avg:8,.0f} characters on average -> about {avg * len(ids[lab]) / 1e9:.1f} GB of text")

        db_bytes = session.execute(text("PRAGMA page_count")).scalar() * session.execute(text("PRAGMA page_size")).scalar()
        print(f"\ndatabase file: {db_bytes / 1e9:.1f} GB")

    sample = {lab: random.sample(examples[lab], min(SAMPLE, len(examples[lab]))) for lab in ("tech", "edge", "other")}
    out = Path(_models_dir()).parent / "tech_share_sample.json"
    out.write_text(json.dumps(sample, indent=1))
    print(f"\nsample titles in {out}; took {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
