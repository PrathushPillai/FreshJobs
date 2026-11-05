"""One-off, large-scale discovery of company job boards, to grow coverage the way hiring.cafe does.

    python -m app.pipeline.bulk_discovery hn --months 12      # boards linked from past HN "Who is hiring?" threads
    python -m app.pipeline.bulk_discovery websites            # boards behind hiring YC startups' own websites
    python -m app.pipeline.bulk_discovery commoncrawl         # every Greenhouse/Lever/Ashby/Workday board in Common Crawl
    python -m app.pipeline.bulk_discovery vc                  # portfolio companies of VCs whose job sites allow it
    python -m app.pipeline.bulk_discovery workday --top 5000  # Workday career sites of the biggest H-1B sponsors
    python -m app.pipeline.bulk_discovery fix-workday         # repair Workday sources named after a file (robots.txt)

VC job sites: only ones whose robots.txt allows crawling and that don't ask scrapers to use a paid API
instead. Getro-hosted boards (General Catalyst, Accel...) ask for that, and Consider-hosted ones
(Sequoia, Lightspeed) need a CSRF token for their API, so neither is used.

Each finds ATS links, maps them to boards with detect_board / detect_from_page, and adds the ones we don't
track (pipeline/harvest.py). Boards that turn out empty or dead cost little: the poller checks them only
every `idle_poll_minutes`. The Common Crawl index server is often overloaded (HTTP 504), so it retries
and moves on to older monthly crawls.
"""

import argparse
import asyncio
import html
import json
import logging
import re
from urllib.parse import quote

import httpx
from sqlalchemy import select

from app.db import SessionLocal, init_db
from app.models import Company
from app.pipeline.detect import detect_from_page
from app.pipeline.harvest import board_key, harvest
from app.pipeline.startups import US_REGIONS, YC_HIRING

log = logging.getLogger(__name__)

HN_SEARCH = "https://hn.algolia.com/api/v1/search_by_date"
HN_ITEM = "https://hn.algolia.com/api/v1/items/{id}"
CC_INFO = "https://index.commoncrawl.org/collinfo.json"
CC_PATTERNS = ["boards.greenhouse.io/*", "job-boards.greenhouse.io/*", "jobs.lever.co/*", "jobs.ashbyhq.com/*",
               "*.myworkdayjobs.com", "apply.workable.com/*", "jobs.smartrecruiters.com/*", "ats.rippling.com/*",
               "jobs.gem.com/*", "jobs.polymer.co/*"]
_HREF = re.compile(r'href="([^"]+)"')


def _save(links: list[tuple[str, str]], label: str) -> int:
    with SessionLocal() as session:
        added = harvest(session, links, limit=100_000)
        session.commit()
    log.info("%s: %d links, %d new boards", label, len(links), len(added))
    return len(added)


async def from_hn(client: httpx.AsyncClient, months: int) -> int:
    resp = await client.get(HN_SEARCH, params={"tags": "story,author_whoishiring", "hitsPerPage": months * 3 + 3})
    resp.raise_for_status()
    stories = [h for h in resp.json()["hits"] if "who is hiring" in (h.get("title") or "").lower()][:months]
    links: list[tuple[str, str]] = []
    for story in stories:
        r = await client.get(HN_ITEM.format(id=story["objectID"]))
        if r.status_code != 200:
            continue
        for c in r.json().get("children") or []:
            text = c.get("text") or ""
            head = html.unescape(re.sub(r"<[^>]+>", " ", text.split("<p>", 1)[0]))
            # "Company | Role | ..." headers only; otherwise harvest() names the company from its board slug.
            company = head.split("|", 1)[0].strip() if "|" in head else ""
            company = company if len(company) <= 60 else ""
            links += [(company, html.unescape(u)) for u in _HREF.findall(text)]
        log.info("HN %s: %d links so far", story.get("title"), len(links))
    return _save(links, "HN archive")


