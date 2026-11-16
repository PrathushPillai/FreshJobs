"""Always-on discovery: finds the job boards of every US employer we can name, a little at a time, forever.

Runs by itself every `autodiscover_minutes` (main.run_discovery), so coverage keeps growing without anyone
asking. Each run takes the next `autodiscover_per_run` employers we have never looked for, biggest first:

1. employers with H-1B filings for computer and data jobs (most IT filings first), then
2. every employer in the E-Verify list by workforce (10,000+ first, down to 100-499): about 160k employers
   of 100+ people, sponsors or not, so the feed covers all companies and the H-1B data stays a badge, then
3. the rest of the DOL H-1B employers.

For each employer it looks for a board three ways:
- **Its own website** (`find_site`): guesses the domain from the name (only if the page names the employer),
  opens its careers links, careers.<domain>, jobs.<domain> and /careers, and reads the board behind them
  with `detect_in_html`, which knows every ATS we support (adapters with a `PAGE_MARKER` included). A careers
  page with no ATS but schema.org JobPosting data becomes a `website` source.
- **Name probes** on Greenhouse, Lever and Ashby (`discovery.discover`), and Workday (`_workday_for`).
Every lookup is cached (`probe_cache`, `site:<key>`), so an employer is looked for once; `REVISIT_DAYS`
later it is looked for again, since companies change ATS.

robots.txt is honoured for every page read. Nothing here logs in, solves challenges or reads job sites
whose terms forbid it.

    python -m app.pipeline.autodiscover --names 300        # one run now (the backend also runs it by itself)
    python -m app.pipeline.autodiscover --status
"""

import argparse
import asyncio
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse

import httpx

from app.sources import http_util
from sqlalchemy import select, text

from app.config import settings
from app.db import SessionLocal, init_db
from app.h1b import SERVICES_NAME, name_tokens
from app.models import Company, ProbeCache
from app.pipeline.detect import detect_board, detect_in_html
from app.pipeline.harvest import board_key
from app.pipeline.normalize import company_key
from app.sources import ADAPTERS

log = logging.getLogger(__name__)

REVISIT_DAYS = 120
USER_AGENT = "Mozilla/5.0 (compatible; FreshJobs/1.0; job board discovery)"
EVERIFY_SIZES = ["10,000 and over", "5,000 to 9,999", "2,500 to 4,999", "1,000 to 2,499", "500 to 999", "100 to 499"]

# Words that say what kind of company it is, not which one: dropped when guessing a domain.
_NOT_BRAND = {
    "technologies", "technology", "tech", "solutions", "systems", "group", "global", "america", "americas",
    "consulting", "software", "north", "associates", "resources", "shared", "management", "holdings", "labs",
    "enterprises", "corporate", "international", "partners", "operations", "usa", "us", "the", "of", "and",
    "infotech", "it", "digital", "services", "worldwide", "national", "company", "incorporated",
}
_CAREER_LINK = re.compile(
    r"""<a\b[^>]*href\s*=\s*["']([^"'#]+)["'][^>]*>(.*?)</a>""", re.I | re.S)
_CAREER_WORDS = re.compile(r"career|jobs?\b|join (?:us|our team)|work (?:with|for) us|open (?:roles|positions)|"
                           r"opportunit|we.re hiring|current openings|search jobs|view (?:all )?(?:jobs|openings)", re.I)
_PARKED = re.compile(r"domain (?:is )?for sale|buy this domain|this domain may be for sale|parked free|"
                     r"godaddy\.com/domainsearch|sedo\.com|dan\.com", re.I)
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_JSON_LD_JOB = re.compile(r'"@type"\s*:\s*\[?\s*"JobPosting"', re.I)


def brand_variants(name: str) -> list[str]:
    """Domain labels to try for an employer name: "Fidelity Technology Group" -> fidelity, fidelitytechnology..."""
    tokens = list(name_tokens(name))
    core = [t for t in tokens if t not in _NOT_BRAND] or tokens
    out = []
    if core:
        out += ["".join(core[:2]), core[0], "".join(core), "-".join(core[:2])]
    if tokens:
        out.append("".join(tokens))
    return [v for v in dict.fromkeys(out) if v and 3 <= len(v) <= 40]


