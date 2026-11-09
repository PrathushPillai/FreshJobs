from datetime import date, datetime, timezone

from sqlalchemy import JSON, Boolean, Date, Float, ForeignKey, Index, Integer, LargeBinary, String, Text, UniqueConstraint
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
    fail_streak: Mapped[int] = mapped_column(Integer, default=0)  # failed polls in a row (poller.DEAD_BOARD_FAILS)

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
        # after a page, instead of sorting every open job (~800k) on each request. no_sponsor comes after the
        # sort column: no-sponsorship jobs are shown by default, and with it before the sort column the
        # walk only worked while they were hidden ("All jobs" took 2.7 s instead of 0.02 s).
        Index("ix_jobs_by_found_v2", "closed_at", "is_repost", "first_seen_at", "listed_at", "no_sponsor", "remote",
              "exp_level", "min_years", "h1b_filings", "role_match", "everify", "everify_text"),
        Index("ix_jobs_company_described", "company_id", "described", "closed_at"),
        # Keyed by id: a search that starts from its full-text matches checks each job's filters here
        # instead of reading the job's row (whose description comes before these columns).
        Index("ix_jobs_id_feed", "id", "closed_at", "is_repost", "no_sponsor", "listed_at", "first_seen_at", "remote",
              "exp_level", "min_years", "h1b_filings", "role_match"),
        # "Company A-Z" and "Most H-1B filings" sorts (each was a sort of every open job: 21 s and 4 s).
        Index("ix_jobs_by_company", "closed_at", "is_repost", "company_name", "first_seen_at", "listed_at", "no_sponsor",
              "remote", "exp_level", "min_years", "h1b_filings", "role_match"),
        Index("ix_jobs_by_sponsor", "closed_at", "is_repost", "h1b_filings", "first_seen_at", "listed_at", "no_sponsor",
              "remote", "exp_level", "min_years", "role_match"),
        Index("ix_jobs_by_listed_v2", "closed_at", "is_repost", "listed_at", "first_seen_at", "no_sponsor", "remote",
              "exp_level", "min_years", "h1b_filings", "role_match", "everify", "everify_text"),
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
    # What the description says about F-1 students (pipeline/sponsorship.student_visa_signal):
    # "welcome" (OPT/CPT/STEM OPT candidates accepted), "excluded" (OPT/CPT ruled out), or None (not mentioned).
    student_visa: Mapped[str | None] = mapped_column(String(10))
    everify_text: Mapped[bool] = mapped_column(Boolean, default=False)  # the description says the employer uses E-Verify
    # The description says it sponsors visas ("We do sponsor visas!"), and nowhere rules it out (sponsorship.says_sponsors).
    sponsors_text: Mapped[bool] = mapped_column(Boolean, default=False)
    # The employer in USCIS's E-Verify list (app/everify.py): "yes" (an account is enrolled), "ended" (all ended), None.
    everify: Mapped[str | None] = mapped_column(String(8))
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
    # "Jobs with a repost event": a range read here, not a scan of every event (1.9M, mostly "seen").
    __table_args__ = (Index("ix_job_events_kind_job", "kind", "job_id"),)

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


class UserProfile(Base):
    """The user's resume and what was read from it (single row for now; per user once logins exist).

    `version` goes up whenever anything that affects scores changes, so stale JobMatch rows are recomputed.
    """

    __tablename__ = "user_profile"

    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = mapped_column(String(300), default="")
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resume_text: Mapped[str] = mapped_column(Text, default="")
    skills: Mapped[list] = mapped_column(JSON, default=list)  # canonical names (app/matching.py SKILLS)
    years: Mapped[float | None] = mapped_column(Float)  # professional experience, editable
    degree: Mapped[str] = mapped_column(String(20), default="")  # "", "bachelors", "masters", "phd"
    target_roles: Mapped[list] = mapped_column(JSON, default=list)  # roles found in the resume (plus the prefs')
    needs_sponsorship: Mapped[bool] = mapped_column(Boolean, default=True)
    visa_status: Mapped[str] = mapped_column(String(30), default="")  # "f1_opt", "stem_opt", "h1b", "citizen_gc", ""
    opt_end: Mapped[date | None] = mapped_column(Date)
    locations: Mapped[list] = mapped_column(JSON, default=list)  # preferred US states or cities; empty = anywhere
    vector: Mapped[bytes | None] = mapped_column(LargeBinary)  # the resume's embedding (float32)
    # The uploaded file itself, so a better reader can re-read it later without a new upload. Local only.
    resume_file: Mapped[bytes | None] = mapped_column(LargeBinary)
    version: Mapped[int] = mapped_column(Integer, default=0)


