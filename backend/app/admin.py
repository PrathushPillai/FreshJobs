"""The admin dashboard's API: every user and their activity, the portal's health, and account actions.

All routes need the admin (auth.admin_user). Privacy, as the privacy note says:
- The lists show account facts and activity (when someone signed up, logged in, got an alert), never resume text.
- Opening one user ("view as user") shows what the portal holds for them, to fix problems (roles, alert settings,
  what's read from the resume, the tracker, their next alert), and is itself logged as admin_viewed_user.
- Actions (switch off an account, stop its alerts, resend its confirmation email, delete it) are logged with the
  admin as actor.
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.orm import Session

from app.activity import record
from app.auth import admin_user, delete_account_data, make_token, VERIFY_HOURS
from app.config import settings
from app.db import get_session
from app.models import Activity, Application, AuthSession, JobMatch, User, UserPrefs, UserProfile

router = APIRouter(prefix="/api/admin")
PROBLEMS = ("email_failed", "alert_failed", "login_locked")


def _since(hours: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=hours)


def _count(db: Session, kind: str | tuple, hours: int) -> int:
    kinds = (kind,) if isinstance(kind, str) else kind
    return db.scalar(select(func.count()).select_from(Activity).where(Activity.kind.in_(kinds), Activity.at >= _since(hours))) or 0


def _event(a: Activity, emails: dict[int, str]) -> dict:
    return {"id": a.id, "at": a.at, "kind": a.kind, "detail": a.detail, "ip": a.ip, "user_id": a.user_id,
            "user_email": emails.get(a.user_id) if a.user_id else None,
            "actor_email": emails.get(a.actor_id) if a.actor_id else None}


def _emails(db: Session, ids) -> dict[int, str]:
    ids = {i for i in ids if i}
    return dict(db.execute(select(User.id, User.email).where(User.id.in_(ids))).all()) if ids else {}


@router.get("/overview")
def overview(db: Session = Depends(get_session), _: User = Depends(admin_user)):
    """The numbers at the top of the dashboard, and the latest problems."""
    from app.main import _last_poll
    from app.notify.email import email_mode

    users = db.scalar(select(func.count()).select_from(User)) or 0
    active = db.scalar(select(func.count(func.distinct(Activity.user_id))).where(
        Activity.at >= _since(24), Activity.user_id.is_not(None),
        Activity.kind.in_(("login", "signup", "resume_uploaded", "alerts_on", "password_changed")))) or 0
    problems = list(db.scalars(select(Activity).where(Activity.kind.in_(PROBLEMS)).order_by(Activity.id.desc()).limit(8)))
    poll = _last_poll.get("fast") or {}
    return {
        "users": users,
        "confirmed": db.scalar(select(func.count()).select_from(User).where(User.email_verified_at.is_not(None))) or 0,
        "disabled": db.scalar(select(func.count()).select_from(User).where(User.disabled.is_(True))) or 0,
        "alerts_on": db.scalar(select(func.count()).select_from(UserPrefs).where(
            UserPrefs.email_alerts.is_(True), UserPrefs.user_id.is_not(None))) or 0,
        "with_resume": db.scalar(select(func.count()).select_from(UserProfile).where(
            UserProfile.user_id.is_not(None), UserProfile.resume_text != "")) or 0,
        "signups_7d": db.scalar(select(func.count()).select_from(User).where(User.created_at >= _since(24 * 7))) or 0,
        "active_24h": active,
        "emails_24h": _count(db, "email_sent", 24), "emails_failed_24h": _count(db, "email_failed", 24),
        "email_daily_limit": settings.email_daily_limit, "email_mode": email_mode(),
        "email_sender": settings.alert_from or settings.smtp_user,
        "alerts_24h": _count(db, "alert_sent", 24),
        "failed_logins_24h": _count(db, ("login_failed", "login_locked"), 24),
        "recruiter_lookups_24h": _count(db, "recruiter_lookup", 24),
        "last_poll": {"finished_at": poll.get("finished_at"), "sources": poll.get("sources"), "errors": poll.get("errors"),
                      "new_jobs": poll.get("new_jobs")} if poll else None,
        "problems": [_event(a, _emails(db, [p.user_id for p in problems])) for a in problems],
    }


@router.get("/users")
def list_users(q: str = "", limit: int = Query(200, ge=1, le=1000), db: Session = Depends(get_session),
               _: User = Depends(admin_user)):
    """Every account with its facts: confirmed, alerts, resume (yes/no), tracker size, last activity."""
    stmt = select(User).order_by(User.created_at.desc()).limit(limit)
    if q.strip():
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(or_(func.lower(User.email).like(like), func.lower(User.name).like(like)))
    users = list(db.scalars(stmt))
    ids = [u.id for u in users]
    prefs = {p.user_id: p for p in db.scalars(select(UserPrefs).where(UserPrefs.user_id.in_(ids)))}
    resumes = {p.user_id: p for p in db.scalars(select(UserProfile).where(UserProfile.user_id.in_(ids)))}
    tracked = dict(db.execute(select(Application.user_id, func.count()).where(Application.user_id.in_(ids))
                              .group_by(Application.user_id)).all())
    last = dict(db.execute(select(Activity.user_id, func.max(Activity.at)).where(Activity.user_id.in_(ids))
                           .group_by(Activity.user_id)).all())
    week = dict(db.execute(select(Activity.user_id, func.count()).where(Activity.user_id.in_(ids), Activity.at >= _since(24 * 7))
                           .group_by(Activity.user_id)).all())
    out = []
    for u in users:
        p, r = prefs.get(u.id), resumes.get(u.id)
        out.append({
            "id": u.id, "email": u.email, "name": u.name, "is_admin": u.is_admin, "disabled": u.disabled,
            "email_verified": u.email_verified_at is not None, "created_at": u.created_at, "last_login_at": u.last_login_at,
            "last_activity_at": last.get(u.id), "events_7d": week.get(u.id, 0),
            "alerts_on": bool(p and p.email_alerts), "alert_frequency": p.alert_frequency if p else None,
            "roles": (p.include_keywords or [])[:6] if p else [],
            "has_resume": bool(r and r.resume_text), "tracked": tracked.get(u.id, 0),
        })
    return out


@router.get("/activity")
def activity(user_id: int | None = None, kind: str = "", problems: bool = False, limit: int = Query(100, ge=1, le=500),
             before_id: int | None = None, db: Session = Depends(get_session), _: User = Depends(admin_user)):
    """The activity log, newest first: everything, one user's, one kind, or only problems."""
    stmt = select(Activity).order_by(Activity.id.desc()).limit(limit)
    if user_id is not None:
        stmt = stmt.where(or_(Activity.user_id == user_id, Activity.actor_id == user_id))
    if kind:
        stmt = stmt.where(Activity.kind == kind)
    if problems:
        stmt = stmt.where(Activity.kind.in_(PROBLEMS + ("login_failed",)))
    if before_id:
        stmt = stmt.where(Activity.id < before_id)
    rows = list(db.scalars(stmt))
    emails = _emails(db, [a.user_id for a in rows] + [a.actor_id for a in rows])
    return [_event(a, emails) for a in rows]


