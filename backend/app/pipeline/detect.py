"""Map a careers URL to (ats, slug) for a registered adapter.

detect_board(url) works on the URL alone (an ATS board URL, or a big company's own careers site).
detect_from_page(client, url) also handles a company's own careers page (e.g. stripe.com/jobs): it
fetches the HTML and runs detect_board on the ATS links, iframes and scripts embedded in it.
"""

import html as htmllib
import re
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import httpx

# Paths on ATS hosts that aren't a board name.
_RESERVED = {"", "embed", "api", "v1", "jobs", "job", "careers", "static", "assets", "js", "css", "favicon.ico"}
_LOCALE = re.compile(r"^[a-z]{2}(?:-[A-Za-z]{2})?$")
_SUBDOMAIN_BOARDS = {".bamboohr.com": "bamboohr", ".recruitee.com": "recruitee", ".breezy.hr": "breezy",
                     ".pinpointhq.com": "pinpoint"}
_NOT_A_BOARD = {"www", "app", "api", "help", "support", "status", "blog", "marketing", "careers", "jobs"}


def _first_segment(path: str) -> str:
    parts = [p for p in path.split("/") if p]
    return unquote(parts[0]) if parts else ""


def detect_board(url: str) -> tuple[str, str] | None:
    """(ats, slug) for a careers URL, or None if it isn't a board we know."""
    url = (url or "").strip()
    if not url:
        return None
    if "://" not in url:
        url = "https://" + url.lstrip("/")
    try:
        parsed = urlparse(htmllib.unescape(url))
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    path = parsed.path or "/"
    query = parse_qs(parsed.query)
    seg = _first_segment(path)

    # --- Greenhouse: boards.greenhouse.io/x, job-boards.greenhouse.io/x, embed?for=x, boards-api ---
    if host.endswith("greenhouse.io"):
        if "for" in query and query["for"][0]:
            return "greenhouse", query["for"][0].strip().lower()
        if host.startswith("boards-api."):
            m = re.match(r"/v1/boards/([^/]+)", path)
            return ("greenhouse", m.group(1).lower()) if m else None
        if host.split(".")[0] in ("boards", "job-boards", "www", "app") and seg.lower() not in _RESERVED:
            return "greenhouse", seg.lower()
        return None

    if host in ("jobs.lever.co", "jobs.eu.lever.co") and seg.lower() not in _RESERVED:
        return "lever", seg
    if host == "api.lever.co":
        m = re.match(r"/v0/postings/([^/?]+)", path)
        return ("lever", m.group(1)) if m else None

    if host == "jobs.ashbyhq.com" and seg.lower() not in _RESERVED:
        return "ashby", seg

    # --- Workday: {tenant}.wd{N}.myworkdayjobs.com/[locale/]{site} ---
    m = re.match(r"^([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com$", host)
    if m:
        parts = [p for p in path.split("/") if p]
        if parts and parts[0] == "wday":  # the CXS API: /wday/cxs/{tenant}/{site}/...
            return ("workday", f"{m.group(1)}|{m.group(2)}|{parts[3]}") if len(parts) >= 4 else None
        parts = [p for p in parts if not _LOCALE.match(p)]
        # Site names never contain dots: robots.txt, llms.txt, sitemap.xml are files at the host's root.
        return ("workday", f"{m.group(1)}|{m.group(2)}|{parts[0]}") if parts and "." not in parts[0] else None

    if host in ("jobs.smartrecruiters.com", "careers.smartrecruiters.com") and seg.lower() not in _RESERVED:
        return "smartrecruiters", seg
    if host == "api.smartrecruiters.com":
        m = re.match(r"/v1/companies/([^/]+)", path)
        return ("smartrecruiters", m.group(1)) if m else None

    if host == "apply.workable.com":
        if seg.lower() == "api":  # /api/v1/widget/accounts/{x} or /api/v3/accounts/{x}/jobs
            m = re.search(r"/accounts/([^/]+)", path)
            return ("workable", m.group(1)) if m else None
        if seg.lower() not in _RESERVED and seg != "j":
            return "workable", seg

    # --- iCIMS: careers-{x}.icims.com (also other prefixes such as uscareers-{x}) ---
    m = re.match(r"^([a-z0-9]+)-([a-z0-9-]+)\.icims\.com$", host)
    if m:
        if "internal" in m.group(1) or m.group(1) == "www":  # employee-only portals
            return None
        return ("icims", m.group(2)) if m.group(1) == "careers" else ("icims", host)

    # --- Oracle Recruiting Cloud: {x}.fa.{region}.oraclecloud.com/hcmUI/CandidateExperience/.../sites/{site} ---
    if host.endswith(".oraclecloud.com") and ".fa." in f".{host}":
        m = re.search(r"/sites/([A-Za-z0-9_]+)", path)
        if m:
            return "oracle_hcm", f"{host}|{m.group(1)}"
        m = re.search(r"siteNumber=([A-Za-z0-9_]+)", unquote(parsed.query + path))
        return ("oracle_hcm", f"{host}|{m.group(1)}") if m else None

    # --- Eightfold: {tenant}.eightfold.ai (the domain comes from ?domain=, else the tenant + .com) ---
    m = re.match(r"^([a-z0-9-]+)\.eightfold\.ai$", host)
    if m and m.group(1) not in ("www", "app", "static"):
        domain = (query.get("domain") or [f"{m.group(1)}.com"])[0]
        return "eightfold", f"{m.group(1)}|{domain}"

    if host == "ats.rippling.com" and seg.lower() not in _RESERVED:
        return "rippling", seg
    if host == "api.rippling.com":
        m = re.search(r"/board/([^/]+)", path)
        return ("rippling", m.group(1)) if m else None

    # --- Boards on a per-company subdomain: {slug}.bamboohr.com, {slug}.recruitee.com, ... ---
    for suffix, ats in _SUBDOMAIN_BOARDS.items():
        if host.endswith(suffix):
            sub = host[: -len(suffix)]
            if sub and "." not in sub and sub not in _NOT_A_BOARD:
                return ats, sub
            return None

    # --- SuccessFactors: career{N}.successfactors.com/career?company=X is not supported (no public
    # listing), but RMK sites (jobs.x.com) are only recognisable from their page, see detect_from_page.

    # --- Big companies' own career sites ---
    if host.endswith("amazon.jobs"):
        return "amazon", "country=USA"
    if host in ("careers.microsoft.com", "jobs.careers.microsoft.com", "apply.careers.microsoft.com",
                "gcsservices.careers.microsoft.com"):
        return "microsoft", "United States"
    if host == "careers.google.com" or (host == "www.google.com" and path.startswith("/about/careers")):
        return "google", "location=United States"
    if host == "jobs.apple.com":
        return "apple", "postLocation-USA"
    # metacareers.com is deliberately unsupported: its robots.txt forbids automated collection without
    # written permission, and its GraphQL API needs per-session tokens.
    return None


