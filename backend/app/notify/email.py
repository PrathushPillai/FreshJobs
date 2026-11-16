"""Every email the portal sends: account emails (welcome and confirm, password reset, alerts switched on), job
alerts, and the Settings test email.

Email clients ignore most CSS, so the HTML is tables with inline styles (no web fonts, no flexbox), a solid
colour under every gradient (Outlook draws only the colour), and a plain-text part. Alert emails carry
List-Unsubscribe headers (RFC 8058 one-click), which Gmail and Outlook expect from bulk senders, plus a visible
unsubscribe link.
"""

import hashlib
import logging
import smtplib
from collections import OrderedDict
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from html import escape

from app.config import settings
from app.models import Job

log = logging.getLogger(__name__)
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}

BRAND, BRAND_DARK, INK, MUTED, LINE, BG = "#6d28d9", "#4c1d95", "#0c0a1d", "#64748b", "#e9e5f5", "#f5f3ff"


def smtp_configured() -> bool:
    return bool(settings.smtp_host)


def email_configured() -> bool:  # kept for older callers
    return smtp_configured()


def email_mode() -> str:
    """"off" (not set up), "test" (a local test mail server: nothing reaches a real inbox) or "on"."""
    if not smtp_configured():
        return "off"
    return "test" if settings.smtp_host.lower() in _LOCAL_HOSTS else "on"


def _sender() -> str:
    return formataddr(("FreshJobs", settings.alert_from or settings.smtp_user or "alerts@freshjobs.local"))


def emails_sent_last_24h() -> int:
    """Emails sent in the last 24 hours, from the activity log (ix_activity_kind_at)."""
    from datetime import timedelta

    from sqlalchemy import func, select

    from app.db import SessionLocal
    from app.models import Activity

    since = datetime.now(timezone.utc) - timedelta(hours=24)
    with SessionLocal() as session:
        return session.scalar(select(func.count(Activity.id)).where(Activity.kind == "email_sent", Activity.at >= since)) or 0


def send_email(to: str, subject: str, text: str, html: str, headers: dict | None = None,
               user_id: int | None = None) -> tuple[bool, str]:
    """Send one email. Returns (sent, error message). Every attempt goes in the admin's activity log."""
    from app.activity import record
    if not smtp_configured():
        return False, "Email isn't set up: add the SMTP settings to .env and restart the backend."
    if settings.email_daily_limit > 0 and emails_sent_last_24h() >= settings.email_daily_limit:
        # Past it, Gmail starts refusing and may lock the account; better to refuse here and say so.
        log.warning("Email to %s not sent: %d emails in 24 h is the limit", to, settings.email_daily_limit)
        record("email_failed", user_id, f"{subject[:120]} | daily email limit reached")
        return False, f"The site has sent its {settings.email_daily_limit} emails for today. Try again tomorrow."
    msg = EmailMessage()
    msg["Subject"] = subject[:180]
    msg["From"] = _sender()
    msg["To"] = to
    msg["Message-ID"] = make_msgid(domain="freshjobs")
    for k, v in (headers or {}).items():
        msg[k] = v
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
            if settings.smtp_starttls:
                smtp.starttls()
            if settings.smtp_user:
                smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(msg)
    except (OSError, smtplib.SMTPException) as e:
        where = f"{settings.smtp_host}:{settings.smtp_port}"
        log.warning("Email to %s failed via %s: %s", to, where, e)
        record("email_failed", user_id, f"{subject[:120]} | {type(e).__name__}: {str(e)[:150]}")
        return False, f"Couldn't send through {where}: {e}"
    record("email_sent", user_id, subject[:200])
    return True, ""


def _url(path: str) -> str:
    return settings.public_url.rstrip("/") + path


# ------------------------------------------------------------------------------------------------ layout

