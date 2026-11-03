"""Microsoft careers (apply.careers.microsoft.com).

Microsoft moved off its old gcsservices.careers.microsoft.com search API (its certificate no longer
even matches) to an Eightfold "PCSX" site in 2026, so this is the Eightfold adapter pinned to
Microsoft's host and domain.

slug: a location filter as typed in the site's search box, e.g. "United States"; "" or "all" for every job.
~1,200 US jobs (Nov 2026): a full scan is ~120 list requests of 10 jobs, plus one detail per new job.
"""

from app.sources.eightfold import EightfoldAdapter

HOST = "apply.careers.microsoft.com"
DOMAIN = "microsoft.com"


class MicrosoftAdapter(EightfoldAdapter):
    ats = "microsoft"
    min_interval_minutes = 15
    full_scan_every = 12  # every ~3 hours
    lane = "slow"

    def _resolve(self, slug: str) -> tuple[str, str, str]:
        location = (slug or "").strip()
        return HOST, DOMAIN, "" if location.lower() == "all" else location