def names_employer(html: str, name: str) -> bool:
    """Does a home page plausibly belong to this employer (its title or text names the brand)?"""
    if _PARKED.search(html[:20000]):
        return False
    core = [t for t in name_tokens(name) if t not in _NOT_BRAND] or list(name_tokens(name))
    if not core:
        return False
    m = _TITLE.search(html)
    head = re.sub(r"[^a-z0-9]+", "", ((m.group(1) if m else "") + html[:30000]).lower())
    return core[0] in head or "".join(core[:2]) in head


def career_links(html: str, base: str) -> list[str]:
    """Links on a page that lead to its jobs: by their URL or their text (Careers, Join us, Open roles...)."""
    links = []
    for href, label in _CAREER_LINK.findall(html):
        label = re.sub(r"<[^>]+>", " ", label)
        if _CAREER_WORDS.search(href) or _CAREER_WORDS.search(label):
            url = urljoin(base, href.strip())
            if url.startswith("http") and not url.lower().endswith((".pdf", ".jpg", ".png")):
                links.append(url)
    return list(dict.fromkeys(links))[:6]


class Finder:
    """Reads a few pages per employer, honouring robots.txt (cached per host)."""

    def __init__(self, client: httpx.AsyncClient):
        self.client = client
        self._robots: dict[str, object] = {}

    async def allowed(self, url: str) -> bool:
        from urllib.robotparser import RobotFileParser

        parts = urlparse(url)
        host = f"{parts.scheme}://{parts.netloc}"
        rp = self._robots.get(host)
        if rp is None:
            rp = RobotFileParser()
            try:
                r = await self.client.get(f"{host}/robots.txt")
                rp.parse(r.text.splitlines() if r.status_code == 200 else [])
            except (httpx.HTTPError, ValueError):
                rp.parse([])
            self._robots[host] = rp
        return rp.can_fetch(USER_AGENT, url) and rp.can_fetch("*", url)

    async def get(self, url: str) -> httpx.Response | None:
        try:
            if not await self.allowed(url):
                return None
            r = await self.client.get(url)
        except (httpx.HTTPError, ValueError):
            return None
        return r if r.status_code < 400 and "html" in r.headers.get("content-type", "html") else None

    async def find_site(self, name: str) -> httpx.Response | None:
        for label in brand_variants(name):
            for url in (f"https://www.{label}.com", f"https://{label}.com"):
                r = await self.get(url)
                if r is not None and names_employer(r.text, name):
                    return r
        return None

    async def board_for(self, name: str) -> tuple[str, str] | None:
        """(ats, slug) of the employer's job board found from its own website, or None."""
        home = await self.find_site(name)
        if home is None:
            return None
        base = str(home.url)
        domain = (urlparse(base).hostname or "").removeprefix("www.")

        def ok(hit):
            return hit if hit and plausible_board(hit, name, domain) else None

        hit = ok(detect_board(base) or detect_in_html(home.text, base))
        if hit:
            return hit
        pages = career_links(home.text, base) + [f"https://careers.{domain}", f"https://jobs.{domain}",
                                                  urljoin(base, "/careers"), urljoin(base, "/jobs")]
        seen = set()
        for url in pages:
            if url in seen:
                continue
            seen.add(url)
            hit = ok(detect_board(url))
            if hit:
                return hit
            r = await self.get(url)
            if r is None:
                continue
            final = str(r.url)
            hit = ok(detect_board(final) or detect_in_html(r.text, final))
            if hit:
                return hit
            # One step further: a careers landing page's own "See open roles" link.
            for deeper in career_links(r.text, final)[:3]:
                if deeper in seen:
                    continue
                seen.add(deeper)
                hit = ok(detect_board(deeper))
                if hit:
                    return hit
                r2 = await self.get(deeper)
                if r2 is not None:
                    hit = ok(detect_board(str(r2.url)) or detect_in_html(r2.text, str(r2.url)))
                    if hit:
                        return hit
            if _JSON_LD_JOB.search(r.text):
                return "website", final
        return None


# A board slug that is a sign-in or members area, not a careers site (connect.secure.wellsfargo.com/auth).
_LOGIN = re.compile(r"(?:^|[/|.-])(?:auth|login|log-in|signin|sign-in|sso|members?|access|account|accounts|"
                    r"static|ui|portal-login|nxi|pmp|investor)(?:[/|.-]|$)")
