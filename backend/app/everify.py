"""Which employers use E-Verify, from USCIS's E-Verify Employer Search.

STEM OPT students can only work for an employer enrolled in E-Verify, so every job shows whether its
employer is enrolled. USCIS no longer publishes a bulk file: the list comes from the Tableau dashboard at
https://www.e-verify.gov/e-verify-employer-search. Its export of all years is "too large", so the user
exports it one Industry at a time (Download -> Crosstab, Date Enrolled "Last 30 years", Open and
Terminated) into backend/data/everify/. The files are UTF-16, tab-separated. Then:

    python -m app.everify import data/everify      # every *.csv in the folder; then the jobs refresh

An employer often has several accounts (subsidiaries, re-enrollments). It counts as enrolled ("yes") if
any matching account is open, and "ended" if every one has been terminated. USCIS warns that a missing
name doesn't prove an employer isn't enrolled (it may have enrolled under another legal name), so the
UI says "not found", never "not enrolled".
"""

import argparse
import csv
import logging
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.h1b import name_tokens, names_match
from app.models import EVerifyAccount, Job

log = logging.getLogger(__name__)


def _date(value: str) -> date | None:
    value = (value or "").strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    return None


def read_exports(folder: Path) -> list[dict]:
    """Every account in the exported files, once per (name, status, dates), with its DBA as a second name."""
    seen: set[tuple] = set()
    accounts = []
    today = date.today()
    for path in sorted(folder.glob("*.csv")):
        with open(path, encoding="utf-16", newline="") as f:
            reader = csv.DictReader(f, delimiter="\t")
            for row in reader:
                status = (row.get("Account Status") or "").strip()
                enrolled = _date(row.get("Date Enrolled", ""))
                terminated = _date(row.get("Date Terminated", ""))
                is_open = status == "Open" and (terminated is None or terminated > today)
                for name in {(row.get("Employer") or "").strip(), (row.get("Doing Business As") or "").strip()}:
                    key = name_tokens(name)
                    if not key:
                        continue
                    ident = (key, is_open, enrolled, terminated)
                    if ident in seen:
                        continue
                    seen.add(ident)
                    accounts.append({
                        "first_token": key[0][:100], "name_key": " ".join(key)[:300], "name": name[:300], "open": is_open,
                        "enrolled": enrolled, "terminated": terminated,
                        "workforce": (row.get("Workforce Size") or "").strip()[:40],
                    })
        log.info("read %s: %d accounts so far", path.name, len(accounts))
    return accounts


def lookup(session: Session, company_name: str) -> dict | None:
    """The employer's E-Verify status, from every account whose name matches (see h1b.names_match)."""
    tokens = name_tokens(company_name)
    if not tokens:
        return None
    rows = [
        a for a in session.scalars(select(EVerifyAccount).where(EVerifyAccount.first_token == tokens[0]))
        if names_match(tokens, tuple(a.name_key.split()))
    ]
    if not rows:
        return None
    open_rows = [a for a in rows if a.open]
    if open_rows:
        since = min((a.enrolled for a in open_rows if a.enrolled), default=None)
        best = max(open_rows, key=lambda a: (_size(a.workforce), a.enrolled or date.min))
        return {"status": "yes", "since": since, "ended": None, "name": best.name, "workforce": best.workforce,
                "accounts": len(rows)}
    last = max(rows, key=lambda a: a.terminated or date.min)
    return {"status": "ended", "since": min((a.enrolled for a in rows if a.enrolled), default=None),
            "ended": last.terminated, "name": last.name, "workforce": last.workforce, "accounts": len(rows)}


def _size(workforce: str) -> int:
    """'1,000 to 2,499' -> 1000, for picking the main account."""
    digits = workforce.split(" ")[0].replace(",", "")
    return int(digits) if digits.isdigit() else 0


# Employer name -> status. Many jobs share an employer, so each name is looked up once per process
# (cleared by import_folder; a restart picks up an import made by another process).
_status_cache: dict[str, str | None] = {}


def status_for(session: Session, company_name: str) -> str | None:
    """"yes", "ended" or None (not in the list) for a job's employer name."""
    company_name = company_name or ""
    if company_name not in _status_cache:
        if len(_status_cache) > 100_000:
            _status_cache.clear()
        found = lookup(session, company_name)
        _status_cache[company_name] = found["status"] if found else None
    return _status_cache[company_name]


def import_folder(session: Session, folder: Path) -> int:
    accounts = read_exports(folder)
    session.execute(delete(EVerifyAccount))
    for i in range(0, len(accounts), 50_000):
        session.execute(EVerifyAccount.__table__.insert(), accounts[i : i + 50_000])
        session.commit()
    _status_cache.clear()
    refresh_jobs(session)
    return len(accounts)


def refresh_jobs(session: Session) -> int:
    """Recompute Job.everify for every employer in the jobs table; returns how many employers are enrolled."""
    names = [n for (n,) in session.execute(select(Job.company_name).distinct()) if n]
    enrolled = 0
    for i, name in enumerate(names, 1):
        status = status_for(session, name)
        enrolled += status == "yes"
        session.execute(update(Job).where(Job.company_name == name, Job.everify.is_distinct_from(status))
                        .values(everify=status))
        if i % 2000 == 0:
            session.commit()
            log.info("matched %d/%d employers, %d enrolled", i, len(names), enrolled)
    session.commit()
    log.info("%d of %d employers are enrolled in E-Verify", enrolled, len(names))
    return enrolled


def main() -> None:
    from app.db import SessionLocal, init_db

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    imp = sub.add_parser("import")
    imp.add_argument("folder", type=Path)
    sub.add_parser("refresh")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    init_db()
    with SessionLocal() as session:
        if args.cmd == "import":
            print(f"Imported {import_folder(session, args.folder):,} E-Verify accounts")
        else:
            print(f"{refresh_jobs(session):,} employers are enrolled")


if __name__ == "__main__":
    main()