def _target(db: Session, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "No such user.")
    return user


@router.get("/users/{user_id}")
def view_user(user_id: int, request: Request, db: Session = Depends(get_session), admin: User = Depends(admin_user)):
    """View as user: what the portal holds for one account, to fix a problem. Logged (admin_viewed_user)."""
    from app.main import _match_brief
    from app.pipeline.alerts import is_due, jobs_for, under_cap
    from app.pipeline.scoring import get_profile
    from app.prefs import get_prefs

    user = _target(db, user_id)
    if user.id != admin.id:
        record("admin_viewed_user", user.id, "opened their account in the admin dashboard", request, actor_id=admin.id)
    prefs = get_prefs(db, user.id)
    profile = get_profile(db, user.id)
    now = datetime.now(timezone.utc)
    pending = jobs_for(db, user, prefs, now) if prefs.email_alerts else []
    top = list(db.execute(select(JobMatch.job_id, JobMatch.score).where(
        JobMatch.user_id == user.id, JobMatch.profile_version == (profile.version if profile else -1))
        .order_by(JobMatch.score.desc()).limit(5)).all()) if profile else []
    from app.models import Job

    titles = dict(db.execute(select(Job.id, Job.title).where(Job.id.in_([j for j, _ in top]))).all()) if top else {}
    apps = list(db.scalars(select(Application).where(Application.user_id == user.id).order_by(Application.created_at.desc()).limit(50)))
    recent = list(db.scalars(select(Activity).where(or_(Activity.user_id == user.id, Activity.actor_id == user.id))
                             .order_by(Activity.id.desc()).limit(40)))
    return {
        "account": {"id": user.id, "email": user.email, "name": user.name, "is_admin": user.is_admin, "disabled": user.disabled,
                    "email_verified": user.email_verified_at is not None, "created_at": user.created_at,
                    "last_login_at": user.last_login_at,
                    "sessions": db.scalar(select(func.count()).select_from(AuthSession).where(
                        AuthSession.user_id == user.id, AuthSession.expires_at > now)) or 0},
        "roles": prefs.include_keywords, "excluded": prefs.exclude_keywords,
        "alerts": {"on": prefs.email_alerts, "frequency": prefs.alert_frequency, "min_score": prefs.alert_min_score,
                   "daily_cap": prefs.alert_daily_cap, "sent_today": prefs.alerts_sent_today if prefs.alerts_sent_on else 0,
                   "last_alert_at": prefs.last_alert_at, "due_now": prefs.email_alerts and is_due(prefs, now) and under_cap(prefs, now),
                   "hide_no_sponsor": prefs.hide_no_sponsor, "exclude_senior": prefs.exclude_senior,
                   "next_alert": [{"title": j.title, "company": j.company_name, "first_seen_at": j.first_seen_at} for j in pending[:8]],
                   "next_alert_count": len(pending)},
        # What was read from the resume: not the resume itself.
        "resume": {"uploaded": bool(profile and profile.resume_text), "filename": profile.filename if profile else "",
                   "uploaded_at": profile.uploaded_at if profile else None, "skills": profile.skills if profile else [],
                   "years": profile.years if profile else None, "degree": profile.degree if profile else "",
                   "target_roles": profile.target_roles if profile else [], "visa_status": profile.visa_status if profile else "",
                   "needs_sponsorship": profile.needs_sponsorship if profile else None,
                   "scored": db.scalar(select(func.count()).select_from(JobMatch).where(JobMatch.user_id == user.id)) or 0,
                   "top_matches": [{"job_id": j, "title": titles.get(j, ""), "score": s} for j, s in top]},
        "tracker": [{"company": a.company, "title": a.title, "status": a.status, "applied_at": a.applied_at,
                     "created_at": a.created_at} for a in apps],
        "activity": [_event(a, _emails(db, [a.actor_id for a in recent] + [user.id])) for a in recent],
    }