def _layout(preheader: str, title: str, body: str, footer: str = "") -> str:
    """The frame every email shares: a dark violet header with the logo, a white card, and a footer."""
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light"><title>{escape(title)}</title></head>
<body style="margin:0;padding:0;background:{BG};">
<span style="display:none!important;opacity:0;color:transparent;max-height:0;overflow:hidden">{escape(preheader)}</span>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{BG};"><tr><td align="center" style="padding:24px 12px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;">
<tr><td style="background:{INK};background-image:linear-gradient(135deg,#1e1b4b 0%,{BRAND_DARK} 55%,{BRAND} 100%);border-radius:20px 20px 0 0;padding:26px 28px;">
  <table role="presentation" cellpadding="0" cellspacing="0"><tr>
    <td style="background:{BRAND};background-image:linear-gradient(135deg,#a78bfa,{BRAND},#c026d3);border-radius:10px;width:36px;height:36px;text-align:center;font-size:20px;color:#fff;font-family:Arial,sans-serif;">⚡</td>
    <td style="padding-left:10px;font-family:Arial,Helvetica,sans-serif;font-size:20px;font-weight:800;color:#ffffff;">Fresh<span style="color:#6ee7b7;">Jobs</span></td>
  </tr></table>
  <div style="font-family:Arial,Helvetica,sans-serif;font-size:24px;line-height:1.25;font-weight:800;color:#ffffff;margin-top:18px;">{escape(title)}</div>