class JobVector(Base):
    """A job's embedding (title + the start of its description), computed once and reused for every profile."""

    __tablename__ = "job_vectors"

    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True)
    vector: Mapped[bytes] = mapped_column(LargeBinary)  # float32, normalized
    described: Mapped[bool] = mapped_column(Boolean, default=False)  # embedded with a description, not just the title


class JobMatch(Base):
    """How well the profile fits a job (app/matching.py score), for one profile version."""

    __tablename__ = "job_matches"
    __table_args__ = (Index("ix_job_matches_version_score", "profile_version", "score"),)

    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True)
    profile_version: Mapped[int] = mapped_column(Integer)
    score: Mapped[int] = mapped_column(Integer)
    detail: Mapped[dict] = mapped_column(JSON)  # parts, matched/missing skills, notes


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


class USCISH1B(Base):
    """An employer's H-1B petition decisions in one fiscal year, from USCIS's H-1B Employer Data Hub (app/uscis.py)."""

    __tablename__ = "uscis_h1b"

    id: Mapped[int] = mapped_column(primary_key=True)
    first_token: Mapped[str] = mapped_column(String(100), index=True)
    name_key: Mapped[str] = mapped_column(String(300))
    name: Mapped[str] = mapped_column(String(300))
    fiscal_year: Mapped[int] = mapped_column(Integer)
    new_approvals: Mapped[int] = mapped_column(Integer, default=0)  # new employment: workers new to H-1B (new grads)
    new_denials: Mapped[int] = mapped_column(Integer, default=0)
    approvals: Mapped[int] = mapped_column(Integer, default=0)  # every kind: new, continuing, transfers, amendments
    denials: Mapped[int] = mapped_column(Integer, default=0)


class EVerifyAccount(Base):
    """One E-Verify account from USCIS's E-Verify Employer Search export (see app/everify.py)."""

    __tablename__ = "everify_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    first_token: Mapped[str] = mapped_column(String(100), index=True)  # name_key's first word, for lookups
    name_key: Mapped[str] = mapped_column(String(300))  # h1b.name_tokens of the name, space-separated
    name: Mapped[str] = mapped_column(String(300))  # the enrolled name (or its DBA)
    open: Mapped[bool] = mapped_column(Boolean)  # enrolled now (status Open and no past termination date)
    enrolled: Mapped[date | None] = mapped_column(Date)
    terminated: Mapped[date | None] = mapped_column(Date)
    workforce: Mapped[str] = mapped_column(String(40), default="")  # "10,000 and over"


class H1BRole(Base):
    """One employer's certified H-1B LCAs for one occupation (SOC code), from the same DOL file."""

    __tablename__ = "h1b_roles"

    id: Mapped[int] = mapped_column(primary_key=True)
    name_key: Mapped[str] = mapped_column(String(300), index=True)  # H1BSponsor.name_key
    soc_code: Mapped[str] = mapped_column(String(20))  # "15-1252" (detail digits dropped)
    soc_title: Mapped[str] = mapped_column(String(200))  # "Software Developers"
    filings: Mapped[int] = mapped_column(Integer)
    median_wage: Mapped[int | None] = mapped_column(Integer)  # offered wage, annualized USD
    low_wage: Mapped[int | None] = mapped_column(Integer)  # 25th percentile
    high_wage: Mapped[int | None] = mapped_column(Integer)  # 75th percentile
    top_titles: Mapped[list] = mapped_column(JSON, default=list)  # the employer's own job titles, commonest first
    top_cities: Mapped[list] = mapped_column(JSON, default=list)  # "Seattle, WA"
