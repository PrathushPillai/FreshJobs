"""The admin dashboard (app/admin.py), the activity log (app/activity.py), account deletion and Contact & support."""

import base64

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import sign_up


@pytest.fixture()
def sent(monkeypatch):
    """Every email the portal tries to send, captured (and logged as sent, as the real one is)."""
    from app.activity import record
    from app.notify import email

    box = []

    def fake_send(to, subject, text, html, headers=None, user_id=None):
        box.append({"to": to, "subject": subject, "text": text, "headers": headers or {}})
        record("email_sent", user_id, subject)
        return True, ""

    monkeypatch.setattr(email.settings, "smtp_host", "localhost")
    monkeypatch.setattr(email, "send_email", fake_send)
    return box


def test_only_the_admin_sees_the_dashboard(session, sent):
    with TestClient(app) as admin, TestClient(app) as user:
        sign_up(admin, "admin@example.test")
        sign_up(user, "user@example.test")
        for path in ["/api/admin/overview", "/api/admin/users", "/api/admin/activity", "/api/admin/users/1", "/api/admin/support"]:
            assert user.get(path).status_code == 403, path
            assert admin.get(path).status_code == 200, path


def test_activity_records_what_happens_without_secrets(session, sent):
    with TestClient(app) as admin, TestClient(app) as user:
        sign_up(admin, "admin@example.test")
        sign_up(user, "user@example.test", "my secret password")
        user.post("/api/auth/logout")
        user.post("/api/auth/login", json={"email": "user@example.test", "password": "wrong guess"})
        user.post("/api/auth/login", json={"email": "user@example.test", "password": "my secret password"})
        kinds = [e["kind"] for e in admin.get("/api/admin/activity").json()]
        assert {"signup", "logout", "login_failed", "login", "email_sent"} <= set(kinds)
        assert "my secret password" not in str(admin.get("/api/admin/activity").json())
        uid = next(u["id"] for u in admin.get("/api/admin/users").json() if u["email"] == "user@example.test")
        theirs = admin.get("/api/admin/activity", params={"user_id": uid}).json()
        assert theirs and all(e["user_email"] == "user@example.test" for e in theirs if e["user_id"] == uid)
        assert [e["kind"] for e in admin.get("/api/admin/activity", params={"problems": True}).json()] == ["login_failed"]


def test_overview_and_user_list(session, sent):
    with TestClient(app) as admin, TestClient(app) as user:
        sign_up(admin, "admin@example.test")
        sign_up(user, "user@example.test", name="Uma User")
        o = admin.get("/api/admin/overview").json()
        assert o["users"] == 2 and o["signups_7d"] == 2 and o["emails_24h"] == 2 and o["email_daily_limit"] == 500
        users = admin.get("/api/admin/users").json()
        uma = next(u for u in users if u["email"] == "user@example.test")
        assert uma["name"] == "Uma User" and not uma["is_admin"] and not uma["has_resume"] and uma["tracked"] == 0
        assert [u["email"] for u in admin.get("/api/admin/users", params={"q": "uma"}).json()] == ["user@example.test"]


def test_viewing_a_user_is_logged_and_never_shows_the_resume_text(session, sent, monkeypatch):
    from app import matching
    from app.pipeline import scoring
    from tests.test_matching import RESUME, fake_embed

    monkeypatch.setattr(matching, "embed", fake_embed)
    monkeypatch.setattr(scoring, "refresh_in_background", lambda: None)
    with TestClient(app) as admin, TestClient(app) as user:
        sign_up(admin, "admin@example.test")
        sign_up(user, "user@example.test")
        user.post("/api/profile/resume", json={"filename": "cv.txt", "data_base64": base64.b64encode(RESUME.encode()).decode()})
        uid = user.get("/api/auth/me").json()["id"]
        view = admin.get(f"/api/admin/users/{uid}").json()
        assert view["resume"]["uploaded"] and "Airflow" in view["resume"]["skills"]
        assert "Northeastern University" not in str(view)  # the resume text itself is never in the admin view
        log = admin.get("/api/admin/activity", params={"kind": "admin_viewed_user"}).json()
        assert len(log) == 1 and log[0]["user_email"] == "user@example.test" and log[0]["actor_email"] == "admin@example.test"


