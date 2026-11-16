"""The admin's activity log: what happened to which account, and when (models.Activity).

`record()` never raises: a failure to log must not fail a sign-up or a login. Details are short, human-readable
and never contain passwords, tokens, or resume text.

Kinds, by area:
- account: signup, login, login_failed, login_locked, logout, email_verified, verification_resent,
  password_reset_requested, password_reset, password_changed, account_deleted
- alerts: alerts_on, alerts_off, alert_sent, alert_failed, unsubscribed
- resume: resume_uploaded, resume_deleted
- usage: recruiter_lookup (a Hunter.io search, which spends the admin's credits)
- email: email_sent, email_failed (every email the portal sends; user_id is set when the address is an account)
- admin: admin_viewed_user, admin_disabled_user, admin_enabled_user, admin_alerts_off, admin_resent_verification,
  admin_deleted_user (actor_id is the admin)
"""

import logging

from fastapi import Request

from app.db import SessionLocal
from app.models import Activity

log = logging.getLogger(__name__)


def client_ip(request: Request | None) -> str:
    if request is None:
        return ""
    forwarded = request.headers.get("x-forwarded-for", "")  # behind the cloud server's proxy
    return (forwarded.split(",")[0].strip() or (request.client.host if request.client else ""))[:64]


def record(kind: str, user_id: int | None = None, detail: str = "", request: Request | None = None,
           actor_id: int | None = None) -> None:
    """Add one event, in its own session so it's saved even if the caller's transaction rolls back."""
    try:
        with SessionLocal() as session:
            session.add(Activity(kind=kind, user_id=user_id, actor_id=actor_id, detail=(detail or "")[:500],
                                 ip=client_ip(request)))
            session.commit()
    except Exception:
        log.warning("Couldn't record activity %s for user %s", kind, user_id, exc_info=True)
