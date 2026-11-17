"""Find the Phenom careers sites of the biggest Workday employers (2026-11-17), and add them as sources in batches.

Workday's Cloudflare began refusing our client on 2026-11-16, and Workday holds about half the feed. Many big
Workday employers publish the same jobs on a Phenom careers site in front of their Workday board (CVS, O'Reilly,
Lowe's, TJX, Dollar Tree), which answers our honest FreshJobsBot User-Agent. This finds those sites:

- the Workday employers with the most open jobs (index-only counts per board, summed per employer name);
- for each, the usual careers hosts are tried (jobs. / careers. / talent. + a brand label, at /us/en), one request
  at a time with FreshJobsBot's User-Agent;
- a site is kept only if it's built on Phenom (`phenom.PAGE_MARKER` and its `phApp.ddo` data), names the employer
  (`autodiscover.names_employer`), shows no bot challenge, lets FreshJobsBot use /widgets and /us/en/job/ in its
  robots.txt, and lists US jobs (one search for the country counts).

    PYTHONPATH=. .venv/bin/python scripts/find_phenom_sites.py find --top 300      # writes phenom_sites.json
    PYTHONPATH=. .venv/bin/python scripts/find_phenom_sites.py add --max 10 [--dry-run]   # next batch, most jobs first

`find` reads and saves results as it goes (next to the database), so a cut-off run continues. `add` skips sites
already tracked, so running it again adds the next batch.
"""

import argparse
import asyncio
import json
import time
from pathlib import Path

import httpx
from sqlalchemy import select, text

from app.db import SessionLocal
from app.matching import _models_dir
from app.models import Company
from app.pipeline.autodiscover import brand_variants, names_employer
from app.sources import http_util, phenom

RESULTS = Path(_models_dir()).parent / "phenom_sites.json"
PREFIXES = ("jobs", "careers", "talent")
PAUSE_SECONDS = 1.0
# Cloudflare challenge pages only: "captcha" alone appears in every Phenom page (a reCAPTCHA script for forms).
_CHALLENGE = ("challenge-platform", "Just a moment", "cf-chl")


def biggest_workday_employers(session, top: int) -> list[tuple[str, int]]:
    boards = session.execute(select(Company.id, Company.name).where(Company.ats == "workday",
                                                                    Company.enabled.is_(True))).all()
    counts = dict(session.execute(text(
        "SELECT company_id, count(*) FROM jobs INDEXED BY ix_jobs_company_described WHERE closed_at IS NULL "
        "AND company_id IN (SELECT id FROM companies WHERE ats = 'workday' AND enabled = 1) GROUP BY company_id")).all())
    per_name: dict[str, int] = {}
    for cid, name in boards:
        per_name[name] = per_name.get(name, 0) + counts.get(cid, 0)
    return sorted(per_name.items(), key=lambda kv: -kv[1])[:top]


async def try_site(client, url: str, name: str) -> dict | None:
    try:
        resp = await client.get(url)
    except httpx.HTTPError:
        return None
    html = resp.text
    if resp.status_code != 200 or any(c in html[:50000] for c in _CHALLENGE):
        return None
    slug = phenom.slug_from_page(str(resp.url), html)
    if not slug or not names_employer(html, name):
        return None
    host, cc, lang = phenom.parse_slug(slug)
    for path in ("/widgets", f"/{cc}/{lang}/job/"):
        if not await http_util.robots_allows(client, f"https://{host}", path):
            return {"slug": slug, "skip": f"robots.txt disallows {path}"}
    try:
        _, _, aggs = await phenom.PhenomAdapter()._search(client, host, cc, lang, {}, 0, 1, ["country"])
    except (httpx.HTTPError, ValueError) as e:
        return {"slug": slug, "skip": f"search failed: {type(e).__name__}"}
    us = sum(int(v or 0) for k, v in (aggs.get("country") or {}).items() if k.strip().lower() in phenom.US_NAMES)
    return {"slug": slug, "us_jobs": us}


async def find(top: int) -> None:
    results = json.loads(RESULTS.read_text()) if RESULTS.exists() else {}
    with SessionLocal() as session:
        employers = biggest_workday_employers(session, top)
        tracked = {s.lower() for (s,) in session.execute(select(Company.slug).where(Company.ats == "phenom"))}
    async with http_util.client(timeout=20, follow_redirects=True,
                                headers={"User-Agent": http_util.bot_user_agent()}) as client:
        for i, (name, workday_jobs) in enumerate(employers):
            if name in results:
                continue
            found = None
            for label in brand_variants(name)[:2]:
                for prefix in PREFIXES:
                    found = await try_site(client, f"https://{prefix}.{label}.com/us/en", name)
                    await asyncio.sleep(PAUSE_SECONDS)
                    if found:
                        break
                if found:
                    break
            results[name] = {"workday_jobs": workday_jobs, **(found or {"slug": None})}
            if found and found["slug"].lower() in tracked:
                results[name]["skip"] = "already tracked"
            RESULTS.write_text(json.dumps(results, indent=1))
            print(f"{i + 1}/{len(employers)} {name}: {results[name]}", flush=True)
    usable = [v for v in results.values() if v.get("us_jobs")]
    print(f"done: {len(usable)} usable Phenom sites, {sum(v['us_jobs'] for v in usable):,} US jobs")


def add(max_add: int, dry_run: bool) -> None:
    results = json.loads(RESULTS.read_text())
    with SessionLocal() as session:
        tracked = {s.lower() for (s,) in session.execute(select(Company.slug).where(Company.ats == "phenom"))}
        todo = sorted(((n, v) for n, v in results.items() if v.get("us_jobs") and v["slug"].lower() not in tracked),
                      key=lambda nv: -nv[1]["us_jobs"])[:max_add]
        for name, v in todo:
            print(f"{'would add' if dry_run else 'adding'} {name}: {v['slug']} ({v['us_jobs']:,} US jobs)")
            if not dry_run:
                session.add(Company(name=name, ats="phenom", slug=v["slug"], enabled=True))
        if not dry_run:
            session.commit()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("find")
    f.add_argument("--top", type=int, default=300)
    a = sub.add_parser("add")
    a.add_argument("--max", type=int, default=10)
    a.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if args.cmd == "find":
        t = time.time()
        asyncio.run(find(args.top))
        print(f"{time.time() - t:.0f} s")
    else:
        add(args.max, args.dry_run)


if __name__ == "__main__":
    main()
