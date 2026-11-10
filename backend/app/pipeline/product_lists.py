"""Product companies (not IT consultancies or staffing firms) from VC portfolios and curated GitHub lists.

    python -m app.pipeline.bulk_discovery products [--dry-run] [--only vc|pages|lists]

Researched 2026-11-10, each with its robots.txt checked (and checked again on every run, `_robots_allow`):

- VC portfolio sitemaps (`VC_SITEMAPS`: Sequoia, Accel, NEA, Bessemer, Index, Redpoint, Greylock; ~3,350 companies). Each company page links the company's own website
  in its server HTML; the link whose host matches the page's slug is taken (`site_for_page`), so news
  articles, CDNs and the VC's own Getro job board (jobs.bvp.com...) are never mistaken for it.
- Single-page portfolios (`PORTFOLIO_PAGES`: Initialized, CRV, SV Angel, Spark), every company website on one page.
- GitHub lists (`GITHUB_LISTS`): hiring-without-whiteboards (MIT; US or Remote rows only), remoteintech's
  remote-jobs (ISC; one file per company with `website:`/`careers_url:`, US or worldwide regions only),
  established-remote, awesome-oss-alternatives (MIT), easy-application (MIT).

Ruled out: Lightspeed and Battery (their sitemaps answer 403 to automated readers), Wikipedia's API and Wikidata (robots.txt disallows /w/, /api/, /sparql), SEC EDGAR (403 without a
contact email in the User-Agent), connor11528's CSVs (no licence, Diffbot-harvested, many recruiters), IVP and
Coatue/Thrive/Tiger (JavaScript-drawn or 404), Insight/Sapphire/Menlo (no per-company website links).

Every website then goes through bulk_discovery._boards_behind (its /careers, /jobs, then home page). Names that
read as services firms ("... Consulting", "... Staffing", h1b.SERVICES_NAME) are skipped.
"""

import asyncio
import logging
import re
from urllib.parse import urlparse

import httpx

from app.h1b import SERVICES_NAME

log = logging.getLogger(__name__)

# Sitemap (or sitemap index) -> the path segment that marks a company page.
VC_SITEMAPS = {
    "https://sequoiacap.com/sitemap.xml": "companies",
    "https://accel.com/sitemap.xml": "companies",
    "https://www.nea.com/sitemap.xml": "portfolio",
    "https://www.bvp.com/sitemap.xml": "portfolio",
    "https://www.indexventures.com/sitemap-companies.xml": "companies",
    "https://www.redpoint.com/sitemap.xml": "companies",
    "https://greylock.com/portfolio-sitemap.xml": "portfolio",
}
PORTFOLIO_PAGES = ["https://initialized.com/companies", "https://www.crv.com/companies",
                   "https://svangel.com/portfolio", "https://www.sparkcapital.com/companies"]
GITHUB_RAW = "https://raw.githubusercontent.com"
GITHUB_LISTS = {
    "hiring-without-whiteboards": f"{GITHUB_RAW}/poteto/hiring-without-whiteboards/main/README.md",
    "established-remote": f"{GITHUB_RAW}/yanirs/established-remote/master/README.md",
    "awesome-oss-alternatives": f"{GITHUB_RAW}/RunaCapital/awesome-oss-alternatives/main/README.md",
    "easy-application": f"{GITHUB_RAW}/j-delaney/easy-application/master/README.md",
}
REMOTEINTECH_TREE = "https://api.github.com/repos/remoteintech/remote-jobs/git/trees/main?recursive=1"
REMOTEINTECH_FILE = f"{GITHUB_RAW}/remoteintech/remote-jobs/main/" + "{path}"

# Hosts on VC company pages that are never the company: assets, CMSs, consent tools, press, app stores.
_NOT_SITE = re.compile(
    r"(^|[.])(fonts|cdn|static|assets|images|img|media|api|privacy|cookie|consent|jobs|careers|talent)[.]|"
    r"framer|sanity[.]io|transcend|gstatic|googleapis|cloudfront|wp[.]com|wordpress|hubspot|typekit|vimeo|"
    r"getro|consider[.]co|wellfound|angel[.]co|pitchbook|cbinsights|crunchbase|businesswire|prnewswire|"
    r"globenewswire|reuters|cnbc|venturebeat|axios|theinformation|fortune[.]com|apps[.]apple|play[.]google|"
    r"linkedin|twitter|x[.]com|facebook|instagram|youtube|github|medium[.]com|techcrunch|bloomberg|forbes|"
    r"wsj[.]com|nytimes|ft[.]com|substack|tiktok|threads[.]net|bsky|calendly|zoom[.]us|google[.]com", re.I)
