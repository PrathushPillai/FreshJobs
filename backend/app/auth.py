"""Accounts: sign-up, login, sessions, email confirmation and password reset.

- Passwords are hashed with scrypt (Python's hashlib, no extra library); only the hash is stored.
- A login sets an HTTP-only cookie holding a random token; the database keeps only its SHA-256, so a leaked
  database can't be used to log in. The same goes for the one-time links in emails (AuthToken).
- Every /api route needs a login (`require_login`, installed app-wide in main.py) except PUBLIC_PATHS.
  Routes that change what the whole portal collects (sources, "Check now") need the admin (`admin_user`).
- The admin is the account whose email is settings.admin_email, or with that unset, the first account. It
  takes over the data from before accounts existed: roles, resume, tracker and match scores
  (`claim_legacy_data`).
- Repeated failed logins for one email or from one address are refused for a while (`_too_many_failures`).
"""

import hashlib
import hmac
import logging
import re
import secrets
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.orm import Session

from app.activity import record
from app.config import settings
from app.db import get_session
from app.models import AuthSession, AuthToken, User, UserPrefs

log = logging.getLogger(__name__)
router = APIRouter()

COOKIE = "fj_session"
PUBLIC_PATHS = {"/api/auth/signup", "/api/auth/login", "/api/auth/logout", "/api/auth/me", "/api/auth/verify",
                "/api/auth/forgot", "/api/auth/reset", "/api/alerts/unsubscribe", "/api/status",
                "/api/support"}  # support: also from the login screen (rate limited)
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[A-Za-z]{2,}$")
MIN_PASSWORD = 8
VERIFY_HOURS, RESET_HOURS = 48, 1
FAIL_LIMIT, FAIL_WINDOW = 5, 15 * 60  # 5 failed logins in 15 minutes, per email and per client address
_failures: dict[str, list[float]] = defaultdict(list)


# --------------------------------------------------------------------------------------------- passwords

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    n, r, p = 2**14, 8, 1
    digest = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, dklen=32)
    return f"scrypt${n}${r}${p}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, digest = stored.split("$")
        got = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=32)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got.hex(), digest)


def _check_password(password: str) -> None:
    if len(password or "") < MIN_PASSWORD:
        raise HTTPException(400, f"Use at least {MIN_PASSWORD} characters for the password.")
    if len(password) > 200:
        raise HTTPException(400, "That password is too long.")


