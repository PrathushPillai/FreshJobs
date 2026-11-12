from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR.parent / ".env", extra="ignore")

    database_url: str = f"sqlite:///{(BACKEND_DIR / 'jobs.db').as_posix()}"
    companies_file: Path = BACKEND_DIR / "data" / "companies.yaml"

    # Polling
    poll_minutes: int = 5
    poll_concurrency: int = 10
    # Minutes a lane's poll may run before the sources it hasn't reached wait for the next one. The slow
    # lane has none: a big Workday or Amazon scan can take longer and must be allowed to finish.
    poll_deadline_minutes: dict[str, int] = {"fast": 8, "bulk": 30}
    poll_on_startup: bool = True
    scheduler_enabled: bool = True
    # Each polling lane (and discovery) in its own worker process (app/lanes.py), so they don't share one core
    # with the API under the GIL. false: every lane in a thread of the backend process, as before 2026-11-10.
    poll_workers: bool = True
    # A job missing from its feed this many polls in a row is marked closed.
    missed_polls_to_close: int = 2
    repost_window_days: int = 60
    # A newly listed job whose posted date is older than this is an old job re-listed, not a fresh one.
    resurfaced_days: int = 7
    # Aggregator jobs (which never "disappear" from a complete listing) close after this many days unseen.
    stale_days: int = 14
    # New employers from aggregators to probe for their own Greenhouse/Lever/Ashby board, per poll.
    discovery_per_poll: int = 15
    # New company boards to add per poll from aggregator job links (pipeline/harvest.py). 0 = off.
    harvest_per_poll: int = 200
    # Minutes between polls of a board that had no open jobs, or failed, last time.
    idle_poll_minutes: int = 180
    # Check boards that posted nothing lately less often (poller.activity_factor: x2 after a day, x4 after a week).
    # Off: every board at its normal pace. It was on while the server had 2 vCPUs, which couldn't check every
    # board every 5 minutes; switch it on again only if fast polls start cutting boards short (cut_short).
    activity_pacing: bool = False
    # A job found more than this long after the source's previous successful check gets a
    # "posted between" window instead of "posted when we found it" (date-only and undated sources).
    listed_exact_gap_minutes: int = 45
    # Jobs per source per poll whose missing details are fetched again (poller.poll_once).
    enrich_per_poll: int = 200
    # Jobs per fast poll whose apply page is opened to catch ones taken down (pipeline/liveness.py). 0 = off.
    verify_per_poll: int = 150
    # Resume matching (app/matching.py). Where the embedding model is kept: next to the database by default,
    # never under AppData (the Claude app redirects that folder; see the DATABASE_URL note in CLAUDE.md).
    models_dir: str = ""
    # CPU threads the embedding model may use, so scoring doesn't make the laptop sluggish.
    embed_threads: int = 2
    # Hours between syncs of hiring Y Combinator startups into sources (pipeline/startups.py). 0 = off.
    yc_sync_hours: int = 24
    # Hours between syncs of the community GitHub job lists into sources (pipeline/startup_lists.py). 0 = off.
    list_sync_hours: int = 12
    # Always-on board discovery (pipeline/autodiscover.py): employers looked for per run, and how often.
    autodiscover_per_run: int = 150
    autodiscover_minutes: int = 60

    # Email alerts (leave smtp_host empty to disable)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_starttls: bool = True
    alert_from: str = ""
    alert_to: str = ""

    # Aggregator job APIs. Adzuna needs a free key from https://developer.adzuna.com/
    adzuna_app_id: str = ""
    adzuna_app_key: str = ""
    adzuna_country: str = "us"

    # Google for Jobs (includes LinkedIn/Indeed/Glassdoor postings) via JSearch on RapidAPI.
    # Free key: https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch  (200 requests/month on the free plan,
    # so the default polls each saved search every 4 hours; lower it on a paid plan.)
    jsearch_api_key: str = ""
    jsearch_min_interval_minutes: int = 240

    # Recruiter email lookup. Free key from https://hunter.io/api-keys
    hunter_api_key: str = ""

    # Chatbot (phase 3)
    anthropic_api_key: str = ""
    chat_model: str = "claude-sonnet-5-5"

    cors_origins: list[str] = ["http://localhost:5173"]

    # Accounts (app/auth.py)
    public_url: str = "http://localhost:5173"  # where the site is opened: links in emails point here
    admin_email: str = ""  # this account is the admin; empty = the first account to sign up (fine locally only)
    secure_cookies: bool = False  # True behind HTTPS (the cloud server), so the login cookie is never sent over HTTP
    session_days: int = 30
    support_email: str = ""  # where Contact & support messages go; empty = ADMIN_EMAIL
    api_docs: bool = False  # serve /docs and /openapi.json (handy locally; off on the shared server)
    # Limits for a shared site (app/ratelimit.py); 0 turns one off (the tests do).
    api_requests_per_minute: int = 300  # per visitor (login cookie, else client address); /api/status is exempt
    signups_per_hour_per_client: int = 5
    signups_per_hour: int = 50  # all sign-ups together: each one sends a welcome email from the site's account
    verification_emails_per_hour: int = 3  # "Resend confirmation", per account
    email_daily_limit: int = 450  # emails sent in 24 h; Gmail allows 500 a day for a personal account


settings = Settings()
