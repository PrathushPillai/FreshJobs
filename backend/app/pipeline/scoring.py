"""Scores the "For you" jobs against the user's resume (app/matching.py), in the background.

Each job's embedding is computed once (JobVector) and reused; a new resume only needs a dot product per job.
`refresh()` runs after a resume upload or profile edit, at startup and after each fast poll (new jobs).
`refresh_in_background()` runs it in a separate, low-priority process (`python -m app.pipeline.scoring`): inside
the busy backend, embedding ran at ~1 job/s (the pollers compete for the GIL and CPU), on its own at ~6 jobs/s.
One process at a time (an exclusive lock on `scoring.lock`); it repeats until nothing is left to score, so new
jobs and profile changes made meanwhile are picked up. Progress goes to `scoring.json` (`status()`).
`ensure_scores()` scores a page of other jobs (a search) on demand, in the backend.
"""

import json
import logging
import os
import subprocess
import sys
import threading
from pathlib import Path

import numpy as np
from sqlalchemy import and_, exists, or_, select, text
from sqlalchemy.orm import Session, selectinload

from app import matching
from app.db import SessionLocal
from app.models import Job, JobLocation, JobMatch, JobVector, UserProfile
from app.pipeline.search import expand_query
from app.prefs import get_prefs

log = logging.getLogger(__name__)
_lock = threading.Lock()
BATCH = 64  # saved per batch, so progress shows and a restart loses little
progress: dict = {"running": False, "scored": 0, "total": 0}


def get_profile(session: Session) -> UserProfile | None:
    return session.get(UserProfile, 1)


def profile_view(session: Session, profile: UserProfile) -> dict:
    """What score() needs to know about the user. Target roles: the resume's plus the "Your roles" list."""
    prefs = get_prefs(session)
    targets = list(dict.fromkeys([*(profile.target_roles or []), *(prefs.include_keywords or [])]))
    related = sorted({t for role in targets for t in expand_query(role)[1:]} - set(targets))
    return {
        "skills": profile.skills or [], "years": profile.years, "degree": profile.degree or "",
        "target_roles": targets, "related_roles": related, "needs_sponsorship": profile.needs_sponsorship,
        "locations": profile.locations or [], "version": profile.version,
        "vector": np.frombuffer(profile.vector, dtype=np.float32) if profile.vector else None,
    }


def _job_view(job: Job, view: dict) -> dict:
    from app.pipeline.filters import role_matches

    return {
        "title": job.title, "description": job.description or "", "min_years": job.min_years,
        "no_sponsor": job.no_sponsor, "student_visa": job.student_visa, "remote": job.remote,
        "places": [(p.state, p.city) for p in job.places],
        "related_role": bool(view["related_roles"]) and role_matches(job.title or "", view["related_roles"], []),
    }


def _vectors(session: Session, jobs: list[Job]) -> dict[int, np.ndarray]:
    """Embeddings for these jobs, computing and saving the missing ones."""
    ids = [j.id for j in jobs]
    have = {jid: np.frombuffer(v, dtype=np.float32) for jid, v in session.execute(
        select(JobVector.job_id, JobVector.vector).where(JobVector.job_id.in_(ids)))}
    todo = [j for j in jobs if j.id not in have]
    if todo:
        vectors = matching.embed([matching.job_text(j.title, j.description or "") for j in todo])
        for job, vec in zip(todo, vectors):
            have[job.id] = vec
            session.merge(JobVector(job_id=job.id, vector=vec.astype(np.float32).tobytes(), described=bool(job.described)))
    return have


def _score_jobs(session: Session, jobs: list[Job], view: dict) -> list[JobMatch]:
    vectors = _vectors(session, jobs)
    rows = []
    for job in jobs:
        cos = float(np.dot(view["vector"], vectors[job.id])) if view["vector"] is not None else None
        result = matching.score(view, _job_view(job, view), cos)
        rows.append(JobMatch(job_id=job.id, profile_version=view["version"], score=result["score"], detail=result))
    for row in rows:
        session.merge(row)
    return rows


def stored_scores(session: Session, job_ids: list[int]) -> dict[int, JobMatch]:
    """The current profile's saved matches for these jobs; scores nothing (lists never wait for embedding)."""
    profile = get_profile(session)
    if not profile or not profile.resume_text or not job_ids:
        return {}
    return {m.job_id: m for m in session.scalars(
        select(JobMatch).where(JobMatch.job_id.in_(job_ids), JobMatch.profile_version == profile.version))}


def ensure_scores(session: Session, job_ids: list[int]) -> dict[int, JobMatch]:
    """Matches for these jobs (a page of results), scoring the ones not scored for the current profile."""
    profile = get_profile(session)
    if not profile or not profile.resume_text or not job_ids:
        return {}
    found = {m.job_id: m for m in session.scalars(
        select(JobMatch).where(JobMatch.job_id.in_(job_ids), JobMatch.profile_version == profile.version))}
    missing = [i for i in job_ids if i not in found]
    if missing:
        view = profile_view(session, profile)
        jobs = list(session.scalars(select(Job).where(Job.id.in_(missing)).options(selectinload(Job.places))))
        for row in _score_jobs(session, jobs, view):
            found[row.job_id] = row
        session.commit()
    return found


