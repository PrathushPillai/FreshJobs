"""Remote OK API (aggregator, no key). Returns the ~100 newest remote jobs in one call.

slug: unused (use e.g. "all").

The first array element is a legal notice, not a job. Their terms require linking back to the
job's Remote OK URL and naming Remote OK as the source, so `url` is kept as given.
Some descriptions arrive double-encoded (UTF-8 read as Latin-1, e.g. "Â\\xa0"); we repair those.
"""

import httpx

from app.pipeline.normalize import html_to_text
from app.sources.base import Adapter, NormalizedJob, parse_iso

API = "https://remoteok.com/api"


def fix_mojibake(text: str) -> str:
    if not any(marker in text for marker in ("Ã", "Â", "â€")):
        return text
    try:
        return text.encode("cp1252").decode("utf-8")
    except UnicodeError:
        try:
            return text.encode("latin-1").decode("utf-8")
        except UnicodeError:
            return text


class RemoteOKAdapter(Adapter):
    ats = "remoteok"
    complete_feed = False
    aggregator = True
    min_interval_minutes = 60

    async def fetch(self, client: httpx.AsyncClient, slug: str, **_) -> list[NormalizedJob]:
        resp = await client.get(API)
        resp.raise_for_status()
        jobs = []
        for j in resp.json():
            if not isinstance(j, dict) or not j.get("id") or not j.get("position"):
                continue  # the leading legal/metadata element
            location = (j.get("location") or "").strip()
            jobs.append(
                NormalizedJob(
                    external_id=str(j["id"]),
                    title=fix_mojibake(j.get("position") or "").strip(),
                    url=j.get("url") or j.get("apply_url") or "",
                    locations=[location] if location else [],
                    remote=True,
                    description=html_to_text(fix_mojibake(j.get("description") or "")),
                    posted_at=parse_iso(j.get("date")),
                    company_name=fix_mojibake(j.get("company") or "").strip(),
                )
            )
        return jobs