_CAREERS_WORD = re.compile(r"career|job|talent|recruit|apply|hiring|work|join|opportunit|vacanc|employ", re.I)


def plausible_board(hit: tuple[str, str], name: str, home_domain: str) -> bool:
    """Whether a board found on an employer's website is really that employer's careers site.

    A page can link other companies' boards (a news story about Fortune's best workplaces) or a customer
    sign-in that looks like a platform page. So the board must name the employer (its slug holds a brand
    word) or sit on the employer's own domain, and a careers site on the employer's own host must say so
    (careers, jobs, talent...) and not be a login or members area.
    """
    ats, slug = hit
    if ats == "website":
        return True
    s = slug.lower()
    if _LOGIN.search(s):
        return False
    parts = [p for p in re.split(r"[/|]", s) if p]
    host = next((p for p in parts if "." in p), "")
    own = bool(host and home_domain and (host == home_domain or host.endswith("." + home_domain)))
    on_platform_host = host.endswith((".avature.net", ".icims.com", ".myworkdayjobs.com", ".taleo.net",
                                      ".eightfold.ai", ".oraclecloud.com", ".successfactors.com"))
    # (Eightfold's slug carries the employer's plain domain as a parameter, not as the careers site.)
    if host and ats != "eightfold" and not on_platform_host and not _CAREERS_WORD.search(s):
        return False  # the employer's (or someone's) own host, but not a careers site
    if own:
        return True
    compact = re.sub(r"[^a-z0-9]", "", s)
    words = set(re.split(r"[^a-z0-9]+", s))
    for b in brand_variants(name):
        if (len(b) >= 4 and b in compact) or b in words:
            return True
    core = [t for t in name_tokens(name) if t not in _NOT_BRAND]
    return bool(core) and len(core[0]) >= 2 and core[0] in words


