"""Job alerts, per user and opt-in. `deliver_alerts()` runs after every fast poll.

A user gets an email only when all of these hold:
- they switched alerts on (UserPrefs.email_alerts; off by default) and confirmed their email address;
- they're due: "instant" at most once an hour, "hourly" once per clock hour, "daily" once a day after 8 am
  US Eastern;
- they haven't reached their daily cap (alert_daily_cap, 10 by default);
- there are new jobs for them: found since their watermark (UserPrefs.alert_watermark, which starts when
  they switch alerts on, so there's no backlog), open, not repeats or baseline, in the US, matching their own
  roles, passing their sponsorship and seniority settings, and their minimum resume match if they set one.

The watermark moves to the newest job sent, so each job is mailed to each user at most once.
"""

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db import SessionLocal
from app.models import Job, User, UserPrefs
from app.pipeline.filters import role_matches

log = logging.getLogger(__name__)
EASTERN = ZoneInfo("America/New_York")
DAILY_HOUR = 8
MAX_JOBS = 60  # looked at per email (the email shows 20 cards and "and N more")


def is_due(prefs: UserPrefs, now: datetime) -> bool:
    last = prefs.last_alert_at
    if last is not None and last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    local = now.astimezone(EASTERN)
    if prefs.alert_frequency == "daily":
        if local.hour < DAILY_HOUR:
            return False
        return last is None or last.astimezone(EASTERN).date() < local.date()
    if prefs.alert_frequency == "hourly":
        return last is None or last.astimezone(EASTERN).replace(minute=0, second=0, microsecond=0) < \
            local.replace(minute=0, second=0, microsecond=0)
    return last is None or now - last >= timedelta(hours=1)  # instant, at most hourly


def under_cap(prefs: UserPrefs, now: datetime) -> bool:
    today = now.astimezone(EASTERN).date()
    sent = prefs.alerts_sent_today if prefs.alerts_sent_on == today else 0
    return sent < max(1, prefs.alert_daily_cap or 10)


def jobs_for(session, user: User, prefs: UserPrefs, now: datetime) -> list[Job]:
    since = prefs.alert_watermark or now
    if since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    candidates = session.scalars(
        select(Job).where(Job.closed_at.is_(None), Job.is_repost.is_(False), Job.first_seen_at > since,
                          Job.role_match.is_(True), Job.us_eligible.is_(True), Job.baseline.is_(False))
        .order_by(Job.first_seen_at.desc()).limit(2000).options(selectinload(Job.company)))
    out = []
    for j in candidates:
        if not role_matches(j.title or "", prefs.include_keywords, prefs.exclude_keywords):
            continue
        if prefs.hide_no_sponsor and j.no_sponsor:
            continue
        if prefs.exclude_senior and j.exp_level == "senior":
            continue
        out.append(j)
        if len(out) >= MAX_JOBS:
            break
    return out


def deliver_alerts(now: datetime | None = None) -> int:
    """Send every due alert; returns how many emails went out."""
    from app.main import _match_brief  # the brief a job card shows
    from app.notify.email import send_job_alert, smtp_configured
    from app.pipeline.scoring import ensure_scores

    if not smtp_configured():
        return 0
    now = now or datetime.now(timezone.utc)
    sent = 0
    with SessionLocal() as session:
        rows = session.execute(
            select(User, UserPrefs).join(UserPrefs, UserPrefs.user_id == User.id)
            .where(UserPrefs.email_alerts.is_(True), User.email_verified_at.is_not(None), User.disabled.is_(False))).all()
        for user, prefs in rows:
            if not is_due(prefs, now) or not under_cap(prefs, now):
                continue
            jobs = jobs_for(session, user, prefs, now)
            matches = {i: _match_brief(m) for i, m in ensure_scores(session, [j.id for j in jobs], user.id).items()}
            if prefs.alert_min_score:
                jobs = [j for j in jobs if (matches.get(j.id) or {}).get("score", -1) >= prefs.alert_min_score]
            if not jobs:
                continue
            from app.activity import record

            if send_job_alert(user, jobs, matches):
                record("alert_sent", user.id, f"{len(jobs)} jobs ({prefs.alert_frequency})")
                today = now.astimezone(EASTERN).date()
                prefs.alerts_sent_today = (prefs.alerts_sent_today if prefs.alerts_sent_on == today else 0) + 1
                prefs.alerts_sent_on = today
                prefs.last_alert_at = now
                prefs.alert_watermark = max(j.first_seen_at for j in jobs)
                sent += 1
            else:
                record("alert_failed", user.id, f"{len(jobs)} jobs: the email didn't send")
            session.commit()
    if sent:
        log.info("Sent %d alert emails", sent)
    return sent
