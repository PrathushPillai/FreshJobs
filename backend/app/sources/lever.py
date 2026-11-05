import httpx

from app.sources.base import Adapter, NormalizedJob, from_epoch_ms


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
                    description=(j.get("descriptionPlain") or "") + "\n" + (j.get("additionalPlain") or ""),
                    department=cats.get("team") or cats.get("department") or "",
                    posted_at=from_epoch_ms(j.get("createdAt")),
                )
            )
        return jobs
