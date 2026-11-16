"""Limits for a shared site (app/ratelimit.py): API requests per visitor, sign-ups, resent confirmations and the
daily email cap. conftest.py turns the first three off; each test here turns its own on."""

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.ratelimit import SlidingWindow, limits
from tests.conftest import sign_up
from tests.test_admin import sent  # noqa: F401 (fixture: captures emails instead of sending)


@pytest.fixture(autouse=True)
def fresh_limits():
    limits.clear()
    yield
    limits.clear()


def test_the_window_counts_per_key_and_frees_up_over_time(monkeypatch):
    import app.ratelimit as rl

    clock = [1000.0]
    monkeypatch.setattr(rl.time, "monotonic", lambda: clock[0])
    w = SlidingWindow()
    assert [w.allow("a", 2, 60) for _ in range(3)] == [True, True, False]
    assert w.allow("b", 2, 60)  # another visitor has their own count
    assert w.retry_after("a", 60) == 61
    clock[0] += 61
    assert w.allow("a", 2, 60)
    assert w.allow("x", 0, 60) and w.allow("x", 0, 60)  # 0 = no limit


def test_api_requests_are_limited_per_visitor_but_status_never(session, monkeypatch):
    monkeypatch.setattr(settings, "api_requests_per_minute", 3)
    with TestClient(app) as client:
        sign_up(client)  # one request counted against the address (no cookie yet)
        codes = [client.get("/api/prefs").status_code for _ in range(4)]
        assert codes == [200, 200, 200, 429]
        r = client.get("/api/prefs")
        assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1 and "Too many requests" in r.json()["detail"]
        assert all(client.get("/api/status").status_code == 200 for _ in range(5))
    with TestClient(app) as other:  # a different visitor (no cookie): counted on their own address
        assert other.get("/api/auth/me").status_code == 401


def test_sign_ups_are_limited_per_address_and_overall(session, monkeypatch, sent):
    monkeypatch.setattr(settings, "signups_per_hour_per_client", 2)
    with TestClient(app) as client:
        for i in range(2):
            r = client.post("/api/auth/signup", json={"name": "A", "email": f"a{i}@example.test",
                                                       "password": "correct horse battery"})
            assert r.status_code == 200, r.text
            client.post("/api/auth/logout")
        r = client.post("/api/auth/signup", json={"name": "A", "email": "a9@example.test",
                                                   "password": "correct horse battery"})
        assert r.status_code == 429 and "Too many sign-ups" in r.json()["detail"]
    assert len(sent) == 2  # no welcome email for the refused one

    limits.clear()
    monkeypatch.setattr(settings, "signups_per_hour_per_client", 0)
    monkeypatch.setattr(settings, "signups_per_hour", 1)
    with TestClient(app) as client:
        assert client.post("/api/auth/signup", json={"name": "B", "email": "b1@example.test",
                                                      "password": "correct horse battery"}).status_code == 200
        assert client.post("/api/auth/signup", json={"name": "B", "email": "b2@example.test",
                                                      "password": "correct horse battery"}).status_code == 429


def test_confirmation_emails_are_limited_per_account(session, monkeypatch, sent):
    monkeypatch.setattr(settings, "verification_emails_per_hour", 2)
    with TestClient(app) as client:
        sign_up(client)
        codes = [client.post("/api/auth/resend-verification").status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_no_email_goes_out_past_the_daily_limit(session, monkeypatch):
    from app.activity import record
    from app.notify import email

    monkeypatch.setattr(settings, "smtp_host", "smtp.example.test")
    monkeypatch.setattr(settings, "email_daily_limit", 2)
    for _ in range(2):
        record("email_sent", None, "earlier email")

    class NoSMTP:
        def __init__(self, *a, **k):
            raise AssertionError("must not connect past the daily limit")

    monkeypatch.setattr(email.smtplib, "SMTP", NoSMTP)
    ok, why = email.send_email("x@example.test", "Hi", "text", "<p>html</p>")
    assert not ok and "emails for today" in why
    assert email.emails_sent_last_24h() == 2