class DisableIn(BaseModel):
    disabled: bool


@router.post("/users/{user_id}/disable")
def disable_user(user_id: int, body: DisableIn, request: Request, db: Session = Depends(get_session),
                 admin: User = Depends(admin_user)):
    """Switch an account off (it can't log in and gets no email; its sessions end) or back on."""
    user = _target(db, user_id)
    if user.id == admin.id:
        raise HTTPException(400, "You can't switch off your own admin account.")
    user.disabled = body.disabled
    if body.disabled:
        db.execute(delete(AuthSession).where(AuthSession.user_id == user.id))
    db.commit()
    record("admin_disabled_user" if body.disabled else "admin_enabled_user", user.id, "", request, actor_id=admin.id)
    return {"ok": True, "disabled": user.disabled}


@router.post("/users/{user_id}/alerts-off")
def stop_alerts(user_id: int, request: Request, db: Session = Depends(get_session), admin: User = Depends(admin_user)):
    user = _target(db, user_id)
    db.execute(update(UserPrefs).where(UserPrefs.user_id == user.id).values(email_alerts=False))
    db.commit()
    record("admin_alerts_off", user.id, "", request, actor_id=admin.id)
    return {"ok": True}


@router.post("/users/{user_id}/resend-verification")
def resend_for(user_id: int, request: Request, db: Session = Depends(get_session), admin: User = Depends(admin_user)):
    from app.notify.email import send_welcome

    user = _target(db, user_id)
    if user.email_verified_at:
        return {"sent": False, "already_verified": True}
    link = f"{settings.public_url.rstrip('/')}/?verify={make_token(db, user, 'verify', VERIFY_HOURS)}"
    sent = send_welcome(user, link)
    record("admin_resent_verification", user.id, "sent" if sent else "not sent", request, actor_id=admin.id)
    return {"sent": sent}


@router.delete("/users/{user_id}")
def delete_user(user_id: int, request: Request, db: Session = Depends(get_session), admin: User = Depends(admin_user)):
    """Delete an account and everything that's theirs (resume, roles, tracker, scores). Can't be undone."""
    user = _target(db, user_id)
    if user.id == admin.id:
        raise HTTPException(400, "You can't delete your own admin account.")
    email = user.email
    delete_account_data(db, user.id)
    record("admin_deleted_user", user_id, email, request, actor_id=admin.id)
    return {"ok": True}
