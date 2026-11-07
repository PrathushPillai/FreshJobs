import logging
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from html import escape

from app.config import settings
from app.models import Job

log = logging.getLogger(__name__)


def email_configured() -> bool:
    return bool(settings.smtp_host and settings.alert_to)


_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


def email_mode() -> str:
    """"off" (not set up), "test" (a local test mail server: nothing reaches a real inbox) or "on"."""
    if not email_configured():
        return "off"
    to_domain = (settings.alert_to or "").rsplit("@", 1)[-1].lower()
    if settings.smtp_host.lower() in _LOCAL_HOSTS or to_domain in _LOCAL_HOSTS:
        return "test"
    return "on"


def send_test() -> tuple[bool, str]:
    """Send a short test email now; (sent, what happened)."""
    if not email_configured():
        return False, "Email isn't set up: add the SMTP settings to .env and restart the backend."
    msg = EmailMessage()
    msg["Subject"] = "FreshJobs test email"
    msg["From"] = settings.alert_from or settings.smtp_user
    msg["To"] = settings.alert_to
    msg.set_content("This is a test from FreshJobs. Job alerts will arrive at this address.")
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
            if settings.smtp_starttls:
                smtp.starttls()
            if settings.smtp_user:
                smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(msg)
    except (OSError, smtplib.SMTPException) as e:
        where = f"{settings.smtp_host}:{settings.smtp_port}"
        return False, f"Couldn't send through {where}: {type(e).__name__}: {e}"[:300]
    return True, f"Sent to {settings.alert_to}."


def _minutes_ago(job: Job, now: datetime) -> int:
    seen = job.first_seen_at if job.first_seen_at.tzinfo else job.first_seen_at.replace(tzinfo=timezone.utc)
    return max(0, int((now - seen).total_seconds() // 60))


def build_message(jobs: list[Job], now: datetime) -> EmailMessage:
    subject = f"{len(jobs)} new job{'s' if len(jobs) != 1 else ''}: " + ", ".join(
        f"{j.company.name} – {j.title}" for j in jobs[:3]
    )
    text_lines, rows = [], []
    for j in jobs:
        age = _minutes_ago(j, now)
        text_lines.append(f"{j.company.name} | {j.title} | {j.location or 'n/a'} | seen {age} min ago\n  {j.url}")
        rows.append(
            f"<tr><td style='padding:6px 10px'><b>{escape(j.company.name)}</b></td>"
            f"<td style='padding:6px 10px'><a href='{escape(j.url)}'>{escape(j.title)}</a></td>"
            f"<td style='padding:6px 10px'>{escape(j.location or 'n/a')}</td>"
            f"<td style='padding:6px 10px'>{age} min ago</td></tr>"
        )
    msg = EmailMessage()
    msg["Subject"] = subject[:180]
    msg["From"] = settings.alert_from or settings.smtp_user
    msg["To"] = settings.alert_to
    msg.set_content("\n\n".join(text_lines))
    msg.add_alternative(
        "<p>New matching jobs, straight from the company boards:</p>"
        "<table style='border-collapse:collapse;font-family:sans-serif;font-size:14px'>"
        "<tr><th align=left>Company</th><th align=left>Role</th><th align=left>Location</th><th align=left>First seen</th></tr>"
        + "".join(rows)
        + "</table>",
        subtype="html",
    )
    return msg


def send_alert(jobs: list[Job]) -> bool:
    """Send one email listing all jobs. Returns True if it was sent."""
    if not jobs or not email_configured():
        return False
    msg = build_message(jobs, datetime.now(timezone.utc))
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
            if settings.smtp_starttls:
                smtp.starttls()
            if settings.smtp_user:
                smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(msg)
    except (OSError, smtplib.SMTPException):
        log.exception("Failed to send alert email")
        return False
    log.info("Sent alert email with %d jobs", len(jobs))
    return True