def _sha(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# --------------------------------------------------------------------------------------------- sessions

def _start_session(db: Session, user: User, response: Response) -> None:
    token = secrets.token_urlsafe(32)
    db.add(AuthSession(token_hash=_sha(token), user_id=user.id,
                       expires_at=datetime.now(timezone.utc) + timedelta(days=settings.session_days)))
    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    response.set_cookie(COOKIE, token, max_age=settings.session_days * 86400, httponly=True, samesite="lax",
                        secure=settings.secure_cookies, path="/")


def _session_row(request: Request, db: Session) -> AuthSession | None:
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    row = db.get(AuthSession, _sha(token))
    if row is None or row.expires_at < datetime.now(timezone.utc):
        return None
    return row


def user_from_request(request: Request, db: Session) -> User | None:
    """The user the portal shows: the logged-in user, or while the admin views as someone, that someone."""
    row = _session_row(request, db)
    if row is None:
        return None
    if row.view_as_user_id is not None:
        admin = db.get(User, row.user_id)
        viewed = db.get(User, row.view_as_user_id)
        if admin is None or not admin.is_admin or viewed is None:
            return None
        request.state.viewing_as = viewed
        request.state.real_user = admin
        return viewed
    return db.get(User, row.user_id)


def viewing_as(request: Request) -> bool:
    return getattr(request.state, "viewing_as", None) is not None


def current_user(request: Request, db: Session = Depends(get_session)) -> User:
    """The logged-in user, or 401. FastAPI caches it per request, so routes can ask for it again for free."""
    if hasattr(request.state, "user"):
        return request.state.user
    user = user_from_request(request, db)
    if user is None or (user.disabled and not viewing_as(request)):  # the admin may view a switched-off account
        raise HTTPException(401, "Please log in.")
    request.state.user = user
    return user


VIEW_AS_ALLOWED = {"/api/admin/view-as/stop", "/api/auth/logout"}


def require_login(request: Request, db: Session = Depends(get_session)) -> None:
    """App-wide: every /api route needs a login, except the public ones. While the admin views the portal as a
    user it's read-only: only GET requests (and ending the view) go through, and nothing that spends credits."""
    if request.url.path in PUBLIC_PATHS or not request.url.path.startswith("/api/"):
        return
    current_user(request, db)
    if viewing_as(request):
        if request.url.path in VIEW_AS_ALLOWED:
            return
        spends = request.url.path.endswith("/recruiters") and request.query_params.get("lookup") in ("true", "1")
        if request.method not in ("GET", "HEAD") or spends:
            raise HTTPException(403, "You're viewing the portal as this user: it's read-only. Exit the view to make changes.")


def admin_user(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(403, "Only the portal's admin can do that.")
    return user


# --------------------------------------------------------------------------------------------- one-time links

def make_token(db: Session, user: User, purpose: str, hours: int) -> str:
    token = secrets.token_urlsafe(32)
    db.add(AuthToken(token_hash=_sha(token), user_id=user.id, purpose=purpose,
                     expires_at=datetime.now(timezone.utc) + timedelta(hours=hours)))
    db.commit()
    return token


def use_token(db: Session, token: str, purpose: str) -> User | None:
    row = db.get(AuthToken, _sha(token or ""))
    now = datetime.now(timezone.utc)
    if row is None or row.purpose != purpose or row.used_at is not None or row.expires_at < now:
        return None
    row.used_at = now
    return db.get(User, row.user_id)


# --------------------------------------------------------------------------------------------- brute force

def _too_many_failures(*keys: str) -> bool:
    now = time.monotonic()
    for key in keys:
        _failures[key] = [t for t in _failures[key] if now - t < FAIL_WINDOW]
        if len(_failures[key]) >= FAIL_LIMIT:
            return True
    return False


def _record_failure(*keys: str) -> None:
    for key in keys:
        _failures[key].append(time.monotonic())


# --------------------------------------------------------------------------------------------- data from before accounts

def claim_legacy_data(db: Session, user: User) -> None:
    """Give the admin the single-user data from before accounts: roles and settings, resume, tracker, scores."""
    db.execute(update(UserPrefs).where(UserPrefs.user_id.is_(None)).values(user_id=user.id, email_alerts=False))
    db.execute(text("UPDATE user_profile SET user_id = :u WHERE user_id IS NULL"), {"u": user.id})
    db.execute(text("UPDATE applications SET user_id = :u WHERE user_id IS NULL"), {"u": user.id})
    has_old = db.execute(text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='job_matches'")).first() \
        if db.bind.dialect.name == "sqlite" else None
    if has_old:
        db.execute(text("INSERT OR IGNORE INTO user_job_matches (user_id, job_id, profile_version, score, detail) "
                        "SELECT :u, job_id, profile_version, score, detail FROM job_matches"), {"u": user.id})
    db.commit()


# --------------------------------------------------------------------------------------------- routes

class SignupIn(BaseModel):
    name: str = ""
    email: str
    password: str


class LoginIn(BaseModel):
    email: str
    password: str


class TokenIn(BaseModel):
    token: str


class ResetIn(BaseModel):
    token: str
    password: str


class EmailIn(BaseModel):
    email: str


class PasswordIn(BaseModel):
    current: str
    new: str


def _me(user: User) -> dict:
    return {"id": user.id, "email": user.email, "name": user.name, "is_admin": user.is_admin,
            "email_verified": user.email_verified_at is not None}


def _client(request: Request) -> str:
    return "ip:" + (request.client.host if request.client else "?")


@router.post("/api/auth/signup")
def signup(body: SignupIn, request: Request, response: Response, db: Session = Depends(get_session)):
    from app.notify.email import send_welcome
    from app.prefs import get_prefs

    from app.ratelimit import limits

    # Every sign-up sends a welcome email from the site's mail account: a script signing up in a loop would
    # get that account flagged for spam. Per client address, then all sign-ups together.
    if not (limits.allow("signup:" + _client(request), settings.signups_per_hour_per_client, 3600)
            and limits.allow("signup:all", settings.signups_per_hour, 3600)):
        record("signup_limited", None, _client(request), request)
        raise HTTPException(429, "Too many sign-ups right now. Please try again in an hour.")
    email = body.email.strip().lower()
    if not _EMAIL.match(email):
        raise HTTPException(400, "That doesn't look like an email address.")
    _check_password(body.password)
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(409, "There's already an account with that email. Log in instead.")
    first = db.scalar(select(func.count()).select_from(User)) == 0
    admin = email == settings.admin_email.strip().lower() if settings.admin_email else first
    user = User(email=email, name=body.name.strip()[:120], password_hash=hash_password(body.password), is_admin=admin,
                unsubscribe_token=secrets.token_urlsafe(32))
    db.add(user)
    db.commit()
    if admin:
        claim_legacy_data(db, user)
    get_prefs(db, user.id)  # their own roles and alert settings (alerts off until they switch them on)
    record("signup", user.id, "admin" if admin else "", request)
    link = f"{settings.public_url.rstrip('/')}/?verify={make_token(db, user, 'verify', VERIFY_HOURS)}"
    if not send_welcome(user, link):
        log.info("Welcome email not sent (email not set up); confirmation link for %s: %s", email, link)
    _start_session(db, user, response)
    return _me(user)


@router.post("/api/auth/login")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_session)):
    email = body.email.strip().lower()
    keys = ("email:" + email, _client(request))
    user = db.scalar(select(User).where(User.email == email))
    if _too_many_failures(*keys):
        record("login_locked", user.id if user else None, email, request)
        raise HTTPException(429, "Too many attempts. Wait 15 minutes, or reset your password.")
    if user is None or not verify_password(body.password, user.password_hash):
        _record_failure(*keys)
        record("login_failed", user.id if user else None, "" if user else f"no account: {email}", request)
        raise HTTPException(401, "That email and password don't match.")
    if user.disabled:
        record("login_failed", user.id, "account switched off", request)
        raise HTTPException(403, "This account has been switched off. Contact the portal's admin.")
    _start_session(db, user, response)
    record("login", user.id, "", request)
    return _me(user)


@router.post("/api/auth/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_session)):
    token = request.cookies.get(COOKIE)
    if token:
        row = db.get(AuthSession, _sha(token))
        if row:
            record("logout", row.user_id, "", request)
        db.execute(delete(AuthSession).where(AuthSession.token_hash == _sha(token)))
        db.commit()
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/api/auth/me")
def me(request: Request, db: Session = Depends(get_session)):
    user = user_from_request(request, db)
    if user is None:
        raise HTTPException(401, "Not logged in.")
    out = _me(user)
    if viewing_as(request):
        row = _session_row(request, db)
        out["viewing_as"] = {"admin_email": request.state.real_user.email, "until": row.expires_at if row else None}
    return out


VIEW_AS_MINUTES = 60


def _swap_session(db: Session, request: Request, response: Response, admin: User, view_as: User | None) -> None:
    """Replace the current login with a new one (viewing as someone, or the admin's own). A new cookie, so the
    60-second page cache, keyed by cookie, never serves one user's pages to the other."""
    old = request.cookies.get(COOKIE)
    if old:
        db.execute(delete(AuthSession).where(AuthSession.token_hash == _sha(old)))
    token = secrets.token_urlsafe(32)
    minutes = VIEW_AS_MINUTES if view_as else settings.session_days * 24 * 60
    db.add(AuthSession(token_hash=_sha(token), user_id=admin.id, view_as_user_id=view_as.id if view_as else None,
                       expires_at=datetime.now(timezone.utc) + timedelta(minutes=minutes)))
    db.commit()
    response.set_cookie(COOKIE, token, max_age=settings.session_days * 86400, httponly=True, samesite="lax",
                        secure=settings.secure_cookies, path="/")


@router.post("/api/admin/users/{user_id}/view-as")
def start_view_as(user_id: int, request: Request, response: Response, db: Session = Depends(get_session)):
    """The admin sees the portal exactly as this user does, read-only, for up to an hour. Logged."""
    admin = current_user(request, db)
    if viewing_as(request) or not admin.is_admin:
        raise HTTPException(403, "Only the portal's admin can do that.")
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(404, "No such user.")
    if target.id == admin.id:
        raise HTTPException(400, "That's you.")
    _swap_session(db, request, response, admin, target)
    record("admin_viewed_user", target.id, "started viewing the portal as them (read-only)", request, actor_id=admin.id)
    return _me(target) | {"viewing_as": {"admin_email": admin.email}}


@router.post("/api/admin/view-as/stop")
def stop_view_as(request: Request, response: Response, db: Session = Depends(get_session)):
    row = _session_row(request, db)
    if row is not None and row.view_as_user_id is None:
        user = db.get(User, row.user_id)  # already back (a second click): nothing to undo
        if user is not None and user.is_admin:
            return _me(user)
    if row is None or row.view_as_user_id is None:
        raise HTTPException(400, "You're not viewing as anyone.")
    admin = db.get(User, row.user_id)
    viewed = row.view_as_user_id
    _swap_session(db, request, response, admin, None)
    record("admin_viewed_user", viewed, "stopped viewing as them", request, actor_id=admin.id)
    return _me(admin)


@router.post("/api/auth/verify")
def verify_email(body: TokenIn, db: Session = Depends(get_session)):
    user = use_token(db, body.token, "verify")
    if user is None:
        raise HTTPException(400, "That confirmation link is invalid or has expired. Ask for a new one in My profile.")
    user.email_verified_at = user.email_verified_at or datetime.now(timezone.utc)
    db.commit()
    record("email_verified", user.id)
    return _me(user)


@router.post("/api/auth/resend-verification")
def resend_verification(user: User = Depends(current_user), db: Session = Depends(get_session)):
    from app.notify.email import send_welcome

    from app.ratelimit import limits

    if user.email_verified_at:
        return {"sent": False, "already_verified": True}
    if not limits.allow(f"verify:{user.id}", settings.verification_emails_per_hour, 3600):
        raise HTTPException(429, "We've sent a few confirmation emails already. Check your inbox and spam folder, "
                                 "or try again in an hour.")
    link = f"{settings.public_url.rstrip('/')}/?verify={make_token(db, user, 'verify', VERIFY_HOURS)}"
    sent = send_welcome(user, link)
    if not sent:
        log.info("Confirmation email not sent (email not set up); link for %s: %s", user.email, link)
    record("verification_resent", user.id, "sent" if sent else "not sent")
    return {"sent": sent}


@router.post("/api/auth/forgot")
def forgot_password(body: EmailIn, request: Request, db: Session = Depends(get_session)):
    """Always the same answer, so it can't be used to find out who has an account."""
    from app.notify.email import send_reset

    email = body.email.strip().lower()
    if not _too_many_failures("reset:" + email, _client(request)):
        _record_failure("reset:" + email)  # at most a few reset emails per address per 15 minutes
        user = db.scalar(select(User).where(User.email == email))
        if user:
            record("password_reset_requested", user.id, "", request)
            link = f"{settings.public_url.rstrip('/')}/?reset={make_token(db, user, 'reset', RESET_HOURS)}"
            if not send_reset(user, link):
                log.info("Reset email not sent (email not set up); link for %s: %s", email, link)
    return {"ok": True}


@router.post("/api/auth/reset")
def reset_password(body: ResetIn, response: Response, db: Session = Depends(get_session)):
    _check_password(body.password)
    user = use_token(db, body.token, "reset")
    if user is None:
        raise HTTPException(400, "That reset link is invalid or has expired. Ask for a new one.")
    user.password_hash = hash_password(body.password)
    user.email_verified_at = user.email_verified_at or datetime.now(timezone.utc)  # they read the email
    db.execute(delete(AuthSession).where(AuthSession.user_id == user.id))  # log out everywhere else
    db.commit()
    _failures.pop("email:" + user.email, None)
    _start_session(db, user, response)
    record("password_reset", user.id)
    return _me(user)


@router.post("/api/auth/password")
def change_password(body: PasswordIn, request: Request, user: User = Depends(current_user), db: Session = Depends(get_session)):
    if not verify_password(body.current, user.password_hash):
        raise HTTPException(400, "Your current password isn't right.")
    _check_password(body.new)
    user.password_hash = hash_password(body.new)
    keep = _sha(request.cookies.get(COOKIE, ""))
    db.execute(delete(AuthSession).where(AuthSession.user_id == user.id, AuthSession.token_hash != keep))
    db.commit()
    record("password_changed", user.id, "", request)
    return {"ok": True}


class DeleteAccountIn(BaseModel):
    password: str


def delete_account_data(db: Session, user_id: int) -> None:
    """Remove an account and everything that's theirs: sessions, links, settings, resume, tracker, scores."""
    for table in ("auth_sessions", "auth_tokens", "user_prefs", "user_profile", "applications", "user_job_matches"):
        db.execute(text(f"DELETE FROM {table} WHERE user_id = :u"), {"u": user_id})
    db.execute(delete(User).where(User.id == user_id))
    db.commit()


@router.delete("/api/auth/account")
def delete_my_account(body: DeleteAccountIn, request: Request, response: Response, user: User = Depends(current_user),
                      db: Session = Depends(get_session)):
    """Delete my account: my resume, roles, tracker, scores and alerts. Needs the password, as a confirmation."""
    if not verify_password(body.password, user.password_hash):
        raise HTTPException(400, "That password isn't right.")
    if user.is_admin:
        raise HTTPException(400, "The admin account can't delete itself here.")
    uid, email = user.id, user.email
    delete_account_data(db, uid)
    record("account_deleted", uid, f"by the user ({email.split('@')[-1]})", request)
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


def _unsubscriber(db: Session, token: str) -> User | None:
    return db.scalar(select(User).where(User.unsubscribe_token == token)) if token else None


_BAD_LINK = ("That unsubscribe link isn't valid.", "Log in and switch alerts off in My profile.")


@router.get("/api/alerts/unsubscribe", response_class=HTMLResponse)
def unsubscribe_page(token: str = "", db: Session = Depends(get_session)):
    """The link in an alert email asks first and changes nothing: mail scanners open every link in a message (Microsoft
    cloud addresses fetched it seconds after each alert, 2026-11-14), and a GET that unsubscribed would switch alerts
    off behind the reader's back. The button POSTs back here with confirm=1."""
    if _unsubscriber(db, token) is None:
        # _page is already a response: wrapping it in another HTMLResponse answered 500 (2026-11-14).
        return _page(*_BAD_LINK, status=400)
    from html import escape
    from urllib.parse import quote

    action = escape(f"/api/alerts/unsubscribe?token={quote(token, safe='')}&confirm=1")
    button = (f"<form method='post' action='{action}' style='margin:20px 0 0'><button type='submit' "
              "style='background:#6d28d9;color:#fff;border:0;border-radius:10px;padding:10px 18px;font-size:15px;"
              "font-weight:700;cursor:pointer'>Unsubscribe</button></form>")
    return _page("Stop FreshJobs alerts?", "You'll get no more job alert emails. You can switch them back on in My profile.",
                 extra_html=button)


@router.post("/api/alerts/unsubscribe")
def unsubscribe(token: str = "", confirm: bool = False, db: Session = Depends(get_session)):
    """Turns alerts off: the confirm page's button (confirm=1, answers with a page), or a mail app's RFC 8058 one-click
    POST to the List-Unsubscribe link (answers JSON)."""
    user = _unsubscriber(db, token)
    if user is None:
        return _page(*_BAD_LINK, status=400) if confirm else Response(status_code=400)
    db.execute(update(UserPrefs).where(UserPrefs.user_id == user.id).values(email_alerts=False))
    db.commit()
    record("unsubscribed", user.id, "confirm button" if confirm else "one-click (mail app)")
    if not confirm:
        return {"ok": True}
    return _page("You're unsubscribed.", f"{user.email} won't get FreshJobs alerts any more. "
                 f"You can switch them back on in My profile.")


def _page(title: str, body: str, status: int = 200, extra_html: str = "") -> HTMLResponse:
    from html import escape

    return HTMLResponse(
        "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
        "<title>FreshJobs</title><body style='font-family:system-ui,sans-serif;background:#f5f3ff;margin:0'>"
        "<div style='max-width:440px;margin:15vh auto;background:#fff;border-radius:16px;padding:32px;"
        "box-shadow:0 10px 30px rgba(76,29,149,.12)'><p style='margin:0 0 8px;font-weight:800;color:#5b21b6'>"
        f"⚡ FreshJobs</p><h1 style='margin:0 0 8px;font-size:22px;color:#0c0a1d'>{escape(title)}</h1>"
        f"<p style='margin:0;color:#475569;line-height:1.5'>{escape(body)}</p>"
        f"{extra_html}"
        f"<p style='margin:20px 0 0'><a href='{escape(settings.public_url)}' style='color:#6d28d9'>Open FreshJobs →</a></p>"
        "</div></body>", status)
