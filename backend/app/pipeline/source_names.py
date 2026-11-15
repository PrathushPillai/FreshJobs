"""Real employer names for boards that only had their code name ("Tjx", "Nghs", "Acadiahealthcare").

Boards added by harvesting or discovery are named from their slug (harvest.name_from_slug), so the Feed showed
the code instead of the employer: on 2026-11-15 over half of the newest 2,000 US jobs. Each such board is asked
once for the name it gives itself:
- Workday: the CXS job detail's hiringOrganization (several jobs; the name most of them give);
- Greenhouse: the board API's own name;
- any other board: a job page's JSON-LD hiringOrganization, else its <title> ("Rambus Careers",
  "Job Application for ... at Minitab", "... @ Horizon3").
A name is taken only if it plainly belongs to the slug (it contains the slug, or the slug is its initials), so a
subsidiary's legal name ("Marshalls of MA" on TJX's board) or a portal's name never replaces the employer. A
short code with no usable answer is shown in capitals ("TJX", "NGHS").

The board and its jobs are renamed together, with every name-derived field (company_key, fingerprint, H-1B
filings, E-Verify, employer kind). Runs with each discovery run (`name_some`) and as a CLI:
    python -m app.pipeline.source_names [--limit N] [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import html
import logging
import re
from collections import Counter

import httpx
from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from app.models import Company, Job
from app.pipeline.harvest import name_from_slug
from app.sources import ADAPTERS
from app.sources import http_util

log = logging.getLogger(__name__)

USER_AGENT = "job-portal-personal/0.1 (personal job search tool)"
_STOP = {"the", "of", "and", "&", "inc", "llc", "co", "corp", "corporation", "company", "group", "ltd", "lp", "llp"}
_GENERIC = {"confidential", "company", "hiring company", "careers", "jobs", "career site", "employer", "workday",
            "icims", "lever", "greenhouse", "ashby", "bamboohr", "smartrecruiters", "oracle", "successfactors"}
_TITLE_FORMS = [
    re.compile(r"job application for .+? at (.+)$", re.I),  # Greenhouse
    re.compile(r"^.+? @ (.+)$"),  # Ashby
    re.compile(r"^careers (?:at|\||-|–) (.+)$", re.I),
    re.compile(r"^(.+?) (?:careers|jobs|career site|job opportunities)(?: \| .*)?$", re.I),
    re.compile(r"^(.+?) - .+$"),  # Lever: "Employer - Title"
]


def needs_name(company: Company) -> bool:
    """A single employer's board still named after its slug."""
    if getattr(ADAPTERS.get(company.ats), "aggregator", False) or company.ats in ("yc", "website"):
        return False
    return company.name == name_from_slug(company.slug)


