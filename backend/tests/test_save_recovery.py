"""A poll's fetches and saves share one session: fetches must never touch it, and a failed save must not
break the rest of the poll (cloud server, 2026-11-11: one race corrupted the slow and bulk lanes' runs)."""

import asyncio
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from app.models import Company, Job
from app.pipeline import poller
from app.pipeline.poller import _fetch, _recover_failed_save, _source_info, process_company
from app.prefs import get_prefs
from app.sources import NormalizedJob
from app.sources.base import Adapter
from tests.test_poller import make_company, nj


class Echo(Adapter):
    ats = "echo"

    def __init__(self):
        self.calls = []

    async def fetch(self, client, slug, *, full=True, known_ids=frozenset(), first_scan=False):
        self.calls.append((slug, full, first_scan))
        return [NormalizedJob(external_id="1", title="Data Engineer", url="https://x/1", locations=["Austin, TX"])]


@pytest.mark.asyncio
async def test_a_fetch_never_reads_its_source_from_the_session(session, monkeypatch):
    # By the time a fetch gets a slot, a save in the worker thread may have committed and expired every source
    # in the session; reading company.slug then loaded it on the event loop mid-flush. Here the source is
    # expired and detached, so any read of it from the database raises.
    echo = Echo()
    monkeypatch.setitem(poller.ADAPTERS, "echo", echo)
    company = Company(name="Acme", ats="echo", slug="acme-board", poll_count=3)
    session.add(company)
    session.commit()
    info = _source_info(company)  # what poll_once reads before any save starts
    session.expire(company)
    session.expunge(company)

    async with httpx.AsyncClient() as client:
        got, jobs, complete, error = await _fetch(client, asyncio.Semaphore(1), company, set(), info)
    assert got is company and error is None and [j.external_id for j in jobs] == ["1"]
    assert echo.calls == [("acme-board", True, False)]


def test_after_a_failed_save_the_source_saves_normally(session, now):
    company, prefs = make_company(session), get_prefs(session)
    process_company(session, company, [nj("1")], prefs, now)
    session.commit()
    # A save that added two jobs (flushed, so they have ids) and then failed before its commit.
    process_company(session, company, [nj("1"), nj("2", title="Data Engineer"), nj("3", title="ML Engineer")],
                    prefs, now + timedelta(minutes=5))
    session.flush()
    fresh = _recover_failed_save(session, company)
    assert fresh is not None and fresh.slug == "acme"
    poller._record_save_error(session, fresh, OperationalError("COMMIT", {}, Exception("database is locked")))
    assert session.scalar(select(func.count(Job.id))) == 1  # nothing of the failed save was written
    assert fresh.last_status == "error: save failed (OperationalError)"

    process_company(session, fresh, [nj("1"), nj("2", title="Data Engineer"), nj("3", title="ML Engineer")],
                    prefs, now + timedelta(minutes=10))
    session.commit()
    assert session.scalar(select(func.count(Job.id))) == 3


@pytest.mark.asyncio
async def test_a_save_that_hits_a_locked_database_is_tried_again(session, monkeypatch, tmp_path):
    from app.config import settings

    monkeypatch.setitem(poller.ADAPTERS, "echo", Echo())
    empty = tmp_path / "companies.yaml"
    empty.write_text("[]")
    monkeypatch.setattr(settings, "companies_file", empty)

    async def nothing(*a, **k):
        return []

    monkeypatch.setattr(poller, "maybe_sync_yc", nothing)
    monkeypatch.setattr(poller, "maybe_sync_lists", nothing)
    monkeypatch.setattr(poller, "verify_due", lambda *a, **k: asyncio.sleep(0, 0))
    real_sleep = asyncio.sleep
    monkeypatch.setattr(poller.asyncio, "sleep", lambda s, *a: real_sleep(0, *a))  # no 5 s back-off in tests
    real_save, calls = poller._save, []

    def flaky_save(*args):
        calls.append(1)
        if len(calls) == 1:
            raise OperationalError("COMMIT", {}, Exception("database is locked"))
        return real_save(*args)

    monkeypatch.setattr(poller, "_save", flaky_save)
    session.add(Company(name="Acme", ats="echo", slug="acme-board"))
    session.commit()

    summary = await poller.poll_once("fast")
    session.expire_all()
    acme = session.query(Company).filter_by(ats="echo").one()
    assert len(calls) == 2 and summary["errors"] == 0 and summary["new_jobs"] == 1
    assert acme.poll_count == 1 and (acme.last_status or "").startswith("ok")
