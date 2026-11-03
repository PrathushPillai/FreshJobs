"""Find a company's own job board (Greenhouse / Lever / Ashby) from just its name.

Aggregators like Adzuna tell us about employers we don't track yet; when we find their board we add it,
so future jobs come straight from the source, minutes after posting.

CLI:  python -m app.pipeline.discovery "Capital One" "Stripe" ...   (or --file names.txt)
"""

import argparse
import asyncio
import logging
import re

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Company, ProbeCache
from app.pipeline.normalize import company_key

log = logging.getLogger(__name__)


def slug_candidates(name: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", re.sub(r"\b(inc|llc|ltd|corp|corporation|co)\b\.?", " ", name.lower()))
    if not words:
        return []
    candidates = ["".join(words), "-".join(words)]
    if len(words) > 1:
        candidates.append(words[0])
    return list(dict.fromkeys(c for c in candidates if len(c) >= 3))


async def _greenhouse(client: httpx.AsyncClient, slug: str, name: str) -> bool:
    r = await client.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}")
    if r.status_code != 200:
        return False
    # Greenhouse returns the board's company name: make sure it's the same employer.
    return company_key(r.json().get("name", "")) == company_key(name)


async def _lever(client: httpx.AsyncClient, slug: str, name: str) -> bool:
    r = await client.get(f"https://api.lever.co/v0/postings/{slug}", params={"mode": "json", "limit": 1})
    return r.status_code == 200 and isinstance(r.json(), list) and len(r.json()) > 0 and slug == company_key(name)


async def _ashby(client: httpx.AsyncClient, slug: str, name: str) -> bool:
    r = await client.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
    return r.status_code == 200 and len(r.json().get("jobs", [])) > 0 and slug.replace("-", "") == company_key(name)


PROBES = {"greenhouse": _greenhouse, "lever": _lever, "ashby": _ashby}


async def find_board(client: httpx.AsyncClient, name: str) -> tuple[str, str] | None:
    """Return (ats, slug) for the company's board, or None."""
    for slug in slug_candidates(name):
        for ats, probe in PROBES.items():
            try:
                if await probe(client, slug, name):
                    return ats, slug
            except (httpx.HTTPError, ValueError):
                continue
    return None


async def discover(session: Session, client: httpx.AsyncClient, names: list[str], limit: int) -> list[Company]:
    """Probe up to `limit` not-yet-probed employer names and add any boards found. Commits."""
    tracked = {company_key(c.name) for c in session.scalars(select(Company))}
    probed = set(session.scalars(select(ProbeCache.company_key)))
    todo: dict[str, str] = {}
    for name in names:
        key = company_key(name)
        if key and key not in tracked and key not in probed and key not in todo:
            todo[key] = name
        if len(todo) >= limit:
            break

    sem = asyncio.Semaphore(4)

    async def one(key: str, name: str):
        async with sem:
            return key, name, await find_board(client, name)

    added = []
    for key, name, found in await asyncio.gather(*(one(k, n) for k, n in todo.items())):
        session.add(ProbeCache(company_key=key, found=f"{found[0]}/{found[1]}" if found else None))
        if found and not session.scalar(select(Company).where(Company.ats == found[0], Company.slug == found[1])):
            company = Company(name=name, ats=found[0], slug=found[1])
            session.add(company)
            added.append(company)
            log.info("Discovered %s board for %s: %s", found[0], name, found[1])
    session.commit()
    return added


def main() -> None:
    from app.db import SessionLocal, init_db

    parser = argparse.ArgumentParser()
    parser.add_argument("names", nargs="*")
    parser.add_argument("--file", help="text file with one company name per line")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    names = list(args.names)
    if args.file:
        names += [line.strip() for line in open(args.file, encoding="utf-8") if line.strip()]
    init_db()

    async def run():
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            with SessionLocal() as session:
                added = await discover(session, client, names, limit=len(names))
        print(f"Added {len(added)} of {len(names)}: " + ", ".join(f"{c.name} ({c.ats}/{c.slug})" for c in added))

    asyncio.run(run())


if __name__ == "__main__":
    main()
