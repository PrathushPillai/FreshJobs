"""One-off, large-scale discovery of company job boards, to grow coverage the way hiring.cafe does.

    python -m app.pipeline.bulk_discovery hn --months 12      # boards linked from past HN "Who is hiring?" threads
    python -m app.pipeline.bulk_discovery websites            # boards behind hiring YC startups' own websites
    python -m app.pipeline.bulk_discovery commoncrawl         # every Greenhouse/Lever/Ashby/Workday board in Common Crawl
    python -m app.pipeline.bulk_discovery vc                  # portfolio companies of VCs whose job sites allow it
    python -m app.pipeline.bulk_discovery workday --top 5000  # Workday career sites of the biggest H-1B sponsors
    python -m app.pipeline.bulk_discovery fix-workday         # repair Workday sources named after a file (robots.txt)
    python -m app.pipeline.bulk_discovery ai-portfolios       # boards behind AI investors' portfolio company websites
    python -m app.pipeline.bulk_discovery niche --sector robotics|health|all [--dry-run]   # robotics and healthcare
    python -m app.pipeline.bulk_discovery products [--only vc|pages|lists] [--dry-run]   # product companies (pipeline/product_lists.py)

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
from urllib.parse import quote, urlparse

import httpx
from sqlalchemy import select

from app.db import SessionLocal, init_db
from app.models import Company
from app.pipeline.detect import detect_board as detect_board_url, detect_from_page
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
    return await _boards_behind(client, [(c["name"], c["website"]) for c in companies], "YC websites")


async def _boards_behind(client: httpx.AsyncClient, sites: list[tuple[str, str]], label: str, dry_run: bool = False) -> int:
    """Find and add the ATS board behind each (name, website): its /careers, /jobs, then home page. With
    dry_run, only counts the untracked boards found."""
    with SessionLocal() as session:
        tracked = {board_key(c.ats, c.slug) for c in session.scalars(select(Company))}
    sem = asyncio.Semaphore(8)
    links: list[tuple[str, str]] = []

    async def one(name: str, site: str) -> None:
        site = site.rstrip("/")
        async with sem:
            for url in (f"{site}/careers", f"{site}/jobs", site):
                try:
                    found = await detect_from_page(client, url)
                except (httpx.HTTPError, ValueError):
                    found = None
                if found:
                    if board_key(*found) not in tracked:
                        # harvest() maps URLs to boards; a synthetic board URL is the simplest input.
                        links.append((name, _board_url(*found)))
                    return

    await asyncio.gather(*(one(n, s) for n, s in sites))
    links = [link for link in links if link[1]]
    if dry_run:
        found = {board_key(*b) for b in (detect_board_url(u) for _, u in links) if b}
        log.info("%s (dry run): %d sites, %d untracked boards found, nothing saved", label, len(sites), len(found))
        return len(found)
    return _save(links, label)


# AI-focused investors' own portfolio pages (not their Getro/Consider job boards), whose robots.txt allows
# crawling and which link each company's website (checked Nov 2026), plus a curated list of AI startups.
AI_PORTFOLIO_PAGES = [
    "https://aigrant.com/", "https://www.airstreet.com/portfolio", "https://www.kleinerperkins.com/partnerships/",
    "https://www.khoslaventures.com/portfolio/", "https://www.gradient.com/portfolio/", "https://www.felicis.com/portfolio",
    "https://www.madrona.com/portfolio/", "https://www.conviction.com/",
    "https://raw.githubusercontent.com/vinitshahdeo/awesome-ai-startups-hiring/main/README.md",
]
_NOT_COMPANY = ("linkedin.com", "twitter.com", "x.com", "facebook.com", "instagram.com", "youtube.com", "github.com",
                "medium.com", "substack.com", "techcrunch.com", "bloomberg.com", "forbes.com", "wsj.com", "nytimes.com",
                "google.com", "apple.com", "crunchbase.com", "wikipedia.org", "shields.io", "notion.so", "bit.ly",
                "arxiv.org", "ros.org", "openrobotics.org", "readthedocs.io", "ieee.org", "gitlab.com", "bitbucket.org",
                "huggingface.co", "kaggle.com", "coursera.org", "udemy.com", "udacity.com", "edx.org", "reddit.com",
                "stackoverflow.com", "discord.gg", "discord.com", "slack.com", "t.co", "tiktok.com", "vimeo.com")


def company_sites(html_text: str, page_url: str) -> list[str]:
    """Websites a portfolio page links to: other hosts, one URL per domain, minus social and news sites."""
    own = (urlparse(page_url).hostname or "").removeprefix("www.")
    sites: dict[str, str] = {}
    for href in re.findall(r"""href=["'](https?://[^"'#?]+)|\]\((https?://[^)\s#?]+)\)""", html_text):
        url = href[0] or href[1]
        host = (urlparse(url).hostname or "").lower().removeprefix("www.")
        if not host or host == own or host.endswith("." + own) or any(host == d or host.endswith("." + d) for d in _NOT_COMPANY):
            continue
        if re.search(r"\.(edu|gov|mil)$|\.ac\.[a-z]{2}$|\.edu\.[a-z]{2}$", host) or host.startswith(("docs.", "wiki.", "blog.")):
            continue  # universities, labs, government and documentation sites: no company job board
        sites.setdefault(host, f"https://{host}")
    return list(sites.values())


async def from_ai_portfolios(client: httpx.AsyncClient) -> int:
    """Boards behind the websites of AI-investor portfolio companies (AI_PORTFOLIO_PAGES)."""
    sites: list[tuple[str, str]] = []
    for page in AI_PORTFOLIO_PAGES:
        try:
            r = await client.get(page)
        except httpx.HTTPError:
            continue
        if r.status_code == 200:
            found = company_sites(r.text, page)
            log.info("%s: %d company websites", page, len(found))
            sites += [(urlparse(s).hostname.split(".")[0].title(), s) for s in found]
    sites = list(dict((s, (n, s)) for n, s in sites).values())
    return await _boards_behind(client, sites, "AI portfolios")


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


# Robotics and healthcare: companies people rarely browse. Each page's robots.txt allowed it and it linked company
# websites in plain HTML when checked (Nov 2026); robots.txt is checked again on every run (`_robots_allow`).
# Left out: pages that refuse automated readers (HTTP 403: The Robot Report, Seedtable, Fundup, StartupSavant,
# Flare Capital, F-Prime, .406 Ventures, Echo Health Ventures), pages drawn by JavaScript (Eclipse, Root, DCVC,
# Rock Health, Define), and Getro/Consider job boards (see the module docstring).
NICHE_PAGES = {
    "robotics": [
        "https://raw.githubusercontent.com/vmayoral/ros-robotics-companies/master/README.md",
        "https://raw.githubusercontent.com/balloch/awesome-robotics-ai-companies/master/README.md",
        "https://raw.githubusercontent.com/mathiasmantelli/awesome-mobile-robotics/master/README.md",
        "https://techcrunch.com/2023/03/07/a-list-of-robotics-companies-that-are-hiring/",
        "https://techcrunch.com/2023/08/08/these-robotics-companies-are-hiring/",
        "https://techcrunch.com/2024/03/15/these-61-robotics-companies-are-hiring/",
        "https://techcrunch.com/2024/04/12/these-75-robotics-companies-are-hiring/",
        "https://techcrunch.com/2024/11/09/these-60-robotics-companies-are-hiring/",
        "https://playground.global/portfolio/",
    ],
    "health": [
        "https://www.optumventures.com/portfolio",
        "https://kpventures.com/portfolio/",
    ],
}
# Employer names that place an H-1B sponsor in the sector (DOL legal names: "Cerner Corporation", "Medtronic Inc").
NICHE_WORDS = {
    "robotics": re.compile(r"\b(robot\w*|automation|autonomous|autonomy|drones?|unmanned|mechatronics?|lidar)\b", re.I),
    "health": re.compile(r"\b(health\w*|medical|medicine|hospitals?|clinics?|clinical|pharma\w*|therapeutics?|"
                         r"diagnostics?|genomics?|surgical|dental|imaging|oncology|cardio\w*|biotech\w*|bio|biosciences?|"
                         r"life sciences?|care|medtech|telehealth|healthcare)\b", re.I),
}
IT_SOC = "15-%"  # computer and mathematical occupations: software, data, IT


async def _robots_allow(client: httpx.AsyncClient, url: str) -> bool:
    """Whether the site's robots.txt lets us read this page (no robots.txt, or an unreadable one, allows it)."""
    from urllib.robotparser import RobotFileParser

    parts = urlparse(url)
    rp = RobotFileParser()
    try:
        r = await client.get(f"{parts.scheme}://{parts.netloc}/robots.txt")
        rp.parse(r.text.splitlines() if r.status_code == 200 else [])
    except httpx.HTTPError:
        return True
    agent = str(client.headers.get("User-Agent", "*"))
    return rp.can_fetch(agent, url) and rp.can_fetch("*", url)


async def niche_sites(client: httpx.AsyncClient, sector: str) -> list[tuple[str, str]]:
    """(name, website) for every company the sector's list pages link to, one per domain."""
    sites: dict[str, tuple[str, str]] = {}
    for page in NICHE_PAGES[sector]:
        if not await _robots_allow(client, page):
            log.info("%s: robots.txt disallows it, skipped", page)
            continue
        try:
            r = await client.get(page)
        except httpx.HTTPError as e:
            log.info("%s: %s", page, type(e).__name__)
            continue
        if r.status_code != 200:
            log.info("%s: HTTP %d, skipped", page, r.status_code)
            continue
        found = company_sites(r.text, str(r.url))
        log.info("%s: %d company websites", page, len(found))
        for site in found:
            sites.setdefault(site, (urlparse(site).hostname.split(".")[0].title(), site))
    return list(sites.values())


def niche_sponsors(session, sector: str, limit: int) -> list[str]:
    """H-1B sponsors in the sector that filed for computer and data jobs (SOC 15-), most such filings first:
    companies that both use IT and sponsor, which the top-sponsor probes (by total filings) often miss."""
    from sqlalchemy import func

    from app.models import H1BRole, H1BSponsor

    rows = session.execute(
        select(H1BSponsor.name, func.sum(H1BRole.filings).label("it"))
        .join(H1BRole, H1BRole.name_key == H1BSponsor.name_key)
        .where(H1BRole.soc_code.like(IT_SOC))
        .group_by(H1BSponsor.name_key, H1BSponsor.name)
        .order_by(func.sum(H1BRole.filings).desc())
    )
    words = NICHE_WORDS[sector]
    return [name for name, _ in rows if words.search(name)][:limit]


async def from_niche(client: httpx.AsyncClient, sector: str, dry_run: bool = False, sponsors: int = 3000) -> int:
    """Robotics and/or healthcare boards: behind the websites on NICHE_PAGES, and for the sector's H-1B sponsors
    (Greenhouse, Lever and Ashby by name, then Workday). A dry run reads the lists and finds the boards behind the
    websites, but saves nothing and probes no names."""
    from app.pipeline.discovery import discover

    sectors = list(NICHE_PAGES) if sector == "all" else [sector]
    added = 0
    for sec in sectors:
        sites = await niche_sites(client, sec)
        added += await _boards_behind(client, sites, f"{sec} lists", dry_run=dry_run)
        with SessionLocal() as session:
            names = niche_sponsors(session, sec, sponsors)
        log.info("%s: %d H-1B sponsors with IT filings", sec, len(names))
        if dry_run:
            continue
        with SessionLocal() as session:
            found = await discover(session, client, names, limit=len(names))
        log.info("%s sponsors: %d Greenhouse/Lever/Ashby boards", sec, len(found))
        added += len(found) + await _workday_for(client, names)
    return added


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
    return await _workday_for(client, names)


async def _workday_for(client: httpx.AsyncClient, names: list[str]) -> int:
    """Probe each name for a Workday career site (results cached as wd:<key>) and add the ones found. Names
    already tracked or already probed are skipped."""
    from app.models import ProbeCache
    from app.pipeline.normalize import company_key

    with SessionLocal() as session:
        tracked = {company_key(c.name) for c in session.scalars(select(Company))}
        probed = set(session.scalars(select(ProbeCache.company_key).where(ProbeCache.company_key.like("wd:%"))))
    names = list(dict.fromkeys(n for n in names if company_key(n) and company_key(n) not in tracked
                               and f"wd:{company_key(n)}" not in probed))
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
        "workday": _workday_url(slug),
    }.get(ats, "")


