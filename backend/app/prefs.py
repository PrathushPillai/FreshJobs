from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Company, UserPrefs
from app.pipeline.filters import DEFAULT_EXCLUDE, DEFAULT_INCLUDE


def get_prefs(session: Session) -> UserPrefs:
    prefs = session.get(UserPrefs, 1)
    if prefs is None:
        prefs = UserPrefs(id=1, include_keywords=DEFAULT_INCLUDE, exclude_keywords=DEFAULT_EXCLUDE)
        session.add(prefs)
        session.commit()
    return prefs


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
