"""Find the Phenom and Radancy careers sites of the biggest Workday employers, for adding as sources in batches.

Workday's Cloudflare began refusing our client on 2026-11-16, and Workday holds about half the feed. Many big
Workday employers publish the same jobs on a careers site in front of their Workday board, which answers our honest
FreshJobsBot User-Agent:
- Phenom (2026-11-17: CVS, O'Reilly, Lowe's, TJX, Dollar Tree...);
- Radancy / TalentBrew (2026-11-19: Radancy's Workday integration sends requisitions straight to the career sites
  it hosts, e.g. Allied Universal's jobs.aus.com).

For the Workday employers with the most open jobs (index-only counts per board, summed per employer name), the usual
careers hosts are tried (jobs. / careers. / talent. + a brand label), one request a second with FreshJobsBot's
User-Agent. A site is kept only if it's built on the platform, names the employer (`autodiscover.names_employer`),
shows no bot challenge, lets FreshJobsBot read its job list and job pages in robots.txt (Phenom: /widgets and
/us/en/job/; Radancy: /sitemap.xml and /job/), and lists US jobs.

    PYTHONPATH=. .venv/bin/python scripts/find_phenom_sites.py find --top 1300 [--platform phenom|radancy]
        # new finds go to {platform}_sites_review.json next to the database, for a person to look at first
    PYTHONPATH=. .venv/bin/python scripts/find_phenom_sites.py merge [--platform radancy]
        # moves them into {platform}_sites.json, which the hourly rollout (pipeline/phenom_rollout) adds from
    PYTHONPATH=. .venv/bin/python scripts/find_phenom_sites.py add --max 10 [--platform ...] [--dry-run]   # one batch

`find` saves as it goes, so a cut-off run continues, and skips employers already looked at, so a bigger --top only
checks the next ones.
"""

import argparse
import asyncio
import json
import time
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select, text

from app.db import SessionLocal
from app.models import Company
from app.pipeline.autodiscover import brand_variants, names_employer
from app.pipeline.phenom_rollout import PLATFORMS, results_file
from app.sources import http_util, phenom, radancy

PREFIXES = {"phenom": ("jobs", "careers", "talent"), "radancy": ("jobs", "careers")}
PATHS = {"phenom": "/us/en", "radancy": "/"}
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


async def _page(client, url: str) -> httpx.Response | None:
    try:
        resp = await client.get(url)
    except httpx.HTTPError:
        return None
    if resp.status_code != 200 or any(c in resp.text[:50000] for c in _CHALLENGE):
        return None
    return resp


async def try_phenom(client, url: str, name: str) -> dict | None:
    resp = await _page(client, url)
    if resp is None:
        return None
    slug = phenom.slug_from_page(str(resp.url), resp.text)
    if not slug or not names_employer(resp.text, name):
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


async def try_radancy(client, url: str, name: str) -> dict | None:
    resp = await _page(client, url)
    if resp is None:
        return None
    slug = radancy.slug_from_page(str(resp.url), resp.text)
    if not slug or not names_employer(resp.text, name):
        return None
    base = radancy.base_url(slug)
    host, prefix = urlsplit(base).hostname, urlsplit(base).path.rstrip("/")
    for path in ("/sitemap.xml", f"{prefix}/job/"):
        if not await http_util.robots_allows(client, f"https://{host}", path):
            return {"slug": slug, "skip": f"robots.txt disallows {path}"}
    try:
        sitemap = await client.get(f"{base}/sitemap.xml")
    except httpx.HTTPError as e:
        return {"slug": slug, "skip": f"sitemap failed: {type(e).__name__}"}
    if "<urlset" not in sitemap.text[:2000]:
        return {"slug": slug, "skip": "no sitemap"}
    listed, places = radancy.parse_sitemap(sitemap.text)
    return {"slug": slug, "us_jobs": sum(1 for j in listed if radancy.maybe_us(j["city"], places))}


CHECKS = {"phenom": try_phenom, "radancy": try_radancy}


async def find(top: int, platform: str = "phenom", review: bool = True) -> None:
    main_file, out_file = results_file(platform), results_file(platform, review=review)
    main = json.loads(main_file.read_text()) if main_file.exists() else {}
    results = json.loads(out_file.read_text()) if out_file.exists() else {}
    with SessionLocal() as session:
        employers = biggest_workday_employers(session, top)
        tracked = {s.lower() for (s,) in session.execute(select(Company.slug).where(Company.ats == platform))}
    check = CHECKS[platform]
    async with http_util.client(timeout=20, follow_redirects=True,
                                headers={"User-Agent": http_util.bot_user_agent()}) as client:
        for i, (name, workday_jobs) in enumerate(employers):
            if name in results or name in main:
                continue
            found = None
            for label in brand_variants(name)[:2]:
                for prefix in PREFIXES[platform]:
                    found = await check(client, f"https://{prefix}.{label}.com{PATHS[platform]}", name)
                    await asyncio.sleep(PAUSE_SECONDS)
                    if found:
                        break
                if found:
                    break
            results[name] = {"workday_jobs": workday_jobs, **(found or {"slug": None})}
            if found and found["slug"].lower() in tracked:
                results[name]["skip"] = "already tracked"
            out_file.write_text(json.dumps(results, indent=1))
            print(f"{i + 1}/{len(employers)} {name}: {results[name]}", flush=True)
    usable = [v for v in results.values() if v.get("us_jobs") and not v.get("skip")]
    print(f"done: {len(usable)} usable {platform} sites, {sum(v['us_jobs'] for v in usable):,} US jobs "
          f"(in {out_file.name})")


def merge(platform: str) -> None:
    """Move a reviewed find run's results into the file the hourly rollout reads."""
    review_file, main_file = results_file(platform, review=True), results_file(platform)
    found = json.loads(review_file.read_text())
    merged = {**(json.loads(main_file.read_text()) if main_file.exists() else {}), **found}
    main_file.write_text(json.dumps(merged, indent=1))
    review_file.unlink()
    print(f"merged {len(found)} employers into {main_file.name}")


def add(max_add: int, platform: str, dry_run: bool) -> None:
    """One batch by hand; the hourly upkeep does the same by itself (`app.pipeline.phenom_rollout`)."""
    from app.pipeline import phenom_rollout

    with SessionLocal() as session:
        print(phenom_rollout.step(session, max_add, dry_run, force=True, platform=platform))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("find")
    f.add_argument("--top", type=int, default=300)
    f.add_argument("--platform", choices=PLATFORMS, default="phenom")
    f.add_argument("--no-review", action="store_true", help="write straight to the file the rollout reads")
    m = sub.add_parser("merge")
    m.add_argument("--platform", choices=PLATFORMS, default="phenom")
    a = sub.add_parser("add")
    a.add_argument("--max", type=int, default=10)
    a.add_argument("--platform", choices=PLATFORMS, default="phenom")
    a.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if args.cmd == "find":
        t = time.time()
        asyncio.run(find(args.top, args.platform, review=not args.no_review))
        print(f"{time.time() - t:.0f} s")
    elif args.cmd == "merge":
        merge(args.platform)
    else:
        add(args.max, args.platform, args.dry_run)


if __name__ == "__main__":
    main()
