import os
from datetime import datetime, timezone

# Point the app at an in-memory database before anything imports app.db.
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["SCHEDULER_ENABLED"] = "false"
os.environ["SMTP_HOST"] = ""
os.environ["ADMIN_EMAIL"] = ""  # tests don't use the real .env's admin: the first account is the admin
os.environ["GROQ_API_KEY"] = ""  # never call real AI APIs from tests (the visa reader's tests use fakes)
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["SECURE_COOKIES"] = "false"  # the server's .env sets it; the test client speaks plain http
os.environ["PUBLIC_URL"] = "http://localhost:5173"
os.environ["CORS_ORIGINS"] = '["http://localhost:5173"]'
# Request and sign-up limits off (tests make many requests and accounts from one client); test_ratelimit turns them on.
os.environ["API_REQUESTS_PER_MINUTE"] = "0"
os.environ["SIGNUPS_PER_HOUR_PER_CLIENT"] = "0"
os.environ["SIGNUPS_PER_HOUR"] = "0"
os.environ["VERIFICATION_EMAILS_PER_HOUR"] = "0"

import pytest
from sqlalchemy.pool import StaticPool

import app.db as db

db.engine = db.create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
db.SessionLocal.configure(bind=db.engine)


@pytest.fixture()
def session():
    db.Base.metadata.drop_all(db.engine)
    db.init_db()
    from app import everify, main

    main._facets.update(at=None, rows=[])  # the location counts snapshot would outlive the test's data
    from app.pipeline import liveness
    liveness._checked.clear()  # job ids are reused by the next test's data
    everify._status_cache.clear()  # employer -> E-Verify status, cached per process
    main._jobs_cache.clear()  # cached /api/jobs pages and header counts would outlive the test's data
    main._status_counts.clear()
    main._suggestion_cache.clear()
    main._open_counts.clear()
    from app import auth

    auth._failures.clear()  # failed-login counts would outlive the test
    with db.SessionLocal() as s:
        yield s


@pytest.fixture()
def now():
    return datetime(2026, 11, 3, 15, 0, tzinfo=timezone.utc)


def sign_up(client, email="me@example.test", password="correct horse battery", name="Me"):
    """Sign up and stay logged in (the cookie stays on the client). The first account is the admin and takes
    over the data from before accounts, as on a real upgrade."""
    r = client.post("/api/auth/signup", json={"name": name, "email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture(autouse=True)
def _workday_robots_allow(request, monkeypatch):
    """The robots.txt check of the Workday and Phenom readers is tested on its own (tests named *robots*); elsewhere the fake Workday servers
    have no robots.txt, so it allows everything."""
    if "robots" in request.node.name:
        return
    from app.sources import http_util, phenom, workday

    async def allow(*a, **k):
        return True

    monkeypatch.setattr(workday, "robots_allows", allow)
    monkeypatch.setattr(phenom, "robots_allows", allow)
    monkeypatch.setattr(http_util, "robots_allows", allow)  # Radancy calls it through the module