</td></tr>
<tr><td style="background:#ffffff;border-radius:0 0 20px 20px;padding:24px 28px 28px;font-family:Arial,Helvetica,sans-serif;color:{INK};font-size:15px;line-height:1.55;">
{body}
</td></tr>
<tr><td style="padding:18px 8px;text-align:center;font-family:Arial,Helvetica,sans-serif;font-size:12px;line-height:1.6;color:{MUTED};">
{footer}<br>FreshJobs · jobs straight from company career sites, minutes after they're posted.
</td></tr>
</table></td></tr></table></body></html>"""


def _button(href: str, label: str, color: str = BRAND) -> str:
    return (f'<a href="{escape(href)}" style="display:inline-block;background:{color};color:#ffffff;text-decoration:none;'
            f'font-weight:700;font-size:15px;padding:12px 22px;border-radius:999px;">{escape(label)}</a>')


# ------------------------------------------------------------------------------------------------ account emails

def send_welcome(user, confirm_link: str) -> bool:
    name = (user.name or "").split(" ")[0] or "there"
    body = (f"<p style='margin:0 0 14px;'>Hi {escape(name)}, your FreshJobs account is ready.</p>"
            f"<p style='margin:0 0 20px;color:{MUTED};'>FreshJobs checks thousands of company career sites every few minutes, "
            "so you can apply to new roles before they reach LinkedIn, with H-1B, E-Verify and OPT signals on every job.</p>"
            f"<p style='margin:0 0 22px;'>{_button(confirm_link, 'Confirm my email')}</p>"
            f"<p style='margin:0;font-size:13px;color:{MUTED};'>Confirming lets you switch on job alerts. "
            "The link works for 48 hours. If you didn't sign up, ignore this email.</p>")
    text = (f"Hi {name}, your FreshJobs account is ready.\n\nConfirm your email (works for 48 hours):\n{confirm_link}\n\n"
            "If you didn't sign up, ignore this email.")
    sent, _ = send_email(user.email, "Welcome to FreshJobs: confirm your email", text,
                         _layout("Your account is ready. Confirm your email to switch on job alerts.", "Welcome to FreshJobs 🎉", body),
                         user_id=user.id)
    return sent


def send_reset(user, reset_link: str) -> bool:
    body = (f"<p style='margin:0 0 20px;'>Someone (hopefully you) asked to reset the password for {escape(user.email)}.</p>"
            f"<p style='margin:0 0 22px;'>{_button(reset_link, 'Choose a new password')}</p>"
            f"<p style='margin:0;font-size:13px;color:{MUTED};'>The link works once, for 1 hour. "
            "If you didn't ask for this, ignore this email: your password stays the same.</p>")
    text = f"Reset your FreshJobs password (works once, for 1 hour):\n{reset_link}\n\nIf you didn't ask for this, ignore this email."
    sent, _ = send_email(user.email, "Reset your FreshJobs password", text,
                         _layout("Choose a new password for FreshJobs.", "Reset your password", body), user_id=user.id)
    return sent


def _unsubscribe_headers(user) -> dict:
    link = _url(f"/api/alerts/unsubscribe?token={user.unsubscribe_token}")
    return {"List-Unsubscribe": f"<{link}>", "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}


def _alert_footer(user) -> str:
    return (f"You get these because you switched on job alerts for {escape(user.email)}. "
            f"<a href='{escape(_url('/?tab=profile'))}' style='color:{BRAND};'>Manage alerts</a> · "
            f"<a href='{escape(_url(f'/api/alerts/unsubscribe?token={user.unsubscribe_token}'))}' style='color:{BRAND};'>Unsubscribe</a>")


FREQUENCY_LABELS = {"instant": "as they come in (at most one email an hour)", "hourly": "once an hour", "daily": "once a day, each morning"}


def send_alerts_on(user, prefs) -> bool:
    roles = ", ".join(prefs.include_keywords[:8]) or "your roles"
    extras = []
    if prefs.alert_min_score:
        extras.append(f"only jobs matching your resume {prefs.alert_min_score}% or better")
    if prefs.hide_no_sponsor:
        extras.append("leaving out jobs that rule out sponsorship")
    body = (f"<p style='margin:0 0 14px;'>You'll get new jobs for <b>{escape(roles)}</b>, "
            f"{escape(FREQUENCY_LABELS.get(prefs.alert_frequency, 'as they come in'))}"
            + (f", {escape('; '.join(extras))}" if extras else "") + ".</p>"
            f"<p style='margin:0 0 20px;color:{MUTED};'>At most {prefs.alert_daily_cap} emails a day. "
            "You can change this, or switch alerts off, any time.</p>"
            f"<p style='margin:0;'>{_button(_url('/?tab=profile'), 'Alert settings')}</p>")
    text = (f"Job alerts are on for {roles}, {FREQUENCY_LABELS.get(prefs.alert_frequency, '')}. "
            f"At most {prefs.alert_daily_cap} emails a day.\nSettings: {_url('/?tab=profile')}")
    sent, _ = send_email(user.email, "Your FreshJobs alerts are on", text,
                         _layout("You'll get new matching jobs by email.", "Job alerts are on ✅", body, _alert_footer(user)),
                         _unsubscribe_headers(user), user_id=user.id)
    return sent


def send_test(to: str) -> tuple[bool, str]:
    body = (f"<p style='margin:0 0 14px;'>Email from FreshJobs reaches you.</p>"
            f"<p style='margin:0;color:{MUTED};'>Sent through {escape(settings.smtp_host)}:{settings.smtp_port}.</p>")
    return send_email(to, "FreshJobs test email", f"Email from FreshJobs reaches you (via {settings.smtp_host}).",
                      _layout("This is a test.", "It works ✉️", body))


# ------------------------------------------------------------------------------------------------ job alerts

def _ago(when: datetime | None, now: datetime) -> str:
    if when is None:
        return ""
    when = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
    mins = max(0, int((now - when).total_seconds() // 60))
    if mins < 1:
        return "just now"
    if mins < 60:
        return f"{mins} min ago"
    hours = mins // 60
    return f"{hours} h ago" if hours < 24 else f"{hours // 24} d ago"


_AVATAR = ["#7c3aed", "#db2777", "#2563eb", "#059669", "#d97706", "#0891b2", "#4f46e5", "#dc2626"]


def _avatar(name: str) -> str:
    initials = "".join(w[0] for w in name.split()[:2]).upper() or "?"
    color = _AVATAR[int(hashlib.md5(name.encode()).hexdigest(), 16) % len(_AVATAR)]
    return (f"<td width='44' valign='top' style='padding-right:12px;'><div style='width:44px;height:44px;border-radius:12px;"
            f"background:{color};color:#fff;font-weight:800;font-size:16px;line-height:44px;text-align:center;'>{escape(initials)}</div></td>")


def _chip(label: str, fg: str, bg: str) -> str:
    return (f"<span style='display:inline-block;margin:0 6px 6px 0;padding:3px 10px;border-radius:999px;background:{bg};"
            f"color:{fg};font-size:12px;font-weight:700;'>{escape(label)}</span>")


def _match_chip(score: int) -> str:
    fg, bg = ("#065f46", "#d1fae5") if score >= 75 else ("#1e40af", "#dbeafe") if score >= 60 else ("#92400e", "#fef3c7")
    return _chip(f"{score}% match", fg, bg)


def group_jobs(jobs: list[Job]) -> list[list[Job]]:
    """The same role at the same employer in several cities is one card (FanDuel's "Salesforce Tester" x3)."""
    groups: "OrderedDict[tuple, list[Job]]" = OrderedDict()
    for j in jobs:
        key = (j.company_key or (j.company_name or "").lower(), (j.title or "").strip().lower())
        groups.setdefault(key, []).append(j)
    return list(groups.values())


def _places(group: list[Job]) -> str:
    places = list(dict.fromkeys((j.location or "").strip() for j in group if (j.location or "").strip()))
    if not places:
        return "Location not given"
    shown = "; ".join(places[:3])
    return shown + (f" and {len(places) - 3} more" if len(places) > 3 else "")


def _job_card(group: list[Job], match: dict | None, now: datetime) -> tuple[str, str]:
    j = group[0]
    company = j.company_name or (j.company.name if j.company else "")
    posted = _ago(j.listed_at or j.first_seen_at, now)
    chips = []
    if match:
        chips.append(_match_chip(match["score"]))
    if j.sponsors_text:
        chips.append(_chip("Says it sponsors", "#065f46", "#d1fae5"))
    if j.student_visa == "welcome":
        chips.append(_chip("OPT/CPT welcome", "#065f46", "#d1fae5"))
    if j.h1b_filings:
        chips.append(_chip(f"H-1B · {j.h1b_filings:,}", "#5b21b6", "#ede9fe"))
    if j.everify == "yes" or j.everify_text:
        chips.append(_chip("E-Verify", "#1e40af", "#dbeafe"))
    if j.exp_level in ("entry", "mid"):
        chips.append(_chip({"entry": "Entry level", "mid": "Mid level"}[j.exp_level], "#334155", "#f1f5f9"))
    if j.no_sponsor:
        chips.append(_chip("No sponsorship", "#9f1239", "#ffe4e6"))
    html = f"""<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border:1px solid {LINE};border-radius:16px;margin:0 0 12px;">
<tr><td style="padding:16px;"><table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
{_avatar(company)}
<td valign="top">
  <a href="{escape(j.url)}" style="font-size:16px;font-weight:800;color:{INK};text-decoration:none;line-height:1.3;">{escape(j.title)}</a>
  <div style="font-size:13px;color:{MUTED};margin:3px 0 10px;">{escape(company)} · {escape(_places(group))}{f" · <span style='color:#059669;font-weight:700;'>posted {escape(posted)}</span>" if posted else ""}</div>
  <div>{''.join(chips)}</div>
  <div style="margin-top:6px;"><a href="{escape(j.url)}" style="display:inline-block;background:{BRAND};color:#fff;text-decoration:none;font-weight:700;font-size:13px;padding:8px 16px;border-radius:999px;">Apply ↗</a></div>
</td></tr></table></td></tr></table>"""
    text = f"{j.title} | {company} | {_places(group)}" + (f" | posted {posted}" if posted else "") + \
        (f" | {match['score']}% match" if match else "") + f"\n  {j.url}"
    return html, text


def build_alert(user, jobs: list[Job], matches: dict[int, dict], now: datetime) -> tuple[str, str, str]:
    """(subject, plain text, HTML) for one user's alert. matches: job id -> match brief (may be empty)."""
    groups = group_jobs(jobs)
    groups.sort(key=lambda g: -(matches.get(g[0].id) or {}).get("score", -1))  # best match first when scored
    n = len(groups)
    lead = groups[0][0]
    subject = f"{n} new job{'s' if n != 1 else ''}: {lead.title} at {lead.company_name or ''}".strip()
    if n > 1:
        subject += f" and {n - 1} more"
    cards, texts = zip(*(_job_card(g, matches.get(g[0].id), now) for g in groups[:20]))
    more = (f"<p style='margin:4px 0 0;color:{MUTED};font-size:13px;'>…and {n - 20} more in "
            f"<a href='{escape(_url('/'))}' style='color:{BRAND};'>your feed</a>.</p>") if n > 20 else ""
    body = (f"<p style='margin:0 0 18px;color:{MUTED};'>New roles matching your For you feed, straight from the company "
            "boards. The sooner you apply, the fewer applicants you're up against.</p>"
            + "".join(cards) + more +
            f"<p style='margin:18px 0 0;text-align:center;'>{_button(_url('/'), 'Open my feed')}</p>")
    title = f"{n} new job{'s' if n != 1 else ''} for you"
    return subject, "\n\n".join(texts) + f"\n\nOpen your feed: {_url('/')}\nUnsubscribe: " + \
        _url(f"/api/alerts/unsubscribe?token={user.unsubscribe_token}"), \
        _layout(f"{lead.title} at {lead.company_name or ''} and more, minutes after posting.", title, body, _alert_footer(user))


def send_job_alert(user, jobs: list[Job], matches: dict[int, dict]) -> bool:
    if not jobs:
        return False
    subject, text, html = build_alert(user, jobs, matches, datetime.now(timezone.utc))
    sent, _ = send_email(user.email, subject, text, html, _unsubscribe_headers(user), user_id=user.id)
    if sent:
        log.info("Sent an alert with %d jobs to user %d", len(jobs), user.id)
    return sent
