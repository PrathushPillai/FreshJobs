"""ADP Workforce Now recruiting career centers
(workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?cid={cid}&ccId=...).

slug: the career center's cid (a GUID), optionally "{cid}|{ccId}" when ccId isn't the usual
"19000101_000001", e.g. "755a7d59-7228-4f58-8652-0928bf9894cc".

Verified live (Nov 2026). workforcenow.adp.com serves no robots.txt (the path redirects to ADP's
login page), so nothing is disallowed; the career center page itself reads this public JSON:
- GET /mascsr/default/careercenter/public/events/staffing/v1/job-requisitions
  ?cid=&ccId=&lang=en_US&locale=en_US&$top=20&$skip=N -> {"jobRequisitions": [{itemID,
  requisitionTitle, requisitionLocations[{address{cityName, countrySubdivisionLevel1{codeValue},
  countryCode}, nameCode{shortName}}], postDate, customFieldGroup{dateFields[PostingDate (UTC)],
  stringFields[ExternalJobID]}, workLevelCode, organizationalUnits}], "meta": {"totalNumber"}}.
  $skip is 1-based (skip=1 is the first job). No description in the list.
- GET .../job-requisitions/{itemID}?cid=&ccId=&lang=&locale= -> the same plus requisitionDescription
  (HTML), one request per new job.
A cid whose career center is switched off answers 500 {"developerMessage": "An internal server error
occurred..."}; that's reported as a failed fetch. ADP's other recruiting product (myjobs.adp.com,
ADP Recruiting Management) is a separate app and isn't covered.
"""

import asyncio
import re
from collections import Counter
from collections.abc import Set
from urllib.parse import parse_qs, urlparse

import httpx

from app.pipeline.normalize import html_to_text
from app.sources import http_util
from app.sources.base import Adapter, NormalizedJob, parse_iso
from app.sources.hr_common import outside_us

API = "https://workforcenow.adp.com/mascsr/default/careercenter/public/events/staffing/v1/job-requisitions"
BOARD = "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html"
DEFAULT_CCID = "19000101_000001"
PAGE_SIZE = 20
MAX_PAGES = 100
DETAIL_CONCURRENCY = 3
_GUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
_URL = re.compile(r"https?://workforcenow\.adp\.com/mascsr/default/mdf/recruitment/recruitment\.html\?[^\s\"'<>]*", re.I)
PAGE_MARKER = re.compile(r"workforcenow\.adp\.com/mascsr/default/mdf/recruitment/recruitment\.html\?[^\"'<>]*cid=" + _GUID, re.I)


def parse_slug(slug: str) -> tuple[str, str]:
    cid, _, ccid = slug.strip().partition("|")
    if not re.fullmatch(_GUID, cid.strip()):
        raise ValueError(f"ADP slug must be a career center cid (GUID), got {slug!r}")
    return cid.strip().lower(), ccid.strip() or DEFAULT_CCID


def detect(url: str) -> str | None:
    m = _URL.match(url.replace("&amp;", "&").replace("&#038;", "&").strip())
    if not m:
        return None
    query = parse_qs(urlparse(m.group(0)).query)
    cid = (query.get("cid") or [""])[0]
    if not re.fullmatch(_GUID, cid):
        return None
    ccid = (query.get("ccId") or [DEFAULT_CCID])[0]
    return cid.lower() if ccid == DEFAULT_CCID else f"{cid.lower()}|{ccid}"


def slug_from_page(url: str, html: str) -> str | None:
    text = html.replace("&amp;", "&").replace("&#038;", "&")
    found = Counter(s for s in (detect(m.group(0)) for m in _URL.finditer(text)) if s)
    return found.most_common(1)[0][0] if found else None


def _field(req: dict, kind: str, code: str, key: str) -> str:
    for f in (req.get("customFieldGroup") or {}).get(kind) or []:
        if ((f.get("nameCode") or {}).get("codeValue")) == code:
            return f.get(key) or ""
    return ""


def _locations(req: dict) -> list[str]:
    out = []
    for loc in req.get("requisitionLocations") or []:
        addr = loc.get("address") or {}
        parts = [addr.get("cityName"), (addr.get("countrySubdivisionLevel1") or {}).get("codeValue"), addr.get("countryCode")]
        text = ", ".join(p.strip() for p in parts if isinstance(p, str) and p.strip())
        text = text or ((loc.get("nameCode") or {}).get("shortName") or "").strip()
        if text and text not in out:
            out.append(text)
    return out


class ADPAdapter(Adapter):
    ats = "adp"
    min_interval_minutes = 30
    lane = "slow"
    max_concurrency = 4

    async def fetch(
        self, client: httpx.AsyncClient, slug: str, *, known_ids: Set[str] = frozenset(), **_
    ) -> list[NormalizedJob]:
        cid, ccid = parse_slug(slug)
        params = {"cid": cid, "ccId": ccid, "lang": "en_US", "locale": "en_US"}
        headers = {"Accept": "application/json"}
        listed: dict[str, dict] = {}
        total = None
        for page in range(MAX_PAGES):
            resp = await http_util.get(
                client, API, params={**params, "$top": PAGE_SIZE, "$skip": 1 + page * PAGE_SIZE}, headers=headers,
            )
            data = resp.json()
            total = (data.get("meta") or {}).get("totalNumber", total)
            fresh = [r for r in data.get("jobRequisitions") or [] if r.get("itemID") and r["itemID"] not in listed]
            if not fresh:
                break
            listed.update((r["itemID"], r) for r in fresh)
            if total is not None and len(listed) >= total:
                break

        sem = asyncio.Semaphore(DETAIL_CONCURRENCY)

        async def build(req: dict) -> NormalizedJob:
            item = req["itemID"]
            locations = _locations(req)
            job_id = _field(req, "stringFields", "ExternalJobID", "stringValue") or item
            job = NormalizedJob(
                external_id=item,
                title=(req.get("requisitionTitle") or "").strip(),
                url=f"{BOARD}?cid={cid}&ccId={ccid}&jobId={job_id}&lang=en_US",
                locations=locations,
                remote=any("remote" in loc.lower() for loc in locations),
                department=", ".join(
                    (u.get("nameCode") or {}).get("shortName") or "" for u in req.get("organizationalUnits") or []
                ).strip(", "),
                posted_at=parse_iso(_field(req, "dateFields", "PostingDate", "dateValue")) or parse_iso(req.get("postDate")),
            )
            if item not in known_ids:
                async with sem:
                    try:
                        resp = await http_util.get(client, f"{API}/{item}", params=params, headers=headers)
                        job.description = html_to_text(resp.json().get("requisitionDescription") or "")
                    except (httpx.HTTPError, ValueError):
                        pass  # listed without a description; enrichment retries later
            return job

        kept = [r for r in listed.values() if (r.get("requisitionTitle") or "").strip() and not outside_us(_locations(r))]
        return list(await asyncio.gather(*(build(r) for r in kept)))
