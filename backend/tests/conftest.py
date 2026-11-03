import os
from datetime import datetime, timezone

# Point the app at an in-memory database before anything imports app.db.
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["SCHEDULER_ENABLED"] = "false"
os.environ["SMTP_HOST"] = ""

import pytest
from sqlalchemy.pool import StaticPool

import app.db as db

db.engine = db.create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
db.SessionLocal.configure(bind=db.engine)


@pytest.fixture()
def session():
    db.Base.metadata.drop_all(db.engine)
    db.init_db()
    with db.SessionLocal() as s:
        yield s


@pytest.fixture()
def now():
    return datetime(2026, 11, 3, 15, 0, tzinfo=timezone.utc)
