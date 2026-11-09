import httpx

from app.pipeline.normalize import html_to_text
from app.sources.base import Adapter, NormalizedJob, from_epoch_ms


def lever_description(j: dict) -> str:
    """A Lever posting's full text: opening, description, every list ("Your mission", "Requirements"…) and
    the closing part. Some postings leave descriptionPlain empty and put everything in the lists, so
    reading only descriptionPlain saved 1,310 jobs with a blank description."""
    parts = [j.get("openingPlain") or "", j.get("descriptionPlain") or "", j.get("descriptionBodyPlain") or ""]
    for item in j.get("lists") or []:
        body = html_to_text(item.get("content") or "")
        if body.strip():
            parts.append(f"{(item.get('text') or '').strip()}\n{body}".strip())
    parts.append(j.get("additionalPlain") or "")
    seen, out = set(), []
    for part in (p.strip() for p in parts):
        if part and part not in seen:  # openingPlain and descriptionPlain can repeat each other
            seen.add(part)
            out.append(part)
    return "\n\n".join(out)


class LeverAdapter(Adapter):
    ats = "lever"
    max_concurrency = 25  # public postings API, one request per board

    async def fetch(self, client: httpx.AsyncClient, slug: str, **_) -> list[NormalizedJob]:
        resp = await client.get(f"https://api.lever.co/v0/postings/{slug}", params={"mode": "json"})
        resp.raise_for_status()
        jobs = []
        for j in resp.json():
            cats = j.get("categories") or {}
            locations = cats.get("allLocations") or ([cats["location"]] if cats.get("location") else [])
            workplace = (j.get("workplaceType") or "").lower()
            jobs.append(
                NormalizedJob(
                    external_id=j["id"],
                    title=j.get("text", "").strip(),
                    url=j.get("hostedUrl", ""),
                    locations=locations,
                    remote=workplace == "remote" or any("remote" in loc.lower() for loc in locations),
                    description=lever_description(j),
                    department=cats.get("team") or cats.get("department") or "",
                    posted_at=from_epoch_ms(j.get("createdAt")),
                )
            )
        return jobs