async def from_websites(client: httpx.AsyncClient) -> int:
    """Find the ATS board behind each hiring YC startup's website (its home page, then /careers and /jobs)."""
    resp = await client.get(YC_HIRING)
    resp.raise_for_status()
    companies = [c for c in resp.json() if c.get("website") and US_REGIONS & set(c.get("regions") or [])]
    with SessionLocal() as session:
        tracked = {board_key(c.ats, c.slug) for c in session.scalars(select(Company))}
    sem = asyncio.Semaphore(8)
    links: list[tuple[str, str]] = []

    async def one(c: dict) -> None:
        site = c["website"].rstrip("/")
        async with sem:
            for url in (f"{site}/careers", f"{site}/jobs", site):
                try:
                    found = await detect_from_page(client, url)
                except (httpx.HTTPError, ValueError):
                    found = None
                if found:
                    if board_key(*found) not in tracked:
                        # harvest() maps URLs to boards; a synthetic board URL is the simplest input.
                        links.append((c["name"], _board_url(*found)))
                    return

    await asyncio.gather(*(one(c) for c in companies))
    return _save([link for link in links if link[1]], "YC websites")


# VC portfolio job sites whose sitemap lists their companies as /company/{slug} or /jobs/{slug}.
# Portfolio pages whose sitemaps robots.txt allows (checked Nov 2026). Pear lists its portfolio through
# WordPress's REST API instead (PEAR_COMPANIES).
VC_SITEMAPS = ["https://jobs.a16z.com/sitemap.xml", "https://www.generalcatalyst.com/sitemap.xml",
               "https://www.luxcapital.com/sitemap.xml", "https://foundersfund.com/company-sitemap.xml"]
PEAR_COMPANIES = "https://pear.vc/wp-json/wp/v2/companies"


async def from_vc(client: httpx.AsyncClient) -> int:
    """Probe Greenhouse/Lever/Ashby for every portfolio company listed in VC job-site sitemaps."""
    from app.pipeline.discovery import discover

    names: list[str] = []
    for url in VC_SITEMAPS:
        r = await client.get(url)
        if r.status_code == 200:
            names += [m.replace("-", " ") for m in re.findall(r"<loc>https?://[^<]+/(?:company|companies|jobs)/([^</]+)/?</loc>", r.text)]
    for page in range(1, 10):
        r = await client.get(PEAR_COMPANIES, params={"per_page": 100, "page": page})
        if r.status_code != 200 or not r.json():
            break
        names += [html.unescape(c.get("title", {}).get("rendered", "")) for c in r.json() if c.get("title")]
    with SessionLocal() as session:
        added = await discover(session, client, list(dict.fromkeys(names)), limit=len(names))
    log.info("VC portfolios: %d companies, %d new boards", len(names), len(added))
    return len(added)


# Workday hosts most tenants live on, and the site names companies most often give their career site.
WD_HOSTS = ["wd1", "wd5", "wd3", "wd12", "wd103", "wd501", "wd10", "wd108", "wd2", "wd105"]
WD_SITES = ["External", "Careers", "{T}Careers", "{T}_Careers", "ExternalCareers", "External_Careers",
            "{T}", "{T}External", "{T}ExternalCareers", "{T}_External", "External_Career_Site", "ExternalCareerSite",
            "external_experienced", "Search", "Global", "careers", "jobs", "{T}Jobs", "{N}", "{N}_Careers", "{N}External"]
_WD_BODY = {"limit": 1, "offset": 0, "searchText": "", "appliedFacets": {}}


async def find_workday(client: httpx.AsyncClient, name: str) -> tuple[str, str] | None:
    """('workday', 'tenant|wdN|Site') for a company name, or None.

    The career site's own JSON API answers 422 for a tenant that isn't on that host, 404
    ("Job_Posting_Site_ID") for a real tenant with the wrong site name, and 200 with jobs for the right
    one. So: find the tenant's host (a few requests), then try the common site names there.
    """
    from app.pipeline.discovery import slug_candidates

    for tenant in [c for c in slug_candidates(name) if "-" not in c][:2]:
        host = None
        for wd in WD_HOSTS:
            try:
                r = await client.post(f"https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/External/jobs", json=_WD_BODY)
            except httpx.HTTPError:
                continue
            if r.status_code == 200 and r.json().get("total"):
                return "workday", f"{tenant}|{wd}|External"
            if r.status_code == 404:
                host = wd
                break
        if host is None:
            continue
        found = await find_site(client, tenant, host, name, WD_SITES[1:])
        if found:
            return found
    return None