def _compact(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _slug_stem(company: Company) -> str:
    base = company.slug.split("|")[0]
    if "://" in base or "." in base:  # a host: careers-acme.icims.com, acme.avature.net/careers
        base = base.split("://", 1)[-1].split("/")[0]
        parts = [p for p in re.split(r"[.]", base) if p not in ("www", "com", "net", "io", "org", "icims", "avature",
                                                                   "careers", "jobs", "us", "en")]
        base = max(parts, key=len) if parts else base
        base = re.sub(r"^(careers|jobs|career)-|-(careers|jobs)$", "", base)
    stem = _compact(base)
    return stem.rstrip("0123456789") or stem  # "aecom2": a second board of AECOM


def belongs(name: str, company: Company) -> bool:
    """The name is this board's employer: it contains the slug, the slug contains it, or the slug is its initials."""
    stem, comp = _slug_stem(company), _compact(name)
    if not stem or not comp:
        return False
    if len(stem) >= 4 and (stem in comp or (len(comp) >= 4 and comp in stem)):
        return True
    words = [w for w in re.findall(r"[a-z0-9]+", name.lower()) if w not in _STOP]
    if stem in words:  # "The TJX Companies" for "tjx"
        return True
    initials = "".join(w[0] for w in words)
    if len(stem) >= 2 and stem in (initials, "".join(w[0] for w in re.findall(r"[a-z0-9]+", name.lower()))):
        return True
    return len(stem) >= 3 and (comp.startswith(stem) or stem.startswith(comp[: max(4, len(stem) - 2)]))


def tidy(name: str) -> str:
    """A display name: entities decoded, spaces collapsed, legal suffixes dropped, SHOUTING calmed."""
    from app.recruiters import short_company

    name = re.sub(r"\s+", " ", html.unescape(name or "")).strip(" -|,")
    name = short_company(name)
    if name.isupper() and len(name) > 5:
        name = " ".join(w if len(w) <= 3 else "-".join(part.capitalize() for part in w.split("-"))
                        for w in name.split())
    return name


def fallback(company: Company) -> str | None:
    """No usable answer: a short code reads better in capitals ("Tjx" -> "TJX")."""
    if company.name.isalpha() and len(company.name) <= 4 and company.name != company.name.upper():
        return company.name.upper()
    return None


def _from_title(page: str) -> str | None:
    m = re.search(r"<title[^>]*>(.*?)</title>", page, re.S | re.I)
    if not m:
        return None
    title = html.unescape(re.sub(r"\s+", " ", m.group(1))).strip()
    for form in _TITLE_FORMS:
        if (hit := form.search(title)):
            return hit.group(1).strip()
    return None


def _from_page(page: str) -> str | None:
    org = http_util.json_ld_job(page).get("hiringOrganization")
    if isinstance(org, dict):
        org = org.get("name")
    if isinstance(org, str) and org.strip():
        return org.strip()
    return _from_title(page)


async def _workday_names(client: httpx.AsyncClient, slug: str) -> list[str]:
    from app.sources.workday import board_host

    tenant, wd, site = slug.split("|")
    base = f"{board_host(tenant, wd)}/wday/cxs/{tenant}/{site}"
    r = await http_util.post(client, base + "/jobs", json={"limit": 5, "offset": 0, "searchText": "", "appliedFacets": {}})
    names = []
    for posting in (r.json().get("jobPostings") or [])[:5]:
        if not posting.get("externalPath"):
            continue
        d = await http_util.get(client, base + posting["externalPath"])
        name = ((d.json().get("hiringOrganization") or {}).get("name") or "").strip()
        if name:
            names.append(name)
    return names


async def find_name(client: httpx.AsyncClient, company: Company, job_url: str | None) -> str | None:
    """The employer's own name for this board, or None."""
    candidates: list[str] = []
    try:
        if company.ats == "workday":
            candidates = await _workday_names(client, company.slug)
        elif company.ats == "greenhouse":
            r = await http_util.get(client, f"https://boards-api.greenhouse.io/v1/boards/{company.slug}")
            candidates = [r.json().get("name") or ""]
        if not any(candidates) and job_url:
            r = await http_util.get(client, job_url)
            candidates = [_from_page(r.text) or ""]
    except (httpx.HTTPError, ValueError, KeyError) as e:
        log.info("no name for %s (%s): %s", company.name, company.ats, e)
    return choose(company, candidates)


# Legal entities abroad and store or cost-centre codes: Workday's hiringOrganization is the legal entity of each job
# ("0704 Thermo Fisher Scientific Australia Pty", "12542 Citicorp Services India Private Limited", "Wal-Mart
# Canada", "Domino's Assistant Manager(03528)"), never a display name as it is.
_ABROAD = re.compile(r"\b(canada|brasil|brazil|australia|india|china|chn|uk|united kingdom|germany|deutschland|france|"
                     r"mexico|méxico|japan|singapore|ireland|netherlands|espa[nñ]a|spain|italia|italy|poland|polska|"
                     r"philippines|malaysia|korea|pty|ltda|gmbh|s\.?a\.?s?|s\.?r\.?l|b\.?v|private limited|pvt|"
                     r"limitada|kk|ag|ab|oy|as)\b", re.I)
_CODES = re.compile(r"^\d+\s+|\s*\(\w*\d\w*\)\s*$")
_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’&.\-]*")


def _brand_span(name: str, stem: str) -> str | None:
    """The words of `name` that spell the board's slug: "Thermo Fisher" in "Thermo Fisher Scientific Australia Pty"
    for "thermofisher", "Wal-Mart" for "walmart", "Circle K" for "circlek"."""
    words = _WORD.findall(name)
    for i in range(len(words)):
        acc = ""
        for j in range(i, len(words)):
            acc += _compact(words[j])
            if acc == stem:
                return " ".join(words[i:j + 1]).rstrip(".")
            if not stem.startswith(acc):
                break
    return None


