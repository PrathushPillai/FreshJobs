"""H-1B sponsor data from the US Department of Labor's public LCA disclosure files.

Every H-1B petition starts with a Labor Condition Application (LCA). Counting an employer's certified
H-1B LCAs shows how much it sponsors. Download the latest "LCA Disclosure Data" .xlsx from
https://www.dol.gov/agencies/eta/foreign-labor/performance (scripted downloads are blocked, so use a browser),
save it in backend/data/h1b/, then:

    python -m app.h1b import data/h1b/LCA_Disclosure_Data_FY2026_Q3.xlsx
    python -m app.h1b discover --top 2000      # find job boards for the biggest sponsors we don't track
"""

import argparse
import asyncio
import csv
import logging
import re
from collections import Counter, defaultdict
from pathlib import Path

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.models import H1BSponsor, Job

log = logging.getLogger(__name__)

# Legal/corporate words that don't identify the employer ("AMAZON.COM SERVICES LLC" -> "amazon").
_DROP = {
    "inc", "incorporated", "llc", "l", "c", "ltd", "limited", "corp", "corporation", "co", "company", "lp", "llp",
    "plc", "pllc", "pc", "na", "the", "com", "usa", "us", "dba", "services", "service", "and",
}


# Words that can follow a brand without making it a different company ("Meta Platforms", "Box Technologies").
_GENERIC = {
    "platforms", "platform", "technologies", "technology", "tech", "labs", "systems", "software", "holdings",
    "group", "global", "international", "solutions", "digital", "online", "web", "ai", "io", "hq", "americas",
    "america", "north", "business", "networks", "financial", "commerce", "cloud", "health", "media", "studios",
    "development", "center", "centre", "operations", "corporate",
}


def name_tokens(name: str) -> tuple[str, ...]:
    name = (name or "").lower().replace("&", " and ").replace("'", "")
    return tuple(t for t in re.findall(r"[a-z0-9]+", name) if t not in _DROP)


class SponsorIndex:
    """Matches an employer name from a job to DOL employer names by token prefix.

    "Amazon" matches "Amazon.com Services LLC" and "Amazon Web Services Inc"; "Meta" matches
    "Meta Platforms, Inc.". A single short token (<= 4 chars, e.g. "Via", "Box") must match exactly,
    since prefixes like that match unrelated firms.
    """

    def __init__(self, rows: list[tuple[str, int]]):
        self.by_first: dict[str, list[tuple[tuple[str, ...], int]]] = defaultdict(list)
        for key, filings in rows:
            tokens = tuple(key.split())
            if tokens:
                self.by_first[tokens[0]].append((tokens, filings))

    def matches(self, company_name: str) -> list[tuple[str, int]]:
        """(normalized DOL employer name, filings) for every employer entity that matches."""
        tokens = name_tokens(company_name)
        if not tokens:
            return []
        # A single word ("Via", "Meta", "Virtual") only takes generic suffixes ("Meta Platforms",
        # "Amazon Web Services"), not other words ("Virtual Staffing Inc" is a different firm).
        single = len(tokens) == 1

        def ok(cand: tuple[str, ...]) -> bool:
            if cand == tokens:
                return True
            if cand[: len(tokens)] != tokens:
                return False
            return not single or all(t in _GENERIC for t in cand[len(tokens):])

        return [(" ".join(cand), n) for cand, n in self.by_first.get(tokens[0], []) if ok(cand)]

    def filings(self, company_name: str) -> int:
        return sum(n for _, n in self.matches(company_name))


_index: SponsorIndex | None = None


def sponsor_index(session: Session) -> SponsorIndex:
    global _index
    if _index is None:
        _index = SponsorIndex(list(session.execute(select(H1BSponsor.name_key, H1BSponsor.filings))))
    return _index


def _rows(path: Path):
    if path.suffix.lower() == ".csv":
        with open(path, newline="", encoding="utf-8-sig") as f:
            yield from csv.DictReader(f)
        return
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True)
    rows = wb.worksheets[0].iter_rows(values_only=True)
    header = [str(h or "").strip().upper() for h in next(rows)]
    for row in rows:
        yield dict(zip(header, row))


def aggregate(path: Path) -> list[dict]:
    """Certified H-1B LCAs per employer, with their most common job titles and states."""
    filings: Counter = Counter()
    display: dict[tuple, Counter] = defaultdict(Counter)
    titles: dict[tuple, Counter] = defaultdict(Counter)
    states: dict[tuple, Counter] = defaultdict(Counter)
    seen = 0
    for r in _rows(path):
        seen += 1
        if seen % 100_000 == 0:
            log.info("read %d rows", seen)
        if str(r.get("VISA_CLASS") or "").strip().upper() != "H-1B":
            continue
        if not str(r.get("CASE_STATUS") or "").strip().lower().startswith("certified"):
            continue
        name = str(r.get("EMPLOYER_NAME") or "").strip()
        key = name_tokens(name)
        if not key:
            continue
        filings[key] += 1
        display[key][name] += 1
        if r.get("JOB_TITLE"):
            titles[key][str(r["JOB_TITLE"]).strip().title()] += 1
        if r.get("WORKSITE_STATE"):
            states[key][str(r["WORKSITE_STATE"]).strip().upper()] += 1
    log.info("read %d rows, %d sponsoring employers", seen, len(filings))
    return [
        {
            "name": display[key].most_common(1)[0][0],
            "name_key": " ".join(key),
            "filings": n,
            "top_titles": [t for t, _ in titles[key].most_common(8)],
            "top_states": [s for s, _ in states[key].most_common(5)],
        }
        for key, n in filings.most_common()
    ]


def import_file(session: Session, path: Path, source: str) -> int:
    global _index
    sponsors = aggregate(path)
    session.execute(delete(H1BSponsor))
    session.add_all(H1BSponsor(source=source, **s) for s in sponsors)
    session.commit()
    _index = None
    refresh_job_counts(session)
    return len(sponsors)


def refresh_job_counts(session: Session) -> int:
    """Recompute Job.h1b_filings for every employer in the jobs table."""
    index = sponsor_index(session)
    names = [n for (n,) in session.execute(select(Job.company_name).distinct()) if n]
    for name in names:
        session.execute(update(Job).where(Job.company_name == name).values(h1b_filings=index.filings(name)))
    session.commit()
    return len(names)


async def discover_top(session: Session, top: int) -> list:
    """Find job boards for the biggest sponsors we don't track yet."""
    import httpx

    from app.pipeline.discovery import discover

    names = list(session.scalars(select(H1BSponsor.name).order_by(H1BSponsor.filings.desc()).limit(top)))
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        found = []
        for i in range(0, len(names), 100):  # batches, so progress is saved as we go
            found += await discover(session, client, names[i : i + 100], limit=100)
            log.info("probed %d/%d sponsors, %d boards found so far", min(i + 100, len(names)), len(names), len(found))
    return found


def main() -> None:
    from app.db import SessionLocal, init_db

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    imp = sub.add_parser("import")
    imp.add_argument("path", type=Path)
    disc = sub.add_parser("discover")
    disc.add_argument("--top", type=int, default=2000)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    init_db()
    with SessionLocal() as session:
        if args.cmd == "import":
            n = import_file(session, args.path, source=args.path.name)
            print(f"Imported {n} sponsoring employers from {args.path.name}")
        else:
            found = asyncio.run(discover_top(session, args.top))
            print(f"Added {len(found)} job boards: " + ", ".join(f"{c.name} ({c.ats})" for c in found))


if __name__ == "__main__":
    main()
