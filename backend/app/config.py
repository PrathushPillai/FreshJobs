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
    poll_on_startup: bool = True
    scheduler_enabled: bool = True
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
    # Jobs per source per poll whose missing details are fetched again (poller.poll_once).
    enrich_per_poll: int = 200
    # Hours between syncs of hiring Y Combinator startups into sources (pipeline/startups.py). 0 = off.
    yc_sync_hours: int = 24

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


settings = Settings()