def choose(company: Company, candidates: list[str]) -> str | None:
    """The display name from what the board says about itself, or None.

    1. The words that spell the slug, from the commonest answer ("Accenture" from "1500 Accenture do Brasil Ltda").
    2. Else a whole answer that belongs to the slug (initials: "Northeast Georgia Health System" for nghs), unless it
       names a legal entity abroad or carries codes.
    """
    cleaned = [_CODES.sub("", tidy(c)).strip() for c in candidates if c and c.strip()]
    ranked = [n for n, _ in Counter(n for n in cleaned if n).most_common()]
    stem = _slug_stem(company)
    for name in ranked:
        if (span := _brand_span(name, stem)) and span.lower() not in _GENERIC:
            return span.upper() if len(span) <= 4 and span.isalpha() and span == span.capitalize() else span
    for name in ranked:
        if (2 <= len(name) <= 60 and name.lower() not in _GENERIC and not _ABROAD.search(name)
                and not any(ch.isdigit() for ch in name) and belongs(name, company)):
            return name
    return None


def rename(session: Session, company: Company, new: str) -> int:
    """Rename a board and its jobs, with every field derived from the employer's name. Returns jobs changed."""
    from app.everify import status_for as everify_status
    from app.h1b import job_employer_kind, sponsor_index
    from app.pipeline.dedupe import fingerprint
    from app.pipeline.normalize import company_key

    old = company.name
    company.name = new
    key = company_key(new)
    index = sponsor_index(session)
    values = dict(company_name=new, company_key=key, everify=everify_status(session, new),
                  h1b_filings=index.filings(new), employer_kind=job_employer_kind(new, company.ats, index))
    rows = session.execute(select(Job.id, Job.title, Job.location).where(
        Job.company_id == company.id, Job.company_name == old)).all()
    for i in range(0, len(rows), 500):
        chunk = rows[i:i + 500]
        session.execute(update(Job).where(Job.id.in_([r.id for r in chunk])).values(**values)
                        .execution_options(synchronize_session=False))
        for r in chunk:
            session.execute(text("UPDATE jobs SET fingerprint = :f WHERE id = :i"),
                            {"f": fingerprint(key, r.title, r.location), "i": r.id})
    return len(rows)


async def _name_all(companies: list[tuple[Company, str | None]], concurrency: int) -> dict[int, str | None]:
    sem = asyncio.Semaphore(concurrency)
    out: dict[int, str | None] = {}
    async with http_util.client(timeout=30, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
        async def one(c: Company, url: str | None):
            async with sem:
                out[c.id] = await find_name(client, c, url)
        await asyncio.gather(*(one(c, u) for c, u in companies))
    return out


def name_some(session: Session, limit: int = 300, dry_run: bool = False, concurrency: int = 8) -> dict:
    """Look up real names for up to `limit` code-named boards (busiest first) and rename them."""
    from app.models import ProbeCache

    asked = {k for (k,) in session.execute(select(ProbeCache.company_key).where(ProbeCache.company_key.like("name:%")))}
    todo = [c for c in session.scalars(select(Company).where(Company.enabled.is_(True)))
            if needs_name(c) and f"name:{c.id}" not in asked]
    if not todo:
        return {"asked": 0, "renamed": 0, "capitalised": 0, "jobs": 0, "examples": []}
    counts = dict(session.execute(text(
        f"SELECT company_id, count(*) FROM jobs INDEXED BY ix_jobs_company_described WHERE closed_at IS NULL "
        f"AND company_id IN ({','.join(str(c.id) for c in todo)}) GROUP BY company_id")).all())
    todo.sort(key=lambda c: -counts.get(c.id, 0))
    todo = todo[:limit]
    urls = {}
    for c in todo:
        urls[c.id] = session.scalar(select(Job.url).where(Job.company_id == c.id, Job.closed_at.is_(None)).limit(1))
    found = asyncio.run(_name_all([(c, urls[c.id]) for c in todo], concurrency))
    renamed = capitalised = jobs = 0
    examples = []
    for c in todo:
        new = found.get(c.id) or fallback(c)
        if new and new != c.name:
            examples.append(f"{c.name} -> {new}")
            if not dry_run:
                jobs += rename(session, c, new)
            renamed += bool(found.get(c.id))
            capitalised += not found.get(c.id)
        if not dry_run:
            session.merge(ProbeCache(company_key=f"name:{c.id}", found=(new or "")[:200]))
            session.commit()
    return {"asked": len(todo), "renamed": renamed, "capitalised": capitalised, "jobs": jobs, "examples": examples[:20]}


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    ap = argparse.ArgumentParser(description="Real employer names for boards named after their slug.")
    ap.add_argument("--limit", type=int, default=300)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--concurrency", type=int, default=8)
    args = ap.parse_args()
    from app.db import SessionLocal

    with SessionLocal() as session:
        result = name_some(session, args.limit, args.dry_run, args.concurrency)
    for e in result.pop("examples"):
        print(" ", e)
    print(result)


if __name__ == "__main__":
    main()