def _state_dir() -> Path:
    d = Path(matching._models_dir())
    d.mkdir(parents=True, exist_ok=True)
    return d


def _save_progress() -> None:
    try:
        (_state_dir() / "scoring.json").write_text(json.dumps(progress))
    except OSError:
        pass


def _try_lock():
    """An exclusive lock on scoring.lock (held by the scoring process while it runs), or None if taken."""
    f = open(_state_dir() / "scoring.lock", "a+")
    try:
        if sys.platform == "win32":
            import msvcrt

            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return f
    except OSError:
        f.close()
        return None


def _release(f) -> None:
    try:
        if sys.platform == "win32":
            import msvcrt

            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
    except OSError:
        pass
    f.close()


def status() -> dict:
    """The scoring process's progress: {running, scored, total}."""
    lock = _try_lock()
    if lock is not None:  # nobody holds it: not running
        _release(lock)
        return {**progress, "running": False}
    try:
        return {**json.loads((_state_dir() / "scoring.json").read_text()), "running": True}
    except (OSError, ValueError):
        return {"running": True, "scored": 0, "total": 0}


def refresh() -> int:
    """Score every open US "For you" job (newest first) not yet scored for the current profile; returns how many were scored."""
    if not _lock.acquire(blocking=False):
        return 0  # already running; it picks up the latest profile version as it goes
    scored = 0
    try:
        with SessionLocal() as session:
            profile = get_profile(session)
            if not profile or not profile.resume_text:
                return 0
            view = profile_view(session, profile)
            current = exists().where(JobMatch.job_id == Job.id, JobMatch.profile_version == view["version"])
            todo = list(session.scalars(
                select(Job.id).where(Job.closed_at.is_(None), Job.is_repost.is_(False), Job.role_match.is_(True),
                                    Job.us_eligible.is_(True), ~current)
                .order_by(Job.id.desc())))
        progress.update(running=True, scored=0, total=len(todo))
        _save_progress()
        for start in range(0, len(todo), BATCH):
            with SessionLocal() as session:
                profile = get_profile(session)
                if not profile or profile.version != view["version"]:
                    break  # the resume or profile changed: the next refresh starts over with it
                jobs = list(session.scalars(
                    select(Job).where(Job.id.in_(todo[start:start + BATCH])).options(selectinload(Job.places))))
                scored += len(_score_jobs(session, jobs, view))
                session.commit()
            progress["scored"] = scored
            _save_progress()
        log.info("Scored %d jobs against the resume", scored)
        return scored
    except Exception:
        log.exception("Scoring jobs against the resume failed")
        return scored
    finally:
        progress["running"] = False
        _lock.release()


def refresh_in_background() -> None:
    """Start the scoring process unless it's already running (it picks up new work before it exits)."""
    lock = _try_lock()
    if lock is None:
        return
    _release(lock)  # the child takes it; a second start in between finds nothing left and exits at once
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.BELOW_NORMAL_PRIORITY_CLASS | subprocess.CREATE_NO_WINDOW
    backend = Path(__file__).resolve().parent.parent.parent
    subprocess.Popen([sys.executable, "-m", "app.pipeline.scoring"], cwd=backend, creationflags=flags,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)


def repair_resume(session: Session) -> bool:
    """Re-space a saved resume whose words ran together (matching.respace), once. Fields the user edited are kept:
    a field is replaced only if it still equals what the old text gave. Bumps the version, so jobs are rescored."""
    from datetime import date

    profile = get_profile(session)
    if not profile or not profile.resume_text or matching.spacing(profile.resume_text) >= matching.WELL_SPACED:
        return False
    old, new = profile.resume_text, matching.respace(profile.resume_text)
    if matching.spacing(new) <= matching.spacing(old):
        return False
    today = date.today()
    old_skills, new_skills = matching.extract_skills(old), matching.extract_skills(new)
    if list(profile.skills or []) == old_skills:
        profile.skills = new_skills
    else:  # edited: keep the user's list, add what the clean text adds
        profile.skills = list(dict.fromkeys([*(profile.skills or []), *[s for s in new_skills if s not in old_skills]]))
    if profile.years == matching.resume_years(old, today):
        profile.years = matching.resume_years(new, today)
    if (profile.degree or "") == matching.resume_degree(old):
        profile.degree = matching.resume_degree(new)
    if list(profile.target_roles or []) == matching.resume_roles(old):
        profile.target_roles = matching.resume_roles(new)
    profile.resume_text = new
    profile.vector = matching.embed([matching.resume_for_embedding(new)])[0].tobytes()
    profile.version = (profile.version or 0) + 1
    session.commit()
    log.info("Re-spaced the saved resume (%.1f%% -> %.1f%% spaces) and rescoring", 100 * matching.spacing(old), 100 * matching.spacing(new))
    return True


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    lock = _try_lock()
    if lock is None:
        return  # another scoring process is running
    try:
        with SessionLocal() as session:
            repair_resume(session)  # resumes saved before re-spacing existed
        while refresh() > 0:  # again, for jobs found or a profile changed while scoring
            pass
    finally:
        _release(lock)


if __name__ == "__main__":
    main()
