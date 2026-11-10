import os
from datetime import datetime, timezone

# Point the app at an in-memory database before anything imports app.db.
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["SCHEDULER_ENABLED"] = "false"
os.environ["SMTP_HOST"] = ""
os.environ["ADMIN_EMAIL"] = ""  # tests don't use the real .env's admin: the first account is the admin

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
