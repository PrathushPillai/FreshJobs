"""H-1B approvals and denials per employer, from USCIS's H-1B Employer Data Hub.

DOL's LCAs (app/h1b.py) show what an employer *applied* to sponsor. USCIS's data shows the petitions it
actually decided, so it's the better measure of whether an employer gets H-1Bs through, and its
"New Employment" approvals count workers new to H-1B (the new-grad case).

The hub is a Tableau dashboard: https://www.uscis.gov/tools/reports-and-studies/h-1b-employer-data-hub
-> Crosstab View -> one Fiscal Year -> Download -> Crosstab -> CSV, saved in backend/data/uscis_h1b/
(UTF-16, tab-separated, one row per employer and location). Older years are plain CSVs at
https://www.uscis.gov/archive/h-1b-employer-data-hub-files. Then:

    python -m app.uscis import data/uscis_h1b
"""

import argparse
import csv
import logging
from collections import defaultdict
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.h1b import name_tokens, names_match
from app.models import USCISH1B

log = logging.getLogger(__name__)

_KINDS = ("New Employment", "Continuation", "Change with Same Employer", "New Concurrent", "Change of Employer", "Amended")


def _int(value) -> int:
    try:
        return int(str(value or "0").replace(",", "").strip() or 0)
    except ValueError:
        return 0


def _open(path: Path):
    """The dashboard's export is UTF-16 with tabs; the archived yearly files are UTF-8 with commas."""
    with open(path, "rb") as f:
        utf16 = f.read(2) in (b"\xff\xfe", b"\xfe\xff")
    f = open(path, encoding="utf-16" if utf16 else "utf-8-sig", newline="")
    return f, ("\t" if utf16 else ",")


def _get(row: dict, *names: str) -> str:
    """A column by any of its spellings ("Fiscal Year   ", "Fiscal Year", "Employer (Petitioner) Name", "Employer")."""
    for key, value in row.items():
        if key and key.strip().lower() in names:
            return value or ""
    return ""


def read_exports(folder: Path) -> list[dict]:
    totals: dict[tuple, dict] = defaultdict(lambda: {"new_approvals": 0, "new_denials": 0, "approvals": 0, "denials": 0})
    display: dict[tuple, str] = {}
    for path in sorted(folder.glob("*.csv")):
        f, delimiter = _open(path)
        with f:
            for row in csv.DictReader(f, delimiter=delimiter):
                name = _get(row, "employer (petitioner) name", "employer").strip()
                year = _int(_get(row, "fiscal year"))
                key = name_tokens(name)
                if not key or not year:
                    continue
                t = totals[(key, year)]
                for kind in _KINDS:
                    approved = _int(_get(row, f"{kind.lower()} approval"))
                    denied = _int(_get(row, f"{kind.lower()} denial"))
                    t["approvals"] += approved
                    t["denials"] += denied
                    if kind == "New Employment":
                        t["new_approvals"] += approved
                        t["new_denials"] += denied
                display.setdefault((key, year), name)
        log.info("read %s: %d employer-years so far", path.name, len(totals))
    return [
        {"first_token": key[0][:100], "name_key": " ".join(key)[:300], "name": display[(key, year)][:300],
         "fiscal_year": year, **t}
        for (key, year), t in totals.items()
    ]


def import_folder(session: Session, folder: Path) -> int:
    rows = read_exports(folder)
    session.execute(delete(USCISH1B))
    for i in range(0, len(rows), 50_000):
        session.execute(USCISH1B.__table__.insert(), rows[i : i + 50_000])
    session.commit()
    return len(rows)


def lookup(session: Session, company_name: str) -> dict | None:
    """The employer's approvals and denials per fiscal year (newest first), over every matching petitioner name."""
    tokens = name_tokens(company_name)
    if not tokens:
        return None
    years: dict[int, dict] = {}
    names: set[str] = set()
    for r in session.scalars(select(USCISH1B).where(USCISH1B.first_token == tokens[0])):
        if not names_match(tokens, tuple(r.name_key.split())):
            continue
        names.add(r.name)
        y = years.setdefault(r.fiscal_year, {"fiscal_year": r.fiscal_year, "new_approvals": 0, "new_denials": 0,
                                            "approvals": 0, "denials": 0})
        for field in ("new_approvals", "new_denials", "approvals", "denials"):
            y[field] += getattr(r, field)
    if not years:
        return None
    return {"years": sorted(years.values(), key=lambda y: -y["fiscal_year"]), "names": sorted(names)[:5]}


def main() -> None:
    from app.db import SessionLocal, init_db

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    imp = sub.add_parser("import")
    imp.add_argument("folder", type=Path)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    init_db()
    with SessionLocal() as session:
        print(f"Imported {import_folder(session, args.folder):,} employer-years")


if __name__ == "__main__":
    main()
