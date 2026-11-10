"""H-1B sponsor data from the US Department of Labor's public LCA disclosure files.

Every H-1B petition starts with a Labor Condition Application (LCA). Counting an employer's certified
H-1B LCAs shows how much it sponsors. Download the latest "LCA Disclosure Data" .xlsx from
https://www.dol.gov/agencies/eta/foreign-labor/performance (scripted downloads are blocked, so use a browser),
save it in backend/data/h1b/, then:

    python -m app.h1b import data/h1b/LCA_Disclosure_Data_FY2025_Q4.xlsx data/h1b/LCA_Disclosure_Data_FY2026_Q3.xlsx

Each fiscal year's file is cumulative (the Q4 file holds the whole year), so pass the latest file of each
year; the counts and per-occupation wages cover every file given.
    python -m app.h1b discover --top 2000      # find job boards for the biggest sponsors we don't track
    python -m app.h1b classify data/h1b/<files>   # fill naics/client-site share/kind without re-importing
    python -m app.h1b classify                 # recompute kind from the stored columns
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

from app.models import H1BRole, H1BSponsor, Job

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


def names_match(tokens: tuple[str, ...], cand: tuple[str, ...]) -> bool:
    """Does a registered employer name (cand) belong to the employer a job names (tokens)?

    A prefix match: "Amazon" takes "Amazon Web Services". A single word ("Via", "Meta", "Virtual") only
    takes generic suffixes ("Meta Platforms"), not other words ("Virtual Staffing Inc" is a different firm).
    """
    if cand == tokens:
        return True
    if cand[: len(tokens)] != tokens:
        return False
    return len(tokens) > 1 or all(t in _GENERIC for t in cand[len(tokens):])


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
        return [(" ".join(cand), n) for cand, n in self.by_first.get(tokens[0], []) if names_match(tokens, cand)]

    def filings(self, company_name: str) -> int:
        return sum(n for _, n in self.matches(company_name))


_index: SponsorIndex | None = None


def sponsor_index(session: Session) -> SponsorIndex:
    global _index
    if _index is None:
        _index = SponsorIndex(list(session.execute(select(H1BSponsor.name_key, H1BSponsor.filings))))
    return _index


# Title words that say nothing about the kind of work ("Senior Software Engineer II" -> software, engineer).
_TITLE_NOISE = {
    "senior", "sr", "junior", "jr", "staff", "principal", "lead", "associate", "intern", "internship", "entry", "level",
    "i", "ii", "iii", "iv", "v", "1", "2", "3", "4", "the", "of", "and", "for", "to", "in", "a", "an", "with", "at",
    "remote", "hybrid", "onsite", "us", "usa", "new", "grad", "graduate", "early", "career", "full", "time", "part",
    "contract", "temporary", "team", "member",
}

# Occupations (SOC codes) a job title most likely falls under, tried when the employer's own LCA titles
# don't resemble the job's title. First match wins; patterns are checked against the lowercased title.
_TITLE_SOC = [
    (r"data engineer|etl|data platform|data infrastructure|data warehouse|database", ["15-1243", "15-1242", "15-1252"]),
    (r"data scien|machine learning|\bml\b|\bai\b|research scientist|applied scientist|deep learning",
     ["15-2051", "15-1221", "15-1252"]),
    (r"data analy|analytics|business intelligence|\bbi\b|reporting analyst|insights",
     ["15-2051", "15-2041", "13-1161", "15-1211", "15-2031"]),
    (r"security|cyber", ["15-1212", "15-1299"]),
    (r"network|cloud|devops|site reliability|\bsre\b|infrastructure|systems? admin", ["15-1244", "15-1241", "15-1252", "15-1299"]),
    (r"\bqa\b|quality assurance|test", ["15-1253", "15-1252"]),
    (r"web|front.?end|ui\b|ux", ["15-1254", "15-1255", "15-1252"]),
    (r"software|developer|programmer|engineer.*(full.?stack|back.?end|mobile|ios|android)|\bswe\b|\bsde\b",
     ["15-1252", "15-1253", "15-1299", "15-1211"]),
    (r"business analyst|systems analyst|product analyst", ["15-1211", "13-1111", "15-2051"]),
    (r"product manager|program manager|project manager", ["11-3021", "15-1299", "13-1082", "13-1111"]),
    (r"financial analyst|finance", ["13-2051", "13-2052", "13-2011"]),
    (r"account", ["13-2011"]),
    (r"mechanical", ["17-2141"]),
    (r"electrical|hardware|asic|fpga|silicon|chip", ["17-2061", "17-2072", "17-2071"]),
    (r"civil|structural", ["17-2051"]),
    (r"engineer", ["15-1252", "17-2199", "15-1299"]),
]


def title_tokens(title: str) -> set[str]:
    words = re.findall(r"[a-z0-9+#]+", (title or "").lower())
    return {w[:-1] if len(w) > 4 and w.endswith("s") else w for w in words if w not in _TITLE_NOISE}


def role_history(session: Session, company_name: str, job_title: str, limit: int = 6) -> dict | None:
    """The employer's H-1B filings by occupation, and the occupation that best fits this job.

    The fit is found by comparing the job's title with the employer's own LCA job titles in each occupation
    (most employers file under their real titles), falling back to likely occupation codes for the title.
    """
    keys = [k for k, _ in sponsor_index(session).matches(company_name)]
    if not keys:
        return None
    merged: dict[str, dict] = {}
    for r in session.scalars(select(H1BRole).where(H1BRole.name_key.in_(keys)).order_by(H1BRole.filings.desc())):
        m = merged.get(r.soc_code)
        if m is None:  # the biggest entity's row first: its wages and cities stand for the occupation
            merged[r.soc_code] = {
                "soc_code": r.soc_code, "soc_title": r.soc_title, "filings": r.filings, "median_wage": r.median_wage,
                "low_wage": r.low_wage, "high_wage": r.high_wage, "top_titles": list(r.top_titles),
                "top_cities": list(r.top_cities),
            }
        else:
            m["filings"] += r.filings
            m["top_titles"] += [t for t in r.top_titles if t not in m["top_titles"]]
    if not merged:
        return None
    roles = sorted(merged.values(), key=lambda m: -m["filings"])
    wanted = title_tokens(job_title)

    def fit(role: dict) -> float:
        best = 0.0
        for title in role["top_titles"]:
            have = title_tokens(title)
            if wanted and have:
                best = max(best, len(wanted & have) / len(wanted | have))
        return best

    match, how = None, None
    if wanted:
        scored = max(roles, key=lambda m: (fit(m), m["filings"]))
        if fit(scored) >= 0.5:
            match, how = scored, "title"
    if match is None:
        lowered = (job_title or "").lower()
        for pattern, socs in _TITLE_SOC:
            if re.search(pattern, lowered):
                match = next((merged[s] for s in socs if s in merged), None)
                how = "occupation" if match else None
                break
    return {
        "total": sum(m["filings"] for m in roles),
        "match": {**match, "matched_by": how, "top_titles": match["top_titles"][:5]} if match else None,
        "roles": [{**m, "top_titles": m["top_titles"][:3]} for m in roles[:limit]],
    }


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


# Multipliers that turn an offered wage into a yearly one (WAGE_UNIT_OF_PAY).
_PER_YEAR = {"year": 1, "month": 12, "bi-weekly": 26, "week": 52, "hour": 2080}


def annual_wage(amount, unit) -> int | None:
    """The LCA's offered wage per year in USD, or None if it's missing or implausible."""
    try:
        value = float(str(amount).replace("$", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    per_year = _PER_YEAR.get(str(unit or "").strip().lower())
    if per_year is None:
        return None
    wage = round(value * per_year)
    return wage if 15_000 <= wage <= 2_000_000 else None


def _percentile(values: list[int], q: float) -> int | None:
    if not values:
        return None
    values = sorted(values)
    return values[min(len(values) - 1, int(q * len(values)))]


def _yes(value) -> int:
    return 1 if str(value or "").strip().upper() in ("Y", "YES") else 0


# NAICS codes of IT services firms (custom programming, systems design, other computer services, consulting)
# and of staffing agencies (employment placement, temporary help, PEOs).
# Prefixes, since employers write 4, 5 or 6 digits ("5415", "54151", "541511"). Accounting firms (5412: EY,
# Deloitte & Touche) count only when they also place workers at clients.
_IT_SERVICES_NAICS = ("5415", "5416", "54199")
_ACCOUNTING_NAICS = ("5412",)
_STAFFING_NAICS = ("5613",)
SERVICES_NAME = re.compile(r"\b(consult\w*|staffing|outsourc\w*|infotech|it services|recruit\w*|manpower|"
                            r"talent solutions|workforce solutions)\b", re.I)


def employer_kind(name: str, naics: str, client_site_share: float | None, dependent_share: float | None) -> str:
    """ "staffing", "consultancy" or "" (an employer hiring for its own products and operations).

    From its own LCAs: a staffing NAICS code; or workers placed at other companies' sites (SECONDARY_ENTITY)
    for a large share of filings; or an IT-services NAICS code with H-1B-dependent filings or a services name
    ("... Consulting", "... Infotech"). Product companies file under their own industry (Google 519290,
    Microsoft 513210, Nvidia 334413) and almost never place workers at a client.
    """
    naics = naics or ""
    client = client_site_share or 0.0
    dependent = dependent_share or 0.0
    if naics.startswith(_STAFFING_NAICS):
        return "staffing"
    if client >= CLIENT_SITE_SHARE:
        return "consultancy"
    if naics.startswith(_IT_SERVICES_NAICS) and (dependent >= 0.5 or client >= 0.1 or SERVICES_NAME.search(name or "")):
        return "consultancy"
    if naics.startswith(_ACCOUNTING_NAICS) and client >= 0.1:
        return "consultancy"
    return ""


CLIENT_SITE_SHARE = 0.3


def reclassify(session: Session) -> int:
    """Recompute H1BSponsor.kind from the stored NAICS and shares (after changing employer_kind)."""
    changed = 0
    for s in session.scalars(select(H1BSponsor)):
        kind = employer_kind(s.name, s.naics, s.client_site_share, s.dependent_share)
        if kind != (s.kind or ""):
            s.kind = kind
            changed += 1
    session.commit()
    return changed


def classify_file(session: Session, paths: list[Path]) -> int:
    """Fill naics, shares and kind on the existing sponsor rows from the LCA files, without re-importing."""
    sponsors, _ = aggregate(paths)
    rows = [{"k": s["name_key"], "n": s["naics"], "c": s["client_site_share"], "d": s["dependent_share"],
             "kind": s["kind"]} for s in sponsors]
    from sqlalchemy import text

    session.execute(text("UPDATE h1b_sponsors SET naics = :n, client_site_share = :c, dependent_share = :d, "
                         "kind = :kind WHERE name_key = :k"), rows)
    session.commit()
    return sum(1 for r in rows if r["kind"])


def aggregate(paths: Path | list[Path]) -> tuple[list[dict], list[dict]]:
    """Certified H-1B LCAs per employer (with their commonest titles and states), and per employer and occupation."""
    filings: Counter = Counter()
    display: dict[tuple, Counter] = defaultdict(Counter)
    titles: dict[tuple, Counter] = defaultdict(Counter)
    states: dict[tuple, Counter] = defaultdict(Counter)
    role_titles: dict[tuple, Counter] = defaultdict(Counter)  # (employer key, soc code) -> job titles
    role_cities: dict[tuple, Counter] = defaultdict(Counter)
    role_wages: dict[tuple, list[int]] = defaultdict(list)
    soc_names: dict[str, Counter] = defaultdict(Counter)
    naics: dict[tuple, Counter] = defaultdict(Counter)
    client_site: Counter = Counter()  # filings placing the worker at another company's site
    dependent: Counter = Counter()  # filings as an H-1B-dependent employer
    seen = 0
    paths = [paths] if isinstance(paths, Path) else paths
    # One fiscal year's file is cumulative (Q4 holds the whole year), so give one file per year.
    for r in (row for path in paths for row in _rows(path)):
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
        code = str(r.get("NAICS_CODE") or "").strip().split(".")[0]
        if code:
            naics[key][code] += 1
        client_site[key] += _yes(r.get("SECONDARY_ENTITY"))
        dependent[key] += _yes(r.get("H_1B_DEPENDENT"))
        title = str(r.get("JOB_TITLE") or "").strip().title()
        state = str(r.get("WORKSITE_STATE") or "").strip().upper()
        if title:
            titles[key][title] += 1
        if state:
            states[key][state] += 1
        soc = str(r.get("SOC_CODE") or "").strip()[:7]  # "15-1252.00" -> "15-1252"
        if not re.fullmatch(r"\d\d-\d{4}", soc):
            continue
        role = (key, soc)
        soc_names[soc][str(r.get("SOC_TITLE") or "").strip()] += 1
        if title:
            role_titles[role][title] += 1
        city = str(r.get("WORKSITE_CITY") or "").strip().title()
        if city:
            role_cities[role][f"{city}, {state}" if state else city] += 1
        wage = annual_wage(r.get("WAGE_RATE_OF_PAY_FROM"), r.get("WAGE_UNIT_OF_PAY"))
        if wage:
            role_wages[role].append(wage)
    log.info("read %d rows, %d sponsoring employers, %d employer occupations", seen, len(filings), len(role_titles))
    roles = [
        {
            "name_key": " ".join(key),
            "soc_code": soc,
            "soc_title": soc_names[soc].most_common(1)[0][0][:200],
            "filings": sum(role_titles[(key, soc)].values()) or len(role_wages[(key, soc)]),
            "median_wage": _percentile(role_wages[(key, soc)], 0.5),
            "low_wage": _percentile(role_wages[(key, soc)], 0.25),
            "high_wage": _percentile(role_wages[(key, soc)], 0.75),
            "top_titles": [t for t, _ in role_titles[(key, soc)].most_common(12)],
            "top_cities": [c for c, _ in role_cities[(key, soc)].most_common(5)],
        }
        for key, soc in set(role_titles) | set(role_wages)
    ]
    sponsors = [
        {
            "name": display[key].most_common(1)[0][0],
            "name_key": " ".join(key),
            "filings": n,
            "top_titles": [t for t, _ in titles[key].most_common(8)],
            "top_states": [s for s, _ in states[key].most_common(5)],
            "naics": naics[key].most_common(1)[0][0][:10] if naics[key] else "",
            "client_site_share": round(client_site[key] / n, 3),
            "dependent_share": round(dependent[key] / n, 3),
        }
        for key, n in filings.most_common()
    ]
    for s in sponsors:
        s["kind"] = employer_kind(s["name"], s["naics"], s["client_site_share"], s["dependent_share"])
    return sponsors, roles


def import_file(session: Session, path: Path | list[Path], source: str) -> int:
    global _index
    sponsors, roles = aggregate(path)
    session.execute(delete(H1BSponsor))
    session.execute(delete(H1BRole))
    session.add_all(H1BSponsor(source=source, **s) for s in sponsors)
    session.add_all(H1BRole(**r) for r in roles)
    session.commit()
    _index = None
    refresh_job_counts(session)
    return len(sponsors)


def refresh_job_counts(session: Session) -> int:
    """Recompute Job.h1b_filings for every employer in the jobs table."""
    index = sponsor_index(session)
    names = [n for (n,) in session.execute(select(Job.company_name).distinct()) if n]
    for name in names:
        n = index.filings(name)  # write only the rows whose count changes: most don't
        session.execute(update(Job).where(Job.company_name == name, Job.h1b_filings.is_distinct_from(n))
                        .values(h1b_filings=n))
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
    imp.add_argument("paths", type=Path, nargs="+")
    cls = sub.add_parser("classify", help="fill employer kind (consultancy/staffing) from the LCA files")
    cls.add_argument("paths", type=Path, nargs="*")
    disc = sub.add_parser("discover")
    disc.add_argument("--top", type=int, default=2000)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    init_db()
    with SessionLocal() as session:
        if args.cmd == "import":
            source = ", ".join(p.name for p in args.paths)
            n = import_file(session, args.paths, source=source[:200])
            print(f"Imported {n} sponsoring employers from {source}")
        elif args.cmd == "classify":
            if args.paths:
                n = classify_file(session, args.paths)
                print(f"{n} employers are consultancies or staffing firms")
            else:
                print(f"{reclassify(session)} employers changed kind")
        else:
            found = asyncio.run(discover_top(session, args.top))
            print(f"Added {len(found)} job boards: " + ", ".join(f"{c.name} ({c.ats})" for c in found))


if __name__ == "__main__":
    main()