def test_admin_actions_disable_stop_alerts_resend_delete(session, sent):
    with TestClient(app) as admin, TestClient(app) as user:
        me = sign_up(admin, "admin@example.test")
        sign_up(user, "user@example.test", "user password 1")
        uid = user.get("/api/auth/me").json()["id"]
        assert admin.post(f"/api/admin/users/{me['id']}/disable", json={"disabled": True}).status_code == 400  # not yourself
        assert admin.post(f"/api/admin/users/{uid}/disable", json={"disabled": True}).json()["disabled"]
        assert user.get("/api/prefs").status_code == 401  # their sessions ended
        r = user.post("/api/auth/login", json={"email": "user@example.test", "password": "user password 1"})
        assert r.status_code == 403
        admin.post(f"/api/admin/users/{uid}/disable", json={"disabled": False})
        assert user.post("/api/auth/login", json={"email": "user@example.test", "password": "user password 1"}).status_code == 200
        assert admin.post(f"/api/admin/users/{uid}/resend-verification").json()["sent"]
        assert admin.post(f"/api/admin/users/{uid}/alerts-off").json()["ok"]
        assert admin.delete(f"/api/admin/users/{me['id']}").status_code == 400
        assert admin.delete(f"/api/admin/users/{uid}").json()["ok"]
        assert user.get("/api/prefs").status_code == 401
        assert [u["email"] for u in admin.get("/api/admin/users").json()] == ["admin@example.test"]
        kinds = {e["kind"] for e in admin.get("/api/admin/activity").json() if e["actor_email"] == "admin@example.test"}
        assert {"admin_disabled_user", "admin_enabled_user", "admin_resent_verification", "admin_alerts_off",
                "admin_deleted_user"} <= kinds


def test_users_can_delete_their_own_account(session, sent):
    from app.models import User

    with TestClient(app) as admin, TestClient(app) as user:
        sign_up(admin, "admin@example.test")
        sign_up(user, "leaving@example.test", "leaving password")
        assert user.request("DELETE", "/api/auth/account", json={"password": "wrong one"}).status_code == 400
        assert user.request("DELETE", "/api/auth/account", json={"password": "leaving password"}).json()["ok"]
        assert user.get("/api/prefs").status_code == 401
    assert session.query(User).filter_by(email="leaving@example.test").count() == 0


def test_support_requests_reach_the_admin_with_reply_to(session, sent, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "support_email", "help@example.test")
    with TestClient(app) as admin, TestClient(app) as user, TestClient(app) as stranger:
        sign_up(admin, "admin@example.test")
        sign_up(user, "user@example.test", name="Uma")
        r = user.post("/api/support", json={"topic": "bug", "message": "The <b>Apply</b> button does nothing.", "page": "Feed"})
        assert r.status_code == 200
        mail = sent[-1]
        assert mail["to"] == "help@example.test" and mail["headers"]["Reply-To"] == "user@example.test"
        assert "Something's broken from Uma" in mail["subject"] and "The <b>Apply</b> button does nothing." in mail["text"]
        # From the login screen: an email to reply to is required.
        assert stranger.post("/api/support", json={"message": "I can't log in at all."}).status_code == 400
        assert stranger.post("/api/support", json={"email": "who@example.test", "topic": "account",
                                                   "message": "I can't log in at all."}).status_code == 200
        assert stranger.post("/api/support", json={"email": "who@example.test", "message": "short"}).status_code == 400
        listed = admin.get("/api/admin/support").json()
        assert [(r["email"], r["topic"], r["emailed"]) for r in listed] == [
            ("who@example.test", "account", True), ("user@example.test", "bug", True)]
        assert admin.post(f"/api/admin/support/{listed[0]['id']}", json={"status": "done"}).json()["ok"]
        for _ in range(5):
            stranger.post("/api/support", json={"email": "who@example.test", "message": "Another message here."})
        assert stranger.post("/api/support", json={"email": "who@example.test", "message": "And one more message."}).status_code == 429


