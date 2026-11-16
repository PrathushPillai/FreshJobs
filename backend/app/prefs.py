from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Company, UserPrefs
from app.pipeline.filters import DEFAULT_EXCLUDE, DEFAULT_INCLUDE


def get_prefs(session: Session, user_id: int | None = None) -> UserPrefs:
    """A user's settings (created with the default roles on first use), or with no user, the pipeline's view.

    The pipeline's view decides Job.role_match ("someone's For you"): every user's roles together, minus only
    the words every user excludes, so one user's exclusions never drop another's jobs. With one user it's
    simply their row; before any account exists it's the single row from before accounts (id 1). Each user's
    own For you then filters on their own roles (main.list_jobs)."""
    if user_id is not None:
        prefs = session.scalar(select(UserPrefs).where(UserPrefs.user_id == user_id))
        if prefs is None:
            prefs = UserPrefs(user_id=user_id, include_keywords=list(DEFAULT_INCLUDE), exclude_keywords=list(DEFAULT_EXCLUDE),
                              email_alerts=False, alert_frequency="instant", alert_daily_cap=10, alerts_sent_today=0,
                              hide_no_sponsor=True, exclude_senior=True)
            session.add(prefs)
            session.commit()
        return prefs
    # Every row, including the one from before accounts while the admin hasn't claimed it yet: until then its
    # roles still count (otherwise the first other account, even the portal check's, would replace them).
    rows = list(session.scalars(select(UserPrefs)))
    if len(rows) == 1:
        return rows[0]
    if not rows:
        prefs = UserPrefs(id=1, include_keywords=DEFAULT_INCLUDE, exclude_keywords=DEFAULT_EXCLUDE)
        session.add(prefs)
        session.commit()
        return prefs
    include = sorted({k.strip().lower() for r in rows for k in r.include_keywords or [] if k.strip()})
    exclude = set.intersection(*({k.strip().lower() for k in r.exclude_keywords or []} for r in rows))
    return UserPrefs(include_keywords=include, exclude_keywords=sorted(exclude), email_alerts=False,
                     hide_no_sponsor=True, exclude_senior=True)  # not saved: a view


def sync_companies(session: Session, path: Path) -> int:
    """Add any companies from the YAML file that aren't in the database yet. Returns how many were added."""
    if not path.exists():
        return 0
    entries = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    existing = {(c.ats, c.slug) for c in session.scalars(select(Company))}
    added = 0
    for e in entries:
        key = (e["ats"], str(e["slug"]))
        if key not in existing:
            session.add(Company(name=e["name"], ats=e["ats"], slug=str(e["slug"])))
            existing.add(key)
            added += 1
    session.commit()
    return added
