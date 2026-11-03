"""Help find who's hiring for a job: LinkedIn/Google search links, plus Hunter.io email lookup (optional)."""

import logging
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlencode

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.models import RecruiterCache
from app.pipeline.normalize import company_key

log = logging.getLogger(__name__)

CACHE_DAYS = 30
_RECRUITING_WORDS = re.compile(r"recruit|talent|sourc|people|hr\b|human resources|hiring", re.I)


def core_title(title: str) -> str:
    """"Software Engineer, Agent Auth (Remote)" -> "Software Engineer"."""
    t = re.split(r"\s[-–—|]\s|,|\(|\[|:", title)[0]
    return re.sub(r"\s+", " ", t).strip() or title


def search_links(company: str, title: str) -> list[dict]:
    role = core_title(title)
    li_people = "https://www.linkedin.com/search/results/people/?"
    li_posts = "https://www.linkedin.com/search/results/content/?"
    google = "https://www.google.com/search?q="
    return [
        {"label": f"Recruiters at {company} (LinkedIn)",
         "url": li_people + urlencode({"keywords": f"recruiter {company}"})},
        {"label": f"Talent acquisition at {company} (LinkedIn)",
         "url": li_people + urlencode({"keywords": f"talent acquisition {company}"})},
        {"label": f"'{role}' hiring managers at {company} (LinkedIn)",
         "url": li_people + urlencode({"keywords": f"{role} manager {company}"})},
        {"label": f"Recent \"hiring\" posts for {role} at {company} (LinkedIn)",
         "url": li_posts + urlencode({"keywords": f'hiring "{role}" {company}', "sortBy": '"date_posted"'})},
        {"label": f"Recruiter profiles at {company} (Google)",
         "url": google + quote(f'site:linkedin.com/in ("recruiter" OR "talent acquisition") "{company}"')},
        {"label": f"Hiring posts for {role} at {company} (Google)",
         "url": google + quote(f'site:linkedin.com/posts "hiring" "{role}" "{company}"')},
    ]


def hunter_configured() -> bool:
    return bool(settings.hunter_api_key)


async def hunter_lookup(session: Session, company: str, refresh: bool = False) -> dict:
    """Company domain, email pattern and recruiting contacts from Hunter.io (cached for 30 days)."""
    key = company_key(company)
    cached = session.get(RecruiterCache, key)
    if cached and not refresh and cached.fetched_at > datetime.now(timezone.utc) - timedelta(days=CACHE_DAYS):
        return {**cached.data, "cached": True}
    if not hunter_configured():
        return {"error": "Add HUNTER_API_KEY to .env to look up emails."}

    params = {"company": company, "api_key": settings.hunter_api_key, "limit": 10}
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get("https://api.hunter.io/v2/domain-search", params={**params, "department": "hr"})
        if resp.status_code == 200 and not resp.json().get("data", {}).get("emails"):
            # No HR contacts listed: fall back to everyone (still useful for the email pattern).
            resp = await client.get("https://api.hunter.io/v2/domain-search", params=params)
    if resp.status_code != 200:
        errors = resp.json().get("errors", [{}]) if resp.headers.get("content-type", "").startswith("application/json") else [{}]
        detail = errors[0].get("details") or f"HTTP {resp.status_code}"
        log.warning("Hunter lookup for %s failed: %s", company, detail)
        return {"error": f"Hunter.io: {detail}"}

    d = resp.json()["data"]
    contacts = [
        {
            "name": " ".join(x for x in (e.get("first_name"), e.get("last_name")) if x) or None,
            "email": e["value"],
            "position": e.get("position"),
            "department": e.get("department"),
            "linkedin": e.get("linkedin"),
            "confidence": e.get("confidence"),
        }
        for e in d.get("emails", [])
    ]
    contacts.sort(key=lambda c: (not _RECRUITING_WORDS.search(f"{c['position'] or ''} {c['department'] or ''}"),
                                 -(c["confidence"] or 0)))
    data = {"domain": d.get("domain"), "pattern": d.get("pattern"), "organization": d.get("organization"),
            "contacts": contacts}
    if cached:
        cached.data, cached.fetched_at = data, datetime.now(timezone.utc)
    else:
        session.add(RecruiterCache(company_key=key, data=data))
    session.commit()
    return {**data, "cached": False}

