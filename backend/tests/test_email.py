from datetime import timedelta

from app.models import Company, Job
from app.notify import email


def test_build_message_lists_jobs(now):
    company = Company(name="Acme", ats="greenhouse", slug="acme")
    job = Job(company=company, external_id="1", title="Data Engineer", location="Austin, TX",
              url="https://x/1", first_seen_at=now - timedelta(minutes=4), fingerprint="f")
    msg = email.build_message([job], now)
    assert msg["Subject"] == "1 new job: Acme – Data Engineer"
    body = msg.get_body(("plain",)).get_content()
    assert "Austin, TX" in body and "seen 4 min ago" in body and "https://x/1" in body


def test_send_alert_skips_when_not_configured(monkeypatch):
    monkeypatch.setattr(email.settings, "smtp_host", "")
    assert email.send_alert([object()]) is False