async def find_site(client: httpx.AsyncClient, tenant: str, host: str, name: str, sites=WD_SITES) -> tuple[str, str] | None:
    """The career site of a known Workday tenant, by trying the common site names."""
    # {T}: the tenant ("Nvidia"); {N}: the company's words joined by "_" ("Capital_One").
    words = [w.capitalize() for w in re.findall(r"[A-Za-z0-9]+", re.sub(r"\b(inc|llc|ltd|corp|corporation|co)\b\.?", " ", name, flags=re.I))]
    for site in dict.fromkeys(sites):
        site = site.replace("{T}", tenant.capitalize()).replace("{N}", "_".join(words) or tenant)
        try:
            r = await client.post(f"https://{tenant}.{host}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs", json=_WD_BODY)
        except httpx.HTTPError:
            continue
        if r.status_code == 200 and r.json().get("total"):
            return "workday", f"{tenant}|{host}|{site}"
    return None


async def fix_workday(client: httpx.AsyncClient) -> int:
    """Repair Workday sources whose site name is a file (tenant|wd1|robots.txt, from crawled URLs).

    Every poll of one fails, and with ~1,500 of them the slow lane never reached real boards. A tenant
    already tracked under a real site name just has the junk row switched off; otherwise its real site
    is looked for, and the row is renamed to it (or switched off if there is none).
    """
    with SessionLocal() as session:
        rows = session.scalars(select(Company).where(Company.ats == "workday")).all()
        real = {tuple(c.slug.split("|")[:2]) for c in rows if c.slug.count("|") == 2 and "." not in c.slug.split("|")[2]}
        junk = [c for c in rows if c.slug.count("|") == 2 and "." in c.slug.split("|")[2]]
        disabled = 0
        for c in junk:
            if tuple(c.slug.split("|")[:2]) in real:
                c.enabled, c.last_status = False, "disabled: not a career site (tenant tracked under its real site)"
                disabled += 1
        session.commit()
        todo = [(c.id, c.name, *c.slug.split("|")[:2]) for c in junk if c.enabled]
    log.info("Workday: %d junk sources, %d switched off (tenant already tracked), %d to look up", len(junk), disabled, len(todo))
    sem = asyncio.Semaphore(12)

    async def one(item):
        cid, name, tenant, host = item
        async with sem:
            return cid, await find_site(client, tenant, host, name)

    fixed = 0
    for i in range(0, len(todo), 200):
        results = await asyncio.gather(*(one(t) for t in todo[i : i + 200]))
        with SessionLocal() as session:
            taken = set(session.scalars(select(Company.slug).where(Company.ats == "workday")))
            for cid, found in results:
                c = session.get(Company, cid)
                if found and found[1] not in taken:
                    c.slug, c.last_status, c.last_polled_at = found[1], None, None
                    taken.add(found[1])
                    fixed += 1
                else:
                    c.enabled, c.last_status = False, "disabled: no Workday career site found"
            session.commit()
        log.info("Workday: looked up %d/%d, %d career sites found", min(i + 200, len(todo)), len(todo), fixed)
    return fixed


async def from_workday(client: httpx.AsyncClient, top: int) -> int:
    """Find Workday career sites for the biggest H-1B sponsors we don't track yet."""
    from app.models import H1BSponsor, ProbeCache
    from app.pipeline.normalize import company_key

    with SessionLocal() as session:
        tracked = {company_key(c.name) for c in session.scalars(select(Company))}
        probed = set(session.scalars(select(ProbeCache.company_key).where(ProbeCache.company_key.like("wd:%"))))
        names = [n for n in session.scalars(select(H1BSponsor.name).order_by(H1BSponsor.filings.desc()).limit(top))
                 if company_key(n) not in tracked and f"wd:{company_key(n)}" not in probed]
    sem = asyncio.Semaphore(12)
    added = 0

    async def one(name: str):
        async with sem:
            return name, await find_workday(client, name)

    for i in range(0, len(names), 200):  # batches, so progress is saved as it goes
        results = await asyncio.gather(*(one(n) for n in names[i : i + 200]))
        with SessionLocal() as session:
            for name, found in results:
                session.merge(ProbeCache(company_key=f"wd:{company_key(name)}", found=found[1] if found else None))
                if found and not session.scalar(select(Company).where(Company.ats == "workday", Company.slug == found[1])):
                    session.add(Company(name=name, ats="workday", slug=found[1]))
                    added += 1
                    log.info("Workday site for %s: %s", name, found[1])
            session.commit()
        log.info("Workday: probed %d/%d sponsors, %d sites found", min(i + 200, len(names)), len(names), added)
    return added


