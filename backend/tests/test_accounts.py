"""Accounts: sign-up, login, sessions, email links, isolation between users, admin-only routes, opt-in alerts."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import Company
from app.pipeline.poller import process_company
from app.prefs import get_prefs
from app.sources import NormalizedJob
from tests.conftest import sign_up


def nj(i, title, loc="Austin, TX", desc=""):
    return NormalizedJob(external_id=str(i), title=title, url=f"https://x/{i}", locations=[loc], description=desc)


@pytest.fixture()
def mail(monkeypatch):
    """Capture account emails and alert emails instead of sending them."""
    from app.notify import email

    box = []
    monkeypatch.setattr(email.settings, "smtp_host", "localhost")
    monkeypatch.setattr(email, "send_welcome", lambda user, link: box.append(("welcome", user.email, link)) or True)
    monkeypatch.setattr(email, "send_reset", lambda user, link: box.append(("reset", user.email, link)) or True)
    monkeypatch.setattr(email, "send_alerts_on", lambda user, prefs: box.append(("alerts_on", user.email, "")) or True)
    monkeypatch.setattr(email, "send_job_alert",
                        lambda user, jobs, matches: box.append(("alert", user.email, [j.title for j in jobs])) or True)
    return box


@pytest.fixture()
def jobs(session, now):
    acme = Company(name="Acme", ats="greenhouse", slug="acme")
    session.add(acme)
    session.flush()
    prefs = get_prefs(session)
    process_company(session, acme, [], prefs, now - timedelta(hours=1))  # baseline
    process_company(session, acme, [nj(1, "Data Analyst"), nj(2, "Registered Nurse"), nj(3, "Software Engineer")], prefs, now)
    session.commit()
    return acme


def link_token(link):
    return link.split("=", 1)[1]


def test_everything_needs_a_login_except_the_public_routes():
    from app.auth import PUBLIC_PATHS

    with TestClient(app) as c:
        for route in app.routes:
            path, methods = getattr(route, "path", ""), getattr(route, "methods", set())
            if not path.startswith("/api/") or path in PUBLIC_PATHS:
                continue
            url = path.replace("{job_id}", "1").replace("{app_id}", "1").replace("{company_id}", "1")
            for method in methods - {"HEAD", "OPTIONS"}:
                r = c.request(method, url, json={})
                assert r.status_code == 401, (method, path, r.status_code)


def test_signup_login_logout_and_the_first_account_is_admin(session, mail):
    with TestClient(app) as c:
        me = sign_up(c, "Admin@Example.test")
        assert me["email"] == "admin@example.test" and me["is_admin"] and not me["email_verified"]
        assert mail[0][0] == "welcome" and "?verify=" in mail[0][2]
        assert c.get("/api/prefs").status_code == 200
        c.post("/api/auth/logout")
        assert c.get("/api/prefs").status_code == 401
        assert c.post("/api/auth/login", json={"email": "admin@example.test", "password": "wrong password"}).status_code == 401
        assert c.post("/api/auth/login", json={"email": "ADMIN@example.test", "password": "correct horse battery"}).status_code == 200
    with TestClient(app) as c2:
        assert not sign_up(c2, "second@example.test")["is_admin"]
        r = c2.post("/api/auth/signup", json={"email": "second@example.test", "password": "another password"})
        assert r.status_code == 409
        assert c2.post("/api/auth/signup", json={"email": "not-an-email", "password": "long enough"}).status_code == 400
        assert c2.post("/api/auth/signup", json={"email": "x@example.test", "password": "short"}).status_code == 400


def test_password_is_hashed_and_sessions_are_hashed(session):
    from app.models import AuthSession, User

    with TestClient(app) as c:
        sign_up(c, "me@example.test", "correct horse battery")
        cookie = c.cookies.get("fj_session")
    user = session.query(User).one()
    assert user.password_hash.startswith("scrypt$") and "correct horse" not in user.password_hash
    assert cookie and session.query(AuthSession).one().token_hash != cookie


def test_repeated_failed_logins_are_refused(session):
    with TestClient(app) as c:
        sign_up(c, "me@example.test")
        c.post("/api/auth/logout")
        for _ in range(5):
            assert c.post("/api/auth/login", json={"email": "me@example.test", "password": "nope nope"}).status_code == 401
        r = c.post("/api/auth/login", json={"email": "me@example.test", "password": "correct horse battery"})
        assert r.status_code == 429


def test_email_confirmation_link_works_once(session, mail):
    with TestClient(app) as c:
        sign_up(c, "me@example.test")
        token = link_token(mail[0][2])
        assert c.post("/api/auth/verify", json={"token": token}).json()["email_verified"]
        assert c.post("/api/auth/verify", json={"token": token}).status_code == 400  # used
        assert c.post("/api/auth/verify", json={"token": "made-up"}).status_code == 400


def test_forgot_and_reset_password(session, mail):
    with TestClient(app) as c, TestClient(app) as other_device:
        sign_up(c, "me@example.test", "old password 1")
        other_device.post("/api/auth/login", json={"email": "me@example.test", "password": "old password 1"})
        assert other_device.get("/api/prefs").status_code == 200
        c.post("/api/auth/logout")
        assert c.post("/api/auth/forgot", json={"email": "nobody@example.test"}).json() == {"ok": True}  # no hint
        c.post("/api/auth/forgot", json={"email": "me@example.test"})
        reset = [m for m in mail if m[0] == "reset"]
        assert len(reset) == 1 and "?reset=" in reset[0][2]
        assert c.post("/api/auth/reset", json={"token": link_token(reset[0][2]), "password": "x"}).status_code == 400
        r = c.post("/api/auth/reset", json={"token": link_token(reset[0][2]), "password": "new password 2"})
        assert r.status_code == 200 and c.get("/api/prefs").status_code == 200  # logged in with the new one
        assert other_device.get("/api/prefs").status_code == 401  # every other session ended
        assert c.post("/api/auth/reset", json={"token": link_token(reset[0][2]), "password": "again pass 3"}).status_code == 400
        c.post("/api/auth/logout")
        assert c.post("/api/auth/login", json={"email": "me@example.test", "password": "old password 1"}).status_code == 401
        assert c.post("/api/auth/login", json={"email": "me@example.test", "password": "new password 2"}).status_code == 200


def test_change_password_needs_the_current_one(session):
    with TestClient(app) as c:
        sign_up(c, "me@example.test", "first password")
        assert c.post("/api/auth/password", json={"current": "wrong one", "new": "second password"}).status_code == 400
        assert c.post("/api/auth/password", json={"current": "first password", "new": "second password"}).status_code == 200
        c.post("/api/auth/logout")
        assert c.post("/api/auth/login", json={"email": "me@example.test", "password": "second password"}).status_code == 200


def test_users_cannot_see_or_change_each_others_data(session, jobs, mail):
    with TestClient(app) as a, TestClient(app) as b:
        sign_up(a, "a@example.test")
        sign_up(b, "b@example.test")
        job_id = next(j["id"] for j in a.get("/api/jobs", params={"hide_no_sponsor": False}).json()["items"])
        entry = a.post("/api/applications", json={"job_id": job_id, "status": "applied"}).json()
        # B sees none of A's tracker, can't change or delete it, and can track the same job themselves.
        assert b.get("/api/applications").json() == []
        assert b.patch(f"/api/applications/{entry['id']}", json={"status": "offer"}).status_code == 404
        assert b.delete(f"/api/applications/{entry['id']}").status_code == 404
        assert b.post("/api/applications", json={"job_id": job_id, "status": "saved"}).status_code == 201
        assert a.get("/api/status").json()["tracked"] == 1 and b.get("/api/status").json()["tracked"] == 1
        tracked_a = a.get("/api/jobs", params={"tracked": "tracked", "hide_no_sponsor": False}).json()["items"]
        assert [j["application"]["status"] for j in tracked_a] == ["applied"]
        tracked_b = b.get("/api/jobs", params={"tracked": "tracked", "hide_no_sponsor": False}).json()["items"]
        assert [j["application"]["status"] for j in tracked_b] == ["saved"]  # the cache never mixes users
        # Each user's For you follows their own roles.
        a.put("/api/prefs", json={"include_keywords": ["data analyst"], "exclude_keywords": []})
        b.put("/api/prefs", json={"include_keywords": ["nurse"], "exclude_keywords": []})
        mine = lambda c: sorted(j["title"] for j in c.get(  # noqa: E731
            "/api/jobs", params={"alert_roles_only": True, "hide_no_sponsor": False}).json()["items"])
        assert mine(a) == ["Data Analyst"] and mine(b) == ["Registered Nurse"]
        assert b.get("/api/prefs").json()["include_keywords"] == ["nurse"]


def test_only_the_admin_changes_sources_or_starts_a_check(session):
    with TestClient(app) as admin, TestClient(app) as user:
        sign_up(admin, "admin@example.test")
        sign_up(user, "user@example.test")
        assert user.post("/api/poll").status_code == 403
        assert user.post("/api/companies", json={"name": "X", "ats": "greenhouse", "slug": "x"}).status_code == 403
        assert admin.post("/api/companies", json={"name": "X", "ats": "greenhouse", "slug": "x"}).status_code == 201


def test_admin_email_setting_decides_the_admin(session, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "admin_email", "boss@example.test")
    with TestClient(app) as first, TestClient(app) as boss:
        assert not sign_up(first, "first@example.test")["is_admin"]  # first, but not the admin email
        assert sign_up(boss, "boss@example.test")["is_admin"]


def test_alerts_are_opt_in_need_a_confirmed_email_and_unsubscribe_in_one_click(session, jobs, mail, now):
    from app.models import User
    from app.pipeline.alerts import deliver_alerts

    with TestClient(app) as c:
        sign_up(c, "me@example.test")
        assert c.get("/api/alerts/settings").json()["email_alerts"] is False  # off until switched on
        assert c.put("/api/alerts/settings", json={"email_alerts": True}).status_code == 400  # email not confirmed
        c.post("/api/auth/verify", json={"token": link_token(mail[0][2])})
        on = c.put("/api/alerts/settings", json={"email_alerts": True, "alert_frequency": "instant", "alert_daily_cap": 2}).json()
        assert on["email_alerts"] and on["confirmation_sent"] and ("alerts_on", "me@example.test", "") in mail
        c.put("/api/prefs", json={"include_keywords": ["data analyst", "software engineer"], "exclude_keywords": []})
    # Switching on starts the marker at "now"; put it on the test's clock. Jobs found before it aren't sent (no
    # backlog); new ones are, once.
    user = session.query(User).one()
    prefs = get_prefs(session, user.id)
    prefs.alert_watermark = now
    session.commit()
    assert deliver_alerts(now + timedelta(minutes=5)) == 0
    acme = jobs
    process_company(session, acme, [nj(1, "Data Analyst"), nj(2, "Registered Nurse"), nj(3, "Software Engineer"),
                                    nj(4, "Senior Data Analyst"), nj(5, "Data Analyst II", "Denver, CO")],
                    get_prefs(session), now + timedelta(minutes=10))
    session.commit()
    later = now + timedelta(hours=2)
    assert deliver_alerts(later) == 1
    sent = [m for m in mail if m[0] == "alert"]
    assert sent[-1][2] == ["Data Analyst II"]  # their role, new, not senior; nurse isn't theirs
    assert deliver_alerts(later + timedelta(minutes=10)) == 0  # nothing new, and "instant" is at most hourly
    with TestClient(app) as anon:
        r = anon.get("/api/alerts/unsubscribe", params={"token": user.unsubscribe_token})
        assert r.status_code == 200 and "unsubscribed" in r.text
    session.expire_all()
    assert get_prefs(session, user.id).email_alerts is False


def test_alert_frequency_and_daily_cap():
    from datetime import datetime, timezone

    from app.models import UserPrefs
    from app.pipeline.alerts import is_due, under_cap

    eastern_9am = datetime(2026, 11, 9, 14, 0, tzinfo=timezone.utc)  # 9 am EST
    p = UserPrefs(alert_frequency="instant", last_alert_at=eastern_9am - timedelta(minutes=30), alert_daily_cap=3,
                  alerts_sent_today=3, alerts_sent_on=eastern_9am.date())
    assert not is_due(p, eastern_9am) and is_due(p, eastern_9am + timedelta(minutes=31))
    p.alert_frequency = "daily"
    p.last_alert_at = eastern_9am - timedelta(days=1)
    assert is_due(p, eastern_9am) and not is_due(p, eastern_9am - timedelta(hours=2))  # before 8 am Eastern
    p.last_alert_at = eastern_9am
    assert not is_due(p, eastern_9am + timedelta(hours=5))  # once a day
    assert not under_cap(p, eastern_9am)  # 3 of 3 sent today
    assert under_cap(p, eastern_9am + timedelta(days=1))  # a new day


def test_roles_from_before_accounts_keep_counting_until_the_admin_claims_them(session, monkeypatch):
    """Before the admin signs up, another account (the portal check's) mustn't replace the existing roles."""
    from app.config import settings

    legacy = get_prefs(session)
    legacy.include_keywords, legacy.exclude_keywords = ["data engineer", "nurse"], []
    session.commit()
    monkeypatch.setattr(settings, "admin_email", "boss@example.test")
    with TestClient(app) as other:
        sign_up(other, "check@example.test")  # an ordinary account, signed up first
    session.expire_all()
    everyone = get_prefs(session)
    assert {"data engineer", "nurse"} <= set(everyone.include_keywords)  # still part of "someone's For you"
    with TestClient(app) as boss:
        sign_up(boss, "boss@example.test")
        assert boss.get("/api/prefs").json()["include_keywords"] == ["data engineer", "nurse"]  # claimed
