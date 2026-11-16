from abc import ABC, abstractmethod
from collections.abc import Set
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx


@dataclass
class NormalizedJob:
    """The common job format every source adapter produces."""

    external_id: str
    title: str
    url: str
    locations: list[str] = field(default_factory=list)
    remote: bool = False
    description: str = ""  # plain text
    department: str = ""
    posted_at: datetime | None = None
    # The employer. Leave empty for single-company boards (the poller uses the source's name);
    # aggregators such as Adzuna must set it on every job.
    company_name: str = ""

    @property
    def location(self) -> str:
        return " | ".join(loc for loc in self.locations if loc)


class Adapter(ABC):
    ats: str
    # True if a full fetch lists every open job, so a job missing from it can be closed.
    # Aggregators only return recent jobs, so they set this to False.
    complete_feed: bool = True
    # A feed that shows only the newest N jobs (JobDiva's 200): when a fetch hit the cap, jobs past it can't be
    # told apart from closed ones, so they age out after stale_days unseen, as aggregator jobs do.
    capped_ages_out: bool = False
    # True for sources listing many employers' jobs (set company_name on every job). Their employers and
    # job links are used to find company boards we don't track yet.
    aggregator: bool = False
    # Minimum minutes between polls of one source (rate limits / provider etiquette). 0 = every poll.
    min_interval_minutes: int = 0
    # Polls between full scans. Adapters that can cheaply fetch just the newest jobs (e.g. Workday)
    # do a partial scan most polls and a full scan every `full_scan_every` polls.
    full_scan_every: int = 1

    # "slow" sources (one request per job, minutes per board) poll in their own lane so they never
    # delay the fast boards. See poller.poll_once(lane=...).
    lane: str = "fast"
    # Fetches of this ATS at once (None = settings.poll_concurrency). Low for providers that rate-limit per IP.
    max_concurrency: int | None = None

    def last_fetch_complete(self, slug: str) -> bool:
        """False if the last full fetch of this slug was truncated (e.g. a result cap), so nothing may be closed."""
        return True

    def is_configured(self) -> bool:
        """False if the adapter needs API keys that aren't set; it's then skipped."""
        return True

    @abstractmethod
    async def fetch(
        self, client: httpx.AsyncClient, slug: str, *, full: bool = True, known_ids: Set[str] = frozenset(),
        first_scan: bool = False,
    ) -> list[NormalizedJob]:
        """Return the source's current jobs.

        full: when False, the adapter may return only the newest jobs (the poller won't close anything).
        first_scan: the source has never been polled (its jobs will all be baseline).
        known_ids: external IDs already in the database. Adapters that need an extra request per job
        for its description should only make it for IDs not in this set.
        """


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def from_epoch_ms(value: int | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
