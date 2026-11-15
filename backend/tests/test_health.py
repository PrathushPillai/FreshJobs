"""The health monitor's alerting: who gets emailed when, and what it saves (app/health.py)."""

from datetime import datetime, timedelta, timezone

import pytest

from app import health
from app.health import Check


@pytest.fixture
def monitor(tmp_path, monkeypatch):
    """health.run() with made-up checks, a temporary health.json and a fake mailer."""
    state = {"checks": [], "sent": []}
    monkeypatch.setattr(health, "health_file", lambda: tmp_path / "health.json")
    monkeypatch.setattr(health, "run_checks", lambda previous, force: list(state["checks"]))
    monkeypatch.setattr(health, "_email", lambda subject, lines, footer: state["sent"].append((subject, lines)) or True)
    monkeypatch.setattr(health, "FAIL_GRACE_MINUTES", 0)
    return state


def test_a_failure_emails_at_once_and_only_once(monitor):
    monitor["checks"] = [Check("backend", "fail", "down"), Check("disk", "ok", "fine")]
    result = health.run()
    assert result["level"] == "fail"
    assert len(monitor["sent"]) == 1 and "backend" in monitor["sent"][0][0]
    health.run()  # still failing: no second email before REMIND_HOURS
    assert len(monitor["sent"]) == 1


def test_recovery_sends_a_back_to_normal_email(monitor):
    monitor["checks"] = [Check("backend", "fail", "down")]
    health.run()
    monitor["checks"] = [Check("backend", "ok", "fine")]
    result = health.run()
    assert result["level"] == "ok"
    assert monitor["sent"][-1][0] == "FreshJobs: back to normal"
    health.run()
    assert len(monitor["sent"]) == 2  # nothing more once it's normal


def test_a_warning_waits_for_its_grace_period(monitor):
    monitor["checks"] = [Check("job platforms", "warn", "workday failing")]
    health.run()
    assert monitor["sent"] == []
    # Pretend the warning started over an hour ago.
    data = health.json.loads(health.health_file().read_text())
    data["since"]["job platforms"] = (datetime.now(timezone.utc) - timedelta(minutes=health.WARN_GRACE_MINUTES + 1)).isoformat()
    health.health_file().write_text(health.json.dumps(data))
    health.run()
    assert len(monitor["sent"]) == 1 and "job platforms" in monitor["sent"][0][0]


def test_a_new_problem_emails_again(monitor):
    monitor["checks"] = [Check("backend", "fail", "down")]
    health.run()
    monitor["checks"] = [Check("backend", "fail", "down"), Check("backups", "fail", "none")]
    health.run()
    assert len(monitor["sent"]) == 2 and "backups" in monitor["sent"][1][0]


def test_print_mode_neither_emails_nor_saves(monitor):
    monitor["checks"] = [Check("backend", "fail", "down")]
    health.run(send=False, save=False)
    assert monitor["sent"] == [] and not health.health_file().exists()


def test_last_result_flags_a_stopped_monitor(monitor):
    assert health.last_result()["level"] == "unknown"
    monitor["checks"] = [Check("backend", "ok", "fine")]
    health.run()
    assert health.last_result()["stale"] is False
    data = health.json.loads(health.health_file().read_text())
    data["checked_at"] = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    health.health_file().write_text(health.json.dumps(data))
    assert health.last_result()["stale"] is True


def test_feed_quality_and_platform_checks_run_on_the_database(session):
    from app.models import Company, Job

    c = Company(name="Acme", ats="greenhouse", slug="acme", enabled=True, last_status="error: HTTP 404")
    session.add(c)
    session.flush()
    session.add(Job(company_id=c.id, external_id="1", title="Data &amp; Analyst", url="https://x/1",
                    fingerprint="f1", first_seen_at=datetime.now(timezone.utc), last_seen_at=datetime.now(timezone.utc)))
    session.commit()
    assert all(c.level == "ok" for c in health.check_feed_content(session))  # one odd title isn't a trend
    assert health.check_platforms(session)[0].level == "ok"  # one board isn't a platform


def test_admin_health_route_is_admin_only(session):
    from fastapi.testclient import TestClient

    from app.main import app
    from tests.conftest import sign_up

    with TestClient(app) as client:
        _check_admin_route(client)


def _check_admin_route(client):
    from tests.conftest import sign_up

    assert client.get("/api/admin/health").status_code == 401
    sign_up(client)  # the first account is the admin
    r = client.get("/api/admin/health")
    assert r.status_code == 200 and "checks" in r.json()


def test_a_failure_during_a_restart_waits_for_the_next_run(monitor, monkeypatch):
    monkeypatch.setattr(health, "FAIL_GRACE_MINUTES", 10)
    monitor["checks"] = [Check("backend", "fail", "down")]
    health.run()
    assert monitor["sent"] == []  # a deploy restarts the backend for minutes
