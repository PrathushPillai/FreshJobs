from datetime import timedelta

from app.models import Company, Job, User
from app.notify import email


def _user():
    return User(id=7, email="me@example.test", name="Me Person", unsubscribe_token="tok123")


def _job(i, title, location, now, company="FanDuel", **kw):
    return Job(id=i, company=Company(name=company, ats="greenhouse", slug="x"), company_name=company,
               company_key=company.lower(), external_id=str(i), title=title, location=location, url=f"https://x/{i}",
               first_seen_at=now - timedelta(minutes=4), listed_at=now - timedelta(minutes=9), fingerprint=f"f{i}", **kw)


def test_alert_groups_cities_and_shows_badges_and_unsubscribe(now):
    jobs = [_job(1, "Salesforce Tester", "Los Angeles, CA", now, h1b_filings=1200),
            _job(2, "Salesforce Tester", "Atlanta, GA", now),
            _job(3, "Salesforce Tester", "New York, NY", now),
            _job(4, "Data Engineer", "Austin, TX", now, company="Acme", sponsors_text=True, exp_level="entry")]
    subject, text, html = email.build_alert(_user(), jobs, {4: {"score": 82}}, now)
    # The same role in three cities is one card; the best match comes first.
    assert subject == "2 new jobs: Data Engineer at Acme and 1 more"
    assert html.count("Salesforce Tester</a>") == 1 and "Los Angeles, CA; Atlanta, GA; New York, NY" in html
    assert "82% match" in html and "Says it sponsors" in html and "H-1B · 1,200" in html and "Entry level" in html
    assert "posted 9 min ago" in html and html.count("Apply ↗") == 2
    assert "/api/alerts/unsubscribe?token=tok123" in html and "Manage alerts" in html
    assert "https://x/4" in text and "Unsubscribe:" in text


def test_alert_html_escapes_job_text(now):
    jobs = [_job(1, "<script>alert(1)</script> Engineer", "Austin, TX", now, company="Evil & Co")]
    _, _, html = email.build_alert(_user(), jobs, {}, now)
    assert "<script>" not in html and "&lt;script&gt;" in html and "Evil &amp; Co" in html


def test_unsubscribe_headers_are_one_click():
    h = email._unsubscribe_headers(_user())
    assert h["List-Unsubscribe"].endswith("/api/alerts/unsubscribe?token=tok123>")
    assert h["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"


def test_nothing_is_sent_when_email_is_not_set_up(monkeypatch):
    monkeypatch.setattr(email.settings, "smtp_host", "")
    assert email.send_email("me@example.test", "s", "t", "<p>h</p>") == (
        False, "Email isn't set up: add the SMTP settings to .env and restart the backend.")
