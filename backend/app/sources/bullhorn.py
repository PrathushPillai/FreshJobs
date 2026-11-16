"""Bullhorn career portals (Bullhorn's open-source "career-portal" app), common at US staffing firms.

slug: "{swimlane}:{corpToken}" or "{swimlane}:{corpToken}:{portal}", e.g. "91:1VCNF4:jobs.acme.com".
The portal part (host and path of the firm's career portal, no scheme) is only used to build job links
({portal}/#/jobs/{id}); without it, links point at the job's public REST record.

Verified live (Nov 2026):
- The portal (github.com/bullhorn/career-portal) reads its corpToken and swimlane from its public app.json
  and calls Bullhorn's anonymous public REST API, built for exactly this:
  https://public-rest{swimlane}.bullhornstaffing.com/rest-services/{corpToken}/search/JobOrder
  ?query=(isOpen:1) AND (isDeleted:0)&fields=...&count=..&start=..&sort=-dateLastPublished
  No login or key beyond the corp token the portal itself publishes; public-rest hosts have no robots.txt.
- One request returns up to 500 jobs with the HTML publicDescription, address (city, state, countryName),
  employmentType and dateLastPublished (epoch ms), so there are no per-job requests.
"""

import re

import httpx

from app.pipeline.normalize import html_to_text
from app.sources import http_util
from app.sources.base import Adapter, NormalizedJob, from_epoch_ms

PAGE_SIZE = 500
MAX_PAGES = 20
FIELDS = ("id,title,address(city,state,countryID,countryName),dateLastPublished,dateAdded,publicDescription,"
          "employmentType,publishedCategory(name)")
_DETECT = re.compile(r"public-rest(\d+)\.bullhornstaffing\.com(?::\d+)?/rest-services/([A-Za-z0-9]+)", re.I)
_TOKEN = re.compile(r'"corpToken"\s*:\s*"([A-Za-z0-9]+)"')
_SWIMLANE = re.compile(r'"swimlane"\s*:\s*"?(\d+)"?')

PAGE_MARKER = re.compile(r"bullhornstaffing\.com/rest-services|\"corpToken\"\s*:|career-portal", re.I)


def parse_slug(slug: str) -> tuple[str, str, str]:
    swimlane, _, rest = slug.strip().partition(":")
    token, _, portal = rest.partition(":")
    return swimlane, token, portal.strip().rstrip("/")


def api_base(swimlane: str, token: str) -> str:
    return f"https://public-rest{swimlane}.bullhornstaffing.com/rest-services/{token}"


def detect(url: str) -> str | None:
    m = _DETECT.search(url or "")
    return f"{m.group(1)}:{m.group(2)}" if m else None


def slug_from_page(url: str, html: str) -> str | None:
    """From a page (or a portal's app.json) that names the public REST URL or the corpToken + swimlane."""
    slug = detect(html)
    if not slug:
        token, swimlane = _TOKEN.search(html or ""), _SWIMLANE.search(html or "")
        if not (token and swimlane) or token.group(1).startswith("["):
            return None
        slug = f"{swimlane.group(1)}:{token.group(1)}"
    portal = re.sub(r"^https?://", "", (url or "").split("#")[0]).rstrip("/")
    portal = re.sub(r"/app\.json$", "", portal)
    return f"{slug}:{portal}" if portal else slug


def _place(address: dict) -> str:
    parts = [address.get("city"), address.get("state"), address.get("countryName")]
    return ", ".join(p.strip() for p in parts if isinstance(p, str) and p.strip())


class BullhornAdapter(Adapter):
    ats = "bullhorn"
    min_interval_minutes = 15
    max_concurrency = 4

    async def fetch(self, client: httpx.AsyncClient, slug: str, **_) -> list[NormalizedJob]:
        swimlane, token, portal = parse_slug(slug)
        base = api_base(swimlane, token)
        jobs: list[NormalizedJob] = []
        start, total = 0, None
        for _page in range(MAX_PAGES):
            data = (await http_util.get(client, f"{base}/search/JobOrder", params={
                "query": "(isOpen:1) AND (isDeleted:0)", "fields": FIELDS, "count": PAGE_SIZE, "start": start,
                "sort": "-dateLastPublished",
            })).json()
            rows = [r for r in data.get("data") or [] if isinstance(r, dict) and r.get("id") and r.get("title")]
            total = data.get("total") if total is None else total
            for r in rows:
                address = r.get("address") or {}
                country = (address.get("countryName") or "").strip()
                if country and country not in ("United States", "USA", "US"):
                    continue
                place = _place(address)
                job_id = str(r["id"])
                url = (f"https://{portal}/#/jobs/{job_id}" if portal
                       else f"{base}/query/JobBoardPost?where=(id={job_id})&fields=id,title,publicDescription")
                jobs.append(NormalizedJob(
                    external_id=job_id,
                    title=r["title"].strip(),
                    url=url,
                    locations=[place] if place else [],
                    remote=bool(re.search(r"\bremote\b", f"{r['title']} {place}", re.I)),
                    description=html_to_text(r.get("publicDescription") or ""),
                    department=(r.get("publishedCategory") or {}).get("name") or "",
                    posted_at=from_epoch_ms(r.get("dateLastPublished") or r.get("dateAdded")),
                ))
            start += len(rows)
            if not rows or (total is not None and start >= total):
                break
        return jobs
