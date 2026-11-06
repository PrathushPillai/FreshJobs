from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy import DateTime as _DateTime
from sqlalchemy.types import TypeDecorator
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DateTime(TypeDecorator):
    """Stores UTC and always returns timezone-aware datetimes (SQLite drops the timezone otherwise)."""

    impl = _DateTime
    cache_ok = True

    def __init__(self, timezone: bool = True):
        super().__init__(timezone=timezone)

    def process_bind_param(self, value, dialect):
        if value is not None and value.tzinfo is not None:
            value = value.astimezone(timezone.utc)
        return value

    def process_result_value(self, value, dialect):
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value


class Company(Base):
    __tablename__ = "companies"
    __table_args__ = (UniqueConstraint("ats", "slug"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    ats: Mapped[str] = mapped_column(String(32))  # greenhouse | lever | ashby | ...
    slug: Mapped[str] = mapped_column(String(200))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_status: Mapped[str | None] = mapped_column(String(300))  # "ok (42 jobs)" or the error
    last_ok_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # last successful fetch
    poll_count: Mapped[int] = mapped_column(Integer, default=0)

    jobs: Mapped[list["Job"]] = relationship(back_populates="company")


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("company_id", "external_id"),
        # Covering index for the feed: filters and title search scan this instead of the wide rows
        # (descriptions make the table ~1 GB), which makes a search ~10x faster.
        Index("ix_jobs_feed_v2", "closed_at", "is_repost", "no_sponsor", "title", "company_name", "first_seen_at",
              "listed_at", "posted_at", "remote", "exp_level", "min_years", "h1b_filings", "role_match"),
        # The feed's two main sorts ("newest found", "most recently posted") read these in order and stop
        # after a page, instead of sorting every open job (~800k) on each request.
        Index("ix_jobs_by_found", "closed_at", "is_repost", "no_sponsor", "first_seen_at", "listed_at", "remote",
              "exp_level", "min_years", "h1b_filings", "role_match"),
        Index("ix_jobs_company_described", "company_id", "described", "closed_at"),
        # Keyed by id: a search that starts from its full-text matches checks each job's filters here
        # instead of reading the job's row (whose description comes before these columns).
        Index("ix_jobs_id_feed", "id", "closed_at", "is_repost", "no_sponsor", "listed_at", "first_seen_at", "remote",
              "exp_level", "min_years", "h1b_filings", "role_match"),
        Index("ix_jobs_by_listed", "closed_at", "is_repost", "no_sponsor", "listed_at", "first_seen_at", "remote",
              "exp_level", "min_years", "h1b_filings", "role_match"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    external_id: Mapped[str] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(String(400))
    # The employer. Equals company.name for single-company boards; aggregators (Adzuna, ...) set it per job.
    company_name: Mapped[str | None] = mapped_column(String(200), index=True)
    company_key: Mapped[str | None] = mapped_column(String(200), index=True)  # normalized, for cross-source dedupe
    location: Mapped[str] = mapped_column(String(400), default="")
    remote: Mapped[bool] = mapped_column(Boolean, default=False)
    url: Mapped[str] = mapped_column(String(1000))
    description: Mapped[str] = mapped_column(Text, default="")
    # Whether description is non-empty, so "which jobs still need details" never reads the descriptions
    # themselves (that took 3 minutes per poll). Set by poller.apply_flags; NULL until backfilled.
    described: Mapped[bool | None] = mapped_column(Boolean)
    department: Mapped[str] = mapped_column(String(200), default="")
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)  # as reported by the ATS

    # Our own timeline: first_seen_at is the "freshness" clock we trust.
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    # Best estimate of when the job went live (pipeline/normalize.listed_at): the source's timestamp when it
    # has one, our detection time when the source only gives a date. "Date posted" filters/sorts use this.
    listed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    # Set when listed_at is only a window start: the job went live between listed_at and listed_until
    # (caught after a gap in checking, e.g. the server was off). listed_earliest is the input to that.
    listed_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    listed_earliest: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Last time its apply page was opened to see if it's still open (pipeline/liveness.py).
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    missed_polls: Mapped[int] = mapped_column(Integer, default=0)

    # Derived flags
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    is_repost: Mapped[bool] = mapped_column(Boolean, default=False)
    repost_of_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id"))
    no_sponsor: Mapped[bool] = mapped_column(Boolean, default=False)
    us_eligible: Mapped[bool] = mapped_column(Boolean, default=True)
    role_match: Mapped[bool] = mapped_column(Boolean, default=False)
    seniority: Mapped[str] = mapped_column(String(32), default="unknown")  # from the title: entry | mid | senior | unknown
    min_years: Mapped[int | None] = mapped_column(Integer)  # "3+ years of experience" -> 3
    exp_level: Mapped[str | None] = mapped_column(String(16), index=True)  # entry (0-2) | mid (3-5) | senior (6+) | unknown
    alerted: Mapped[bool] = mapped_column(Boolean, default=False)
    # Certified H-1B filings by this employer in the imported DOL data (None = not computed yet).
    h1b_filings: Mapped[int | None] = mapped_column(Integer, index=True)
    # Found on the very first scan of a company, so it isn't "fresh" and doesn't trigger alerts.
    baseline: Mapped[bool] = mapped_column(Boolean, default=False)

    company: Mapped[Company] = relationship(back_populates="jobs")
    events: Mapped[list["JobEvent"]] = relationship(back_populates="job", order_by="JobEvent.at")
    places: Mapped[list["JobLocation"]] = relationship(back_populates="job", cascade="all, delete-orphan")
    application: Mapped["Application | None"] = relationship(back_populates="job", uselist=False)


class JobLocation(Base):
    """One parsed location of a job (a job can list several)."""

    __tablename__ = "job_locations"
    __table_args__ = (
        # Covering indexes for the feed's location filters: "is this job in the US?" per job, and
        # "which jobs are in Boston?" for a city or state (see main.list_jobs).
        Index("ix_job_locations_job_place", "job_id", "country", "state", "city"),
        Index("ix_job_locations_place_job", "country", "state", "city", "job_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)
    city: Mapped[str | None] = mapped_column(String(120), index=True)
    state: Mapped[str | None] = mapped_column(String(120), index=True)  # US: two-letter code
    country: Mapped[str | None] = mapped_column(String(80), index=True)

    job: Mapped[Job] = relationship(back_populates="places")


class JobEvent(Base):
    __tablename__ = "job_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32))  # seen | closed | reopened | repost
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    detail: Mapped[str] = mapped_column(String(300), default="")

    job: Mapped[Job] = relationship(back_populates="events")


class UserPrefs(Base):
    """Single-row table holding the user's filter settings."""

    __tablename__ = "user_prefs"

    id: Mapped[int] = mapped_column(primary_key=True)
    include_keywords: Mapped[list] = mapped_column(JSON)
    exclude_keywords: Mapped[list] = mapped_column(JSON)
    email_alerts: Mapped[bool] = mapped_column(Boolean, default=True)
    hide_no_sponsor: Mapped[bool] = mapped_column(Boolean, default=True)
    exclude_senior: Mapped[bool] = mapped_column(Boolean, default=True)


APPLICATION_STATUSES = ["saved", "applied", "assessment", "interviewing", "offer", "rejected", "withdrawn", "ghosted"]


class Application(Base):
    """A job the user is tracking. Copies the job's basics so it survives the job closing (or a manual entry)."""

    __tablename__ = "applications"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id"), unique=True)
    company: Mapped[str] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(String(400))
    url: Mapped[str] = mapped_column(String(1000), default="")
    location: Mapped[str] = mapped_column(String(400), default="")
    status: Mapped[str] = mapped_column(String(32), default="applied")
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str] = mapped_column(Text, default="")
    contact: Mapped[str] = mapped_column(String(400), default="")  # recruiter name / email / LinkedIn
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    job: Mapped[Job | None] = relationship(back_populates="application")


class RecruiterCache(Base):
    """Hunter.io lookups, cached per company to save the free monthly quota."""

    __tablename__ = "recruiter_cache"

    company_key: Mapped[str] = mapped_column(String(200), primary_key=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    data: Mapped[dict] = mapped_column(JSON)


class ProbeCache(Base):
    """Company names we've already tried to find ATS boards for (auto-discovery)."""

    __tablename__ = "probe_cache"

    company_key: Mapped[str] = mapped_column(String(200), primary_key=True)
    probed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    found: Mapped[str | None] = mapped_column(String(200))  # "greenhouse/slug" or None


class H1BSponsor(Base):
    """An employer's certified H-1B LCAs in the imported DOL disclosure file (see app/h1b.py)."""

    __tablename__ = "h1b_sponsors"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(300))
    name_key: Mapped[str] = mapped_column(String(300), index=True)  # normalized tokens, space-separated
    filings: Mapped[int] = mapped_column(Integer, index=True)
    top_titles: Mapped[list] = mapped_column(JSON, default=list)
    top_states: Mapped[list] = mapped_column(JSON, default=list)
    source: Mapped[str] = mapped_column(String(200), default="")  # file it came from
