"""Contact & support: a message from any page (or the login screen) to the admin's inbox.

Each request is saved (SupportRequest, listed in the admin dashboard, so nothing is lost if email fails) and
emailed to settings.support_email (or ADMIN_EMAIL) with Reply-To set to the sender, so answering is just Reply.
Limits: 5 requests a day per account and per address, and messages of 10-4,000 characters.
"""

import re
from datetime import datetime, timedelta, timezone
from html import escape

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.activity import client_ip, record
from app.auth import admin_user, user_from_request
from app.config import settings
from app.db import get_session
from app.models import Activity, SupportRequest, User

router = APIRouter()
TOPICS = {"question": "Question", "bug": "Something's broken", "idea": "Feature idea", "account": "Account or login",
          "other": "Something else"}
PER_DAY = 5
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[A-Za-z]{2,}$")


class SupportIn(BaseModel):
    topic: str = "question"
    message: str
    email: str = ""  # needed only when not logged in
    name: str = ""
    page: str = ""


def support_inbox() -> str:
    return (settings.support_email or settings.admin_email or settings.alert_from or settings.smtp_user).strip()


@router.post("/api/support")
def send_support(body: SupportIn, request: Request, db: Session = Depends(get_session)):
    from app.notify.email import _layout, send_email

    user = user_from_request(request, db)
    if user is not None and user.disabled:
        user = None
    email = (user.email if user else body.email).strip().lower()
    name = (user.name if user else body.name).strip()[:120]
    message = (body.message or "").strip()
    topic = body.topic if body.topic in TOPICS else "other"
    if not _EMAIL.match(email):
        raise HTTPException(400, "Add an email address we can reply to.")
    if not 10 <= len(message) <= 4000:
        raise HTTPException(400, "Write a message of 10 to 4,000 characters.")
    ip = client_ip(request)
    day_ago = datetime.now(timezone.utc) - timedelta(hours=24)
    recent = db.scalar(select(func.count()).select_from(Activity).where(
        Activity.kind == "support_request", Activity.at >= day_ago,
        or_(Activity.user_id == user.id, Activity.ip == ip) if user else Activity.ip == ip)) or 0
    if recent >= PER_DAY:
        raise HTTPException(429, "You've sent a few messages today. We'll reply to those first.")
    req = SupportRequest(user_id=user.id if user else None, email=email, name=name, topic=topic, message=message,
                         page=body.page[:200])
    db.add(req)
    db.commit()
    record("support_request", user.id if user else None, f"{TOPICS[topic]}: {message[:120]}", request)

    to = support_inbox()
    sent = False
    if to:
        who = f"{escape(name)} &lt;{escape(email)}&gt;" if name else escape(email)
        status = "a logged-in user" if user else "someone who isn't logged in"
        body_html = (f"<p style='margin:0 0 6px;'><b>{escape(TOPICS[topic])}</b> from {who}</p>"
                     f"<p style='margin:0 0 16px;font-size:13px;color:#64748b;'>Sent by {status}"
                     f"{' from the ' + escape(body.page) + ' page' if body.page else ''} · request #{req.id}</p>"
                     f"<div style='white-space:pre-wrap;background:#f5f3ff;border-radius:12px;padding:14px 16px;'>{escape(message)}</div>"
                     f"<p style='margin:16px 0 0;font-size:13px;color:#64748b;'>Reply to this email to answer them directly.</p>")
        sent, _ = send_email(
            to, f"[FreshJobs support] {TOPICS[topic]} from {name or email}",
            f"{TOPICS[topic]} from {name or ''} <{email}> ({status}), request #{req.id}\n\n{message}\n\nReply to answer them.",
            _layout(f"{TOPICS[topic]} from {name or email}", "New support request", body_html),
            {"Reply-To": email})
        req.emailed = sent
        db.commit()
    return {"ok": True, "id": req.id}


@router.get("/api/admin/support")
def list_support(status: str = "", db: Session = Depends(get_session), _: User = Depends(admin_user)):
    stmt = select(SupportRequest).order_by(SupportRequest.id.desc()).limit(200)
    if status:
        stmt = stmt.where(SupportRequest.status == status)
    return [{"id": r.id, "at": r.at, "user_id": r.user_id, "email": r.email, "name": r.name, "topic": r.topic,
             "topic_label": TOPICS.get(r.topic, r.topic), "message": r.message, "page": r.page, "emailed": r.emailed,
             "status": r.status} for r in db.scalars(stmt)]


class SupportStatusIn(BaseModel):
    status: str


@router.post("/api/admin/support/{request_id}")
def set_support_status(request_id: int, body: SupportStatusIn, db: Session = Depends(get_session), _: User = Depends(admin_user)):
    req = db.get(SupportRequest, request_id)
    if req is None:
        raise HTTPException(404, "No such request.")
    if body.status not in ("open", "done"):
        raise HTTPException(400, "Status is open or done.")
    req.status = body.status
    db.commit()
    return {"ok": True}