def _board_url(ats: str, slug: str) -> str:
    return {
        "greenhouse": f"https://boards.greenhouse.io/{slug}",
        "lever": f"https://jobs.lever.co/{slug}",
        "ashby": f"https://jobs.ashbyhq.com/{slug}",
        "workable": f"https://apply.workable.com/{slug}",
        "smartrecruiters": f"https://jobs.smartrecruiters.com/{slug}",
        "rippling": f"https://ats.rippling.com/{slug}/jobs",
        "bamboohr": f"https://{slug}.bamboohr.com/careers",
        "recruitee": f"https://{slug}.recruitee.com",
        "breezy": f"https://{slug}.breezy.hr",
        "pinpoint": f"https://{slug}.pinpointhq.com",
        "workday": "https://{0}.{1}.myworkdayjobs.com/{2}".format(*slug.split("|")) if slug.count("|") == 2 else "",
    }.get(ats, "")


async def _cc_get(client: httpx.AsyncClient, url: str, tries: int = 4) -> httpx.Response | None:
    for attempt in range(tries):
        try:
            r = await client.get(url, timeout=180)
            if r.status_code == 200:
                return r
            if r.status_code == 404:
                return None
        except httpx.HTTPError:
            pass
        await asyncio.sleep(10 * (attempt + 1))
    return None


async def from_commoncrawl(client: httpx.AsyncClient, indexes: int, patterns: list[str] | None = None) -> int:
    info = await _cc_get(client, CC_INFO)
    if info is None:
        log.warning("Common Crawl index server unreachable; try again later")
        return 0
    total = 0
    for index in [c["id"] for c in info.json()][:indexes]:
        for pattern in patterns or CC_PATTERNS:
            base = f"https://index.commoncrawl.org/{index}-index?url={quote(pattern)}&output=json&fl=url"
            meta = await _cc_get(client, base + "&showNumPages=true")
            if meta is None:
                log.warning("%s %s: index busy, skipped", index, pattern)
                continue
            pages = json.loads(meta.text).get("pages", 0)
            links = []
            for page in range(pages):
                r = await _cc_get(client, f"{base}&page={page}")
                if r is None:
                    continue
                for line in r.text.splitlines():
                    try:
                        links.append(("", json.loads(line)["url"]))
                    except (ValueError, KeyError):
                        continue
            total += _save(links, f"Common Crawl {index} {pattern}")
    return total


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    hn = sub.add_parser("hn")
    hn.add_argument("--months", type=int, default=12)
    sub.add_parser("websites")
    sub.add_parser("vc")
    wd = sub.add_parser("workday")
    wd.add_argument("--top", type=int, default=5000)
    sub.add_parser("fix-workday")
    cc = sub.add_parser("commoncrawl")
    cc.add_argument("--indexes", type=int, default=3, help="how many recent monthly crawls to search")
    cc.add_argument("--patterns", nargs="*", help="URL patterns to search (default: every CC_PATTERNS entry)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    init_db()

    async def run() -> int:
        headers = {"User-Agent": "FreshJobs personal job feed (board discovery)"}
        async with httpx.AsyncClient(timeout=30, follow_redirects=True, headers=headers) as client:
            if args.cmd == "hn":
                return await from_hn(client, args.months)
            if args.cmd == "websites":
                return await from_websites(client)
            if args.cmd == "vc":
                return await from_vc(client)
            if args.cmd == "workday":
                return await from_workday(client, args.top)
            if args.cmd == "fix-workday":
                return await fix_workday(client)
            return await from_commoncrawl(client, args.indexes, args.patterns)

    print(f"Added {asyncio.run(run())} new boards")


if __name__ == "__main__":
    main()