# --- Page scanning -------------------------------------------------------------------------------

_URL_ATTR = re.compile(r"""(?:href|src|data-src|action|data-url)\s*=\s*["']([^"']+)["']""", re.I)
_BARE_URL = re.compile(
    r"""https?:\\?/\\?/[a-z0-9.-]*(?:greenhouse\.io|lever\.co|ashbyhq\.com|myworkdayjobs\.com|smartrecruiters\.com|"""
    r"""workable\.com|icims\.com|oraclecloud\.com|eightfold\.ai|rippling\.com|bamboohr\.com|recruitee\.com|"""
    r"""breezy\.hr|pinpointhq\.com)(?:\\/|[^"'\s<>)\\])*""",
    re.I,
)
# Script-config patterns: Greenhouse's embed script and Lever/Ashby widget configs.
_GH_EMBED = re.compile(r"greenhouse\.io/embed/job_board/js\?for=([A-Za-z0-9_-]+)", re.I)
_SF_RMK = re.compile(r"rmkcdn\.successfactors\.com|/platform/js/jquery/jquery|jobs2web|j2w\.", re.I)
_SF_UNIFY = re.compile(r"/services/recruiting/v1/jobs|unifiedStandardTitle|sfstd_", re.I)


def _candidates(html: str, base: str) -> list[str]:
    urls = [urljoin(base, htmllib.unescape(u)) for u in _URL_ATTR.findall(html)]
    urls += [u.replace("\\/", "/") for u in _BARE_URL.findall(html)]
    return urls


def detect_in_html(html: str, base: str) -> tuple[str, str] | None:
    """The most common board referenced in a page's HTML (links, iframes, scripts), or None."""
    m = _GH_EMBED.search(html)
    if m:
        return "greenhouse", m.group(1).lower()
    counts: dict[tuple[str, str], int] = {}
    for u in _candidates(html, base):
        hit = detect_board(u)
        if hit and urlparse(u).hostname != urlparse(base).hostname:
            counts[hit] = counts.get(hit, 0) + 1
    if counts:
        return max(counts.items(), key=lambda kv: kv[1])[0]
    host = urlparse(base).hostname or ""
    if host and (_SF_RMK.search(html) or _SF_UNIFY.search(html)) and "successfactors" not in host:
        return "successfactors", host
    return None


async def detect_from_page(client: httpx.AsyncClient, url: str) -> tuple[str, str] | None:
    """detect_board(url), or else fetch the page and look for an ATS board embedded in it.

    Follows redirects (a careers page that redirects to its ATS is detected from the final URL),
    then scans links, iframes and scripts. Returns None if nothing is found or the fetch fails.
    """
    hit = detect_board(url)
    if hit:
        return hit
    if "://" not in url:
        url = "https://" + url
    try:
        resp = await client.get(url, follow_redirects=True)
    except httpx.HTTPError:
        return None
    final = str(resp.url)
    hit = detect_board(final)
    if hit:
        return hit
    if resp.status_code >= 400:
        return None
    return detect_in_html(resp.text, final)