_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")
_ABS_HREF = re.compile(r"""href=["'](https?://[^"'#?\s]+)""", re.I)
_MD_ROW = re.compile(r"^\s*[-*|]\s*\[([^\]]+)\]\((https?://[^)\s]+)\)\s*\|?\s*([^|]*)")
_US_PLACE = re.compile(r",\s*(A[LKZR]|C[AOT]|D[EC]|FL|GA|HI|I[ADLN]|K[SY]|LA|M[ADEINOST]|N[CDEHJMVY]|O[HKR]|PA|"
                       r"RI|S[CD]|T[NX]|UT|V[AT]|W[AIVY])(?![A-Za-z])|remote|united states|usa", re.I)


def _squash(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _label(host: str) -> str:
    """The brand part of a host: www.typesafe.ai -> typesafe, app.ramp.com -> ramp."""
    parts = host.lower().removeprefix("www.").split(".")
    return parts[-2] if len(parts) >= 2 else parts[0]


def site_for_page(html_text: str, page_url: str) -> str | None:
    """The company's own website on a VC company page: an outside link whose brand matches the page's slug
    (/companies/typesafe -> typesafe.ai), or None. Precision over recall: a wrong site wastes a lookup."""
    own = _label(urlparse(page_url).hostname or "")
    slug = _squash([p for p in urlparse(page_url).path.split("/") if p][-1] if urlparse(page_url).path.strip("/") else "")
    if len(slug) < 3:
        return None
    for url in _ABS_HREF.findall(html_text):
        host = (urlparse(url).hostname or "").lower()
        if not host or _label(host) == own or _NOT_SITE.search(host):
            continue
        brand = _label(host)
        if len(brand) >= 3 and (brand in slug or slug in brand):
            return f"https://{host.removeprefix('www.')}"
    return None


def company_pages(sitemap_xml: str, marker: str) -> tuple[list[str], list[str]]:
    """(company page URLs, nested sitemap URLs) in a sitemap: pages are /{marker}/{slug}."""
    pages, nested = [], []
    for loc in _LOC.findall(sitemap_xml):
        path = [p for p in urlparse(loc).path.split("/") if p]
        if loc.lower().endswith(".xml"):
            nested.append(loc)
        elif len(path) == 2 and path[0].lower() == marker:
            pages.append(loc)
    return pages, nested


def markdown_sites(md: str, us_only: bool = False) -> list[tuple[str, str]]:
    """(name, url) from markdown list or table rows `[Name](url) | place | ...`; with us_only, rows whose place
    column names a US state, the US or Remote."""
    out = []
    for line in md.splitlines():
        m = _MD_ROW.match(line) or re.search(r"\|\s*\[([^\]]+)\]\((https?://[^)\s]+)\)\s*\|?\s*([^|]*)", line)
        if not m:
            continue
        name, url, place = m.group(1).strip(), m.group(2), m.group(3)
        host = (urlparse(url).hostname or "").lower()
        if not host or "github.com" in host or "wikipedia.org" in host or "shields.io" in host:
            continue
        if us_only and not _US_PLACE.search(place or ""):
            continue
        out.append((name, url))
    return out


def remoteintech_site(md: str) -> tuple[str, str] | None:
    """(name, careers URL or website) from one remoteintech company file, if it hires in the US or worldwide."""
    front = md.split("---")[1] if md.startswith("---") else md
    fields = dict(re.findall(r"^(\w+):\s*['\"]?(.*?)['\"]?\s*$", front, re.M))
    region = fields.get("region", "").lower()
    if region and not any(w in region for w in ("us", "usa", "america", "worldwide", "global", "anywhere")):
        return None
    url = fields.get("careers_url") or fields.get("website")
    return (fields.get("title") or fields.get("name") or "", url) if url and url.startswith("http") else None


async def _get(client: httpx.AsyncClient, url: str) -> httpx.Response | None:
    from app.pipeline.bulk_discovery import _robots_allow

    if not await _robots_allow(client, url):
        log.info("%s: robots.txt disallows it, skipped", url)
        return None
    try:
        r = await client.get(url)
    except httpx.HTTPError as e:
        log.info("%s: %s", url, type(e).__name__)
        return None
    return r if r.status_code == 200 else None


async def vc_sites(client: httpx.AsyncClient, concurrency: int = 4) -> list[tuple[str, str]]:
    """(name, website) from every VC sitemap's company pages."""
    from app.pipeline.bulk_discovery import _robots_allow

    sem = asyncio.Semaphore(concurrency)
    out: list[tuple[str, str]] = []
    for sitemap, marker in VC_SITEMAPS.items():
        r = await _get(client, sitemap)
        if r is None:
            continue
        pages, nested = company_pages(r.text, marker)
        for sub in nested[:30]:  # a sitemap index: read its child sitemaps
            rs = await _get(client, sub)
            if rs is not None:
                pages += company_pages(rs.text, marker)[0]
        pages = list(dict.fromkeys(pages))
        if pages and not await _robots_allow(client, pages[0]):
            log.info("%s: robots.txt disallows its company pages", sitemap)
            continue

        async def one(page: str) -> None:
            async with sem:
                try:
                    rp = await client.get(page)
                except httpx.HTTPError:
                    return
                await asyncio.sleep(0.5)  # a polite pace for a VC's own site
            if rp.status_code == 200:
                site = site_for_page(rp.text, page)
                if site:
                    out.append(([p for p in urlparse(page).path.split("/") if p][-1].replace("-", " ").title(), site))

        before = len(out)
        await asyncio.gather(*(one(p) for p in pages))
        log.info("%s: %d company pages, %d websites", sitemap, len(pages), len(out) - before)
    return out


async def page_sites(client: httpx.AsyncClient) -> list[tuple[str, str]]:
    from app.pipeline.bulk_discovery import company_sites

    out = []
    for page in PORTFOLIO_PAGES:
        r = await _get(client, page)
        if r is not None:
            found = [s for s in company_sites(r.text, str(r.url)) if not _NOT_SITE.search(urlparse(s).hostname or "")]
            log.info("%s: %d company websites", page, len(found))
            out += [(_label(urlparse(s).hostname).title(), s) for s in found]
    return out


async def list_sites(client: httpx.AsyncClient) -> list[tuple[str, str]]:
    out = []
    for name, url in GITHUB_LISTS.items():
        r = await _get(client, url)
        if r is not None:
            found = markdown_sites(r.text, us_only=name == "hiring-without-whiteboards")
            log.info("%s: %d companies", name, len(found))
            out += found
    r = await _get(client, REMOTEINTECH_TREE)
    if r is not None:
        paths = [t["path"] for t in r.json().get("tree", [])
                 if t["path"].startswith("src/companies/") and t["path"].endswith(".md")]
        sem = asyncio.Semaphore(8)

        async def one(path: str):
            async with sem:
                try:
                    rf = await client.get(REMOTEINTECH_FILE.format(path=path))
                except httpx.HTTPError:
                    return None
            return remoteintech_site(rf.text) if rf.status_code == 200 else None

        found = [f for f in await asyncio.gather(*(one(p) for p in paths)) if f]
        log.info("remoteintech: %d company files, %d hiring in the US or worldwide", len(paths), len(found))
        out += [(n or _label(urlparse(u).hostname or "").title(), u) for n, u in found]
    return out


async def from_products(client: httpx.AsyncClient, only: str | None = None, dry_run: bool = False) -> int:
    """Find and add the boards of product companies from VC portfolios and curated lists."""
    from app.pipeline.bulk_discovery import _boards_behind

    sites: list[tuple[str, str]] = []
    if only in (None, "lists"):
        sites += await list_sites(client)
    if only in (None, "pages"):
        sites += await page_sites(client)
    if only in (None, "vc"):
        sites += await vc_sites(client)
    by_host: dict[str, tuple[str, str]] = {}
    for name, url in sites:
        if SERVICES_NAME.search(name or ""):
            continue
        host = (urlparse(url).hostname or "").lower().removeprefix("www.")
        if host:
            by_host.setdefault(host, (name, url.split("#")[0]))
    log.info("product lists: %d sites, %d distinct", len(sites), len(by_host))
    return await _boards_behind(client, list(by_host.values()), "product companies", dry_run=dry_run)
