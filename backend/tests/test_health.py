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


def _flood_feed(session, employers: list[str], title=lambda i, employer: f"Role {i}"):
    from app.models import Company, Job

    jazz = Company(name="JazzHR (all employers)", ats="jazzhr", slug="all", enabled=True)
    session.add(jazz)
    session.flush()
    now = datetime.now(timezone.utc)
    for i, employer in enumerate(employers):
        session.add(Job(company_id=jazz.id, external_id=str(i), title=title(i, employer), url=f"https://x/{i}",
                        company_name=employer, company_key=employer.lower(), location="Austin, TX",
                        us_eligible=True, fingerprint=f"f{i}", first_seen_at=now, last_seen_at=now))
    session.commit()
    return next(c for c in health.check_feed_content(session) if c.name == "one source flooding")


def test_a_multi_employer_source_is_counted_per_employer(session):
    # JazzHR's daily batch: hundreds of jobs from one source, each from a different employer. Not a flood.
    check = _flood_feed(session, [f"Employer {i}" for i in range(300)])
    assert check.level == "ok"


def test_one_employer_flooding_through_a_multi_employer_source_is_flagged(session):
    # 60 copies of 3 roles across towns (location blasting, like AO Garcia Agency's).
    check = _flood_feed(session, ["Blaster Inc"] * 60 + [f"Employer {i}" for i in range(240)],
                        title=lambda i, e: f"Sales Rep {i % 3}" if e == "Blaster Inc" else f"Role {i}")
    assert check.level == "warn" and "Blaster Inc (via JazzHR" in check.detail and "60 of the newest 300" in check.detail


def test_a_big_employer_posting_many_different_jobs_is_not_a_flood(session):
    # Allied Universal, 2026-11-18: 303 new jobs in one check with 290 different titles.
    check = _flood_feed(session, ["Big Employer"] * 60 + [f"Employer {i}" for i in range(240)])
    assert check.level == "ok"


def test_past_the_max_share_it_is_a_flood_whatever_the_titles(session):
    check = _flood_feed(session, ["Big Employer"] * 100 + [f"Employer {i}" for i in range(200)])
    assert check.level == "warn"


def test_one_outage_is_one_alert():
    """Workday's block also makes its boards stale and the slow lane fall behind: one warning, not three."""
    checks = [Check("job platforms", "warn", "most boards failing on: workday (3,206 of 4,976)",
                    {"platforms": [["workday", 4976, 3206]]}),
              Check("platform freshness", "warn", "boards not checked for hours on: workday (59 h)",
                    {"platforms": ["workday"]}),
              Check("slow polls", "warn", "last poll finished 29 min ago: 8,228 boards, 23 errors, 2,052 new jobs, "
                    "4,954 cut short (the lane can't keep up)"),
              Check("fast polls", "warn", "last poll finished 1 min ago: 19,000 boards, 1 errors, 5 new jobs, "
                    "9,000 cut short (the lane can't keep up)")]
    out = {c.name: c.level for c in health._one_alert_per_incident(checks)}
    assert out == {"job platforms": "warn", "platform freshness": "ok", "slow polls": "ok", "fast polls": "warn"}
    other = [Check("job platforms", "warn", "x", {"platforms": [["workday", 10, 9]]}),
             Check("platform freshness", "warn", "y", {"platforms": ["workday", "greenhouse"]})]
    assert health._one_alert_per_incident(other)[1].level == "warn"  # Greenhouse going stale is its own problem


def test_a_new_boards_first_scan_is_not_a_flood(session):
    """Join Parachute, 2026-11-19: a plasma-center chain's first scan put 492 jobs (21 roles in 77 towns) at the top
    of the newest found. They were all baseline jobs: what the board already had, not a flood."""
    from app.models import Company, Job

    chain = Company(name="Chain Co", ats="pinpoint", slug="chain", enabled=True)
    session.add(chain)
    session.flush()
    now = datetime.now(timezone.utc)
    for i in range(300):
        session.add(Job(company_id=chain.id, external_id=str(i), title=f"Paramedic {i % 5}", url=f"https://c/{i}",
                        company_name="Chain Co", company_key="chain co", location="Austin, TX", us_eligible=True,
                        baseline=i < 280, fingerprint=f"c{i}", first_seen_at=now, last_seen_at=now))
    session.commit()
    check = next(c for c in health.check_feed_content(session) if c.name == "one source flooding")
    assert check.level == "ok" and "20 of the newest 300" in check.detail