def test_recruiter_lookups_are_limited_per_user(session, sent, monkeypatch):
    from app import main
    from app.models import Company, Job

    async def fake_lookup(session, company, refresh):
        return {"domain": "acme.com", "emails": []}

    monkeypatch.setattr(main, "hunter_lookup", fake_lookup)
    acme = Company(name="Acme", ats="greenhouse", slug="acme")
    session.add(acme)
    session.flush()
    from datetime import datetime, timezone
    session.add(Job(company=acme, external_id="1", title="Data Analyst", url="https://x/1", fingerprint="f",
                    first_seen_at=datetime.now(timezone.utc)))
    session.commit()
    job_id = session.query(Job).one().id
    with TestClient(app) as admin, TestClient(app) as user:
        sign_up(admin, "admin@example.test")
        sign_up(user, "user@example.test")
        for _ in range(main.HUNTER_LOOKUPS_PER_DAY):
            assert user.get(f"/api/jobs/{job_id}/recruiters", params={"lookup": True}).status_code == 200
        assert user.get(f"/api/jobs/{job_id}/recruiters", params={"lookup": True}).status_code == 429
        assert user.get(f"/api/jobs/{job_id}/recruiters").status_code == 200  # the search links still work
        assert admin.get(f"/api/jobs/{job_id}/recruiters", params={"lookup": True}).status_code == 200  # no limit
        assert admin.get("/api/admin/overview").json()["recruiter_lookups_24h"] == main.HUNTER_LOOKUPS_PER_DAY + 1


def test_api_docs_are_off_by_default():
    with TestClient(app) as c:
        assert c.get("/docs").status_code == 404 and c.get("/openapi.json").status_code == 404


def test_view_as_user_shows_their_portal_read_only_and_is_logged(session, sent, monkeypatch):
    from app import matching
    from app.pipeline import scoring
    from tests.test_matching import RESUME, fake_embed

    monkeypatch.setattr(matching, "embed", fake_embed)
    monkeypatch.setattr(scoring, "refresh_in_background", lambda: None)
    with TestClient(app) as admin, TestClient(app) as user:
        sign_up(admin, "admin@example.test")
        sign_up(user, "uma@example.test", name="Uma")
        user.put("/api/prefs", json={"include_keywords": ["nurse"], "exclude_keywords": []})
        user.post("/api/profile/resume", json={"filename": "cv.txt", "data_base64": base64.b64encode(RESUME.encode()).decode()})
        uid = user.get("/api/auth/me").json()["id"]
        admin_cookie = admin.cookies.get("fj_session")

        me = admin.post(f"/api/admin/users/{uid}/view-as").json()
        assert me["email"] == "uma@example.test" and me["viewing_as"]["admin_email"] == "admin@example.test"
        assert admin.cookies.get("fj_session") != admin_cookie  # a new session: cached pages can't mix
        # Exactly their portal...
        assert admin.get("/api/auth/me").json()["viewing_as"]["admin_email"] == "admin@example.test"
        assert admin.get("/api/prefs").json()["include_keywords"] == ["nurse"]
        profile = admin.get("/api/profile").json()
        assert profile["has_resume"] and "Airflow" in profile["skills"]
        assert profile["resume_preview"] == ""  # ...but never the resume text
        # ...read-only, and no admin pages or paid lookups meanwhile.
        r = admin.put("/api/prefs", json={"include_keywords": ["changed"], "exclude_keywords": []})
        assert r.status_code == 403 and "read-only" in r.json()["detail"]
        assert admin.post("/api/applications", json={"company": "X", "title": "Y"}).status_code == 403
        assert admin.get("/api/admin/overview").status_code == 403
        assert user.get("/api/prefs").json()["include_keywords"] == ["nurse"]  # unchanged

        back = admin.post("/api/admin/view-as/stop").json()
        assert back["email"] == "admin@example.test" and back["is_admin"]
        again = admin.post("/api/admin/view-as/stop")  # a second click on Exit: still just the admin
        assert again.status_code == 200 and again.json()["is_admin"]
        assert admin.get("/api/admin/overview").status_code == 200
        log = [e["detail"] for e in admin.get("/api/admin/activity", params={"kind": "admin_viewed_user"}).json()]
        assert "started viewing the portal as them (read-only)" in log and "stopped viewing as them" in log


def test_only_the_admin_can_view_as_someone(session, sent):
    with TestClient(app) as admin, TestClient(app) as a, TestClient(app) as b:
        sign_up(admin, "admin@example.test")
        sign_up(a, "a@example.test")
        sign_up(b, "b@example.test")
        bid = b.get("/api/auth/me").json()["id"]
        assert a.post(f"/api/admin/users/{bid}/view-as").status_code == 403
        assert a.post("/api/admin/view-as/stop").status_code == 400
