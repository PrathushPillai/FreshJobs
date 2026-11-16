"""Embeddings and scores are saved with upserts: two writers at once (the job panel, a second request, the
scoring process) used to both insert, and opening a job answered 500 (UNIQUE constraint failed: job_vectors.job_id)."""

import numpy as np
from sqlalchemy import func, select

from app.models import Company, Job, JobMatch, JobVector, User
from app.pipeline import scoring


def _job(session, now):
    company = Company(name="Acme", ats="greenhouse", slug="acme")
    session.add(company)
    session.flush()
    job = Job(company_id=company.id, external_id="1", title="Data Engineer", url="https://x/1", fingerprint="f",
              first_seen_at=now, last_seen_at=now)
    session.add(job)
    session.commit()
    return job


def test_saving_a_vector_twice_updates_it(session, now):
    job = _job(session, now)
    for value in (1.0, 2.0):
        scoring._upsert(session, JobVector, [{"job_id": job.id, "vector": np.full(4, value, np.float32).tobytes(),
                                              "described": False}], ["job_id"])
        session.commit()
    assert session.scalar(select(func.count()).select_from(JobVector)) == 1
    saved = session.get(JobVector, job.id)
    assert np.frombuffer(saved.vector, np.float32)[0] == 2.0


def test_a_vector_saved_meanwhile_by_another_writer_is_no_error(session, now, monkeypatch):
    # The race: the "have" lookup found nothing, then another writer saved the row before this insert.
    job = _job(session, now)
    session.add(JobVector(job_id=job.id, vector=np.zeros(384, np.float32).tobytes(), described=False))
    session.commit()
    real_execute = session.execute

    def nothing_saved_yet(stmt, *a, **k):
        if "job_vectors.job_id, job_vectors.vector" in str(stmt):
            return iter(())
        return real_execute(stmt, *a, **k)

    monkeypatch.setattr(session, "execute", nothing_saved_yet)
    monkeypatch.setattr(scoring.matching, "embed", lambda texts: [np.ones(384, np.float32) for _ in texts])
    got = scoring._vectors(session, [job])
    monkeypatch.undo()
    session.commit()
    assert job.id in got and session.scalar(select(func.count()).select_from(JobVector)) == 1


def test_scores_are_upserted_and_loaded_copies_refreshed(session, now):
    job = _job(session, now)
    user = User(email="u@example.test", name="U", password_hash="x", is_admin=False, unsubscribe_token="t")
    session.add(user)
    session.commit()
    row = {"user_id": user.id, "job_id": job.id, "profile_version": 1, "score": 40, "detail": {"notes": []}}
    scoring._upsert(session, JobMatch, [row], ["user_id", "job_id"])
    session.commit()
    loaded = session.get(JobMatch, (user.id, job.id))
    scoring._upsert(session, JobMatch, [{**row, "profile_version": 2, "score": 77}], ["user_id", "job_id"])
    session.commit()
    assert loaded.score == 77 and loaded.profile_version == 2
