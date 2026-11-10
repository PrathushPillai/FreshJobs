from app.config import settings
from app.notify import email


def test_email_mode(monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", "")
    assert email.email_mode() == "off"
    monkeypatch.setattr(settings, "smtp_host", "localhost")
    assert email.email_mode() == "test"  # a local test sink: nothing reaches a real inbox
    monkeypatch.setattr(settings, "smtp_host", "smtp.gmail.com")
    assert email.email_mode() == "on"


def test_send_test_reports_failures(monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", "127.0.0.1")
    monkeypatch.setattr(settings, "smtp_port", 9)  # nothing listens here
    monkeypatch.setattr(settings, "alert_to", "me@example.com")
    monkeypatch.setattr(settings, "smtp_starttls", False)
    sent, detail = email.send_test("me@example.com")
    assert not sent and "127.0.0.1:9" in detail