def next_names(session, limit: int) -> list[str]:
    """The next employers to look for: never looked for (or not for REVISIT_DAYS), not tracked, biggest first."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=REVISIT_DAYS)
    done = set()
    for key, at in session.execute(select(ProbeCache.company_key, ProbeCache.probed_at)
                                   .where(ProbeCache.company_key.like("site:%"))):
        if at is None or at > cutoff:
            done.add(key[5:])
    tracked = {company_key(n) for n in session.scalars(select(Company.name))}
    out: dict[str, str] = {}

    def take(rows) -> bool:
        for (name,) in rows:
            if SERVICES_NAME.search(name or ""):  # "... Consulting", "... Staffing": not what the user wants
                continue
            key = company_key(name or "")
            if key and key not in done and key not in tracked and key not in out:
                out[key] = name
                if len(out) >= limit:
                    return True
        return False

    # 1. Employers filing H-1Bs for computer and data jobs, most filings first. IT consultancies and staffing
    # firms (h1b.employer_kind) are left out: they top the H-1B lists, and the user wants companies hiring for
    # themselves.
    if take(session.execute(text(
            "SELECT max(s.name) FROM h1b_roles r JOIN h1b_sponsors s ON s.name_key = r.name_key "
            "WHERE r.soc_code LIKE '15-%' AND coalesce(s.kind, '') = '' "
            "GROUP BY r.name_key ORDER BY sum(r.filings) DESC"))):
        return list(out.values())
    # 2. Every employer of 100+ people in E-Verify, biggest first (open accounts only).
    for size in EVERIFY_SIZES:
        if take(session.execute(text("SELECT name FROM everify_accounts WHERE open > 0 AND workforce = :w"),
                                {"w": size})):
            return list(out.values())
    # 3. The remaining H-1B employers.
    take(session.execute(text("SELECT name FROM h1b_sponsors WHERE coalesce(kind, '') = '' ORDER BY filings DESC")))
    return list(out.values())


STATUS_FILE = "autodiscover.json"


def _status_path():
    from pathlib import Path

    from app.matching import _models_dir

    return Path(_models_dir()).parent / STATUS_FILE


def status() -> dict:
    try:
        return json.loads(_status_path().read_text())
    except (OSError, ValueError):
        return {}


def _write_status(update: dict) -> None:
    data = {**status(), **update}
    try:
        _status_path().write_text(json.dumps(data, indent=1))
    except OSError:
        log.warning("couldn't write %s", STATUS_FILE)


async def run_once(names: int | None = None, concurrency: int = 8) -> dict:
    """Look for the boards of the next `names` employers; add the ones found. Returns a summary."""
    from app.pipeline.bulk_discovery import _workday_for
    from app.pipeline.discovery import discover

    from app.pipeline.poller import is_offline

    names = names or settings.autodiscover_per_run
    started = time.monotonic()
    # Offline (e.g. just after waking): every lookup would fail and mark 150 employers "looked for, nothing
    # found" for REVISIT_DAYS. Skip the run instead.
    if await is_offline("getaddrinfo failed"):
        log.info("autodiscover: this machine is offline; skipping this run")
        return {"looked_for": 0, "added": 0, "offline": True}
    with SessionLocal() as session:
        todo = next_names(session, names)
        tracked = {board_key(c.ats, c.slug) for c in session.scalars(select(Company))}
    if not todo:
        return {"looked_for": 0, "added": 0}
    sem = asyncio.Semaphore(concurrency)
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"}
    async with http_util.client(timeout=15, follow_redirects=True, headers=headers) as client:
        finder = Finder(client)

        async def one(name: str):
            async with sem:
                try:
                    return name, await asyncio.wait_for(finder.board_for(name), timeout=90)
                except (asyncio.TimeoutError, Exception):  # one bad site never stops the run
                    log.debug("site lookup failed for %s", name, exc_info=True)
                    return name, None

        results = await asyncio.gather(*(one(n) for n in todo))
        added: list[str] = []
        by_ats: dict[str, int] = {}
        with SessionLocal() as session:
            for name, found in results:
                session.merge(ProbeCache(company_key=f"site:{company_key(name)}",
                                         probed_at=datetime.now(timezone.utc),
                                         found=f"{found[0]}/{found[1]}"[:200] if found else None))
                if found and found[0] in ADAPTERS and board_key(*found) not in tracked:
                    tracked.add(board_key(*found))
                    session.add(Company(name=name, ats=found[0], slug=found[1]))
                    added.append(f"{name} ({found[0]})")
                    by_ats[found[0]] = by_ats.get(found[0], 0) + 1
            session.commit()
        # Names whose website showed nothing: try the public ATS APIs by name too.
        missing = [n for n, f in results if not f]
        probed = 0
        with SessionLocal() as session:
            probed += len(await discover(session, client, missing, limit=len(missing)))
        probed += await _workday_for(client, missing)
    summary = {
        "finished_at": datetime.now(timezone.utc).isoformat(), "looked_for": len(todo),
        "added_from_websites": len(added), "added_by_name": probed, "by_ats": by_ats,
        "seconds": round(time.monotonic() - started), "examples": added[:15],
    }
    total = status().get("total_added", 0) + len(added) + probed
    _write_status({"last_run": summary, "total_added": total,
                   "total_looked_for": status().get("total_looked_for", 0) + len(todo)})
    log.info("autodiscover: looked for %d employers, added %d boards from websites (%s) and %d by name in %ds",
             len(todo), len(added), by_ats, probed, summary["seconds"])
    return summary


# Job-board URL patterns read from each new monthly Common Crawl index (besides bulk_discovery.CC_PATTERNS):
# every platform whose boards are recognisable from their URL alone.
CRAWL_EXTRA = ["*.icims.com", "*.bamboohr.com", "*.recruitee.com", "*.breezy.hr", "*.pinpointhq.com",
               "*.eightfold.ai", "*.taleo.net", "recruiting.ultipro.com/*", "recruiting2.ultipro.com/*",
               "jobs.jobvite.com/*", "*.zohorecruit.com", "*.avature.net", "jobs.dayforcehcm.com/*",
               "workforcenow.adp.com/*", "recruiting.paylocity.com/*", "*.paycomonline.net", "*.ceipal.com",
               "*.jobdiva.com", "*.applytojob.com"]


async def crawl_new_index() -> int:
    """Read the newest Common Crawl index once (a new one comes out about monthly): every job-board URL in it
    on a platform we support is added. Marked done in probe_cache as cc:<index>, also when it fails part-way,
    so a busy index server doesn't make every run retry it."""
    from app.pipeline.bulk_discovery import CC_INFO, CC_PATTERNS, _cc_get, from_commoncrawl

    async with http_util.client(timeout=60, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
        info = await _cc_get(client, CC_INFO, tries=2)
        if info is None:
            return 0
        newest = info.json()[0]["id"]
        with SessionLocal() as session:
            if session.get(ProbeCache, f"cc:{newest}"):
                return 0
            session.merge(ProbeCache(company_key=f"cc:{newest}", probed_at=datetime.now(timezone.utc), found="started"))
            session.commit()
        added = await from_commoncrawl(client, 1, patterns=CC_PATTERNS + CRAWL_EXTRA)
        with SessionLocal() as session:
            session.merge(ProbeCache(company_key=f"cc:{newest}", probed_at=datetime.now(timezone.utc),
                                     found=f"{added} boards"))
            session.commit()
    log.info("Common Crawl %s: %d new boards", newest, added)
    _write_status({"last_crawl": {"index": newest, "added": added, "at": datetime.now(timezone.utc).isoformat()},
                   "total_added": status().get("total_added", 0) + added})
    return added


JOBDIVA_INDEX = "https://www2.jobdiva.com/candidates/myjobs/sitemapindex.jsp?env=2"  # named in its robots.txt
_JOBDIVA_KEY = re.compile(r"[?&](?:amp;)?a=([a-z0-9]+)")
_RSS_TITLE = re.compile(r"<channel>\s*<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", re.S)


async def jobdiva_portals(every_days: int = 7) -> int:
    """Every JobDiva portal (mostly IT staffing and consulting firms) from the sitemap index its robots.txt
    names, re-read weekly. Each new portal is added under the firm name its job feed gives."""
    week = datetime.now(timezone.utc).strftime("%G-W%V") if every_days == 7 else datetime.now(timezone.utc).date().isoformat()
    with SessionLocal() as session:
        if session.get(ProbeCache, f"jobdiva-index:{week}"):
            return 0
        tracked = {c.slug.split("/")[-1] for c in session.scalars(select(Company).where(Company.ats == "jobdiva"))}
    async with http_util.client(timeout=30, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
        try:
            r = await client.get(JOBDIVA_INDEX)
            r.raise_for_status()
        except httpx.HTTPError:
            return 0
        keys = [k for k in dict.fromkeys(_JOBDIVA_KEY.findall(r.text)) if k not in tracked]
        sem = asyncio.Semaphore(4)

        async def name_of(key: str) -> str | None:
            async with sem:
                try:
                    f = await client.get("https://www2.jobdiva.com/candidates/myjobs/getjobsrssfeed.jsp",
                                         params={"a": key, "noofjobs": 1, "versionid": 2})
                except httpx.HTTPError:
                    return None
            m = _RSS_TITLE.search(f.text) if f.status_code == 200 else None
            return re.sub(r"\s+Jobs$", "", re.sub(r"\s+", " ", m.group(1)).strip()) if m else None

        names = await asyncio.gather(*(name_of(k) for k in keys))
    with SessionLocal() as session:
        added = 0
        for key, name in zip(keys, names):
            if name:
                session.add(Company(name=name[:200], ats="jobdiva", slug=f"www2/{key}"))
                added += 1
        session.merge(ProbeCache(company_key=f"jobdiva-index:{week}", probed_at=datetime.now(timezone.utc),
                                 found=f"{added} portals"))
        session.commit()
    log.info("JobDiva index: %d new portals", added)
    if added:
        _write_status({"total_added": status().get("total_added", 0) + added})
    return added


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--names", type=int, default=None)
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--crawl", action="store_true", help="read the newest Common Crawl index now")
    parser.add_argument("--jobdiva", action="store_true", help="add every JobDiva portal now")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if args.status:
        print(json.dumps(status(), indent=1))
        return
    init_db()
    if args.jobdiva:
        print(asyncio.run(jobdiva_portals(every_days=1)), "JobDiva portals added")
        return
    if args.crawl:
        print(asyncio.run(crawl_new_index()), "boards added from Common Crawl")
        return
    print(json.dumps(asyncio.run(run_once(args.names)), indent=1))


if __name__ == "__main__":
    main()