def _workday_url(slug: str) -> str:
    from app.sources.workday import board_host

    parts = slug.split("|")
    return f"{board_host(parts[0], parts[1])}/{parts[2]}" if len(parts) == 3 else ""


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
    sub.add_parser("ai-portfolios")
    niche = sub.add_parser("niche")
    niche.add_argument("--sector", choices=["robotics", "health", "all"], default="all")
    niche.add_argument("--dry-run", action="store_true", help="find boards behind the list websites, save nothing")
    niche.add_argument("--sponsors", type=int, default=3000, help="how many of the sector's H-1B sponsors to probe")
    products = sub.add_parser("products", help="product companies from VC portfolios and curated GitHub lists")
    products.add_argument("--only", choices=["vc", "pages", "lists"])
    products.add_argument("--dry-run", action="store_true", help="find the boards, save nothing")
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
            if args.cmd == "ai-portfolios":
                return await from_ai_portfolios(client)
            if args.cmd == "niche":
                return await from_niche(client, args.sector, args.dry_run, args.sponsors)
            if args.cmd == "products":
                from app.pipeline.product_lists import from_products

                return await from_products(client, args.only, args.dry_run)
            return await from_commoncrawl(client, args.indexes, args.patterns)

    n = asyncio.run(run())
    print(f"Found {n} new boards (dry run: nothing saved)" if getattr(args, "dry_run", False) else f"Added {n} new boards")


if __name__ == "__main__":
    main()
