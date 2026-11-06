import pytest
from datetime import timedelta

from app.models import Company, JobEvent
from app.pipeline.poller import is_alert_worthy, process_company
from app.prefs import get_prefs
from app.sources import NormalizedJob

DESC = "Join our team to build reliable distributed backend systems in Python and Go for millions of users worldwide."


def nj(ext_id, title="Software Engineer", location="New York, NY", description=DESC):
    return NormalizedJob(external_id=ext_id, title=title, url=f"https://x/{ext_id}", locations=[location], description=description)


def make_company(session):
    company = Company(name="Acme", ats="greenhouse", slug="acme")
    session.add(company)
    session.flush()
    return company


def test_first_poll_is_baseline_and_not_alerted(session, now):
    company, prefs = make_company(session), get_prefs(session)
    [job] = process_company(session, company, [nj("1")], prefs, now)
    assert job.baseline and job.alerted
    assert not is_alert_worthy(job, prefs)


def test_new_job_after_baseline_is_fresh_match(session, now):
    company, prefs = make_company(session), get_prefs(session)
    process_company(session, company, [nj("1", title="Data Analyst")], prefs, now)
    later = now + timedelta(minutes=5)
    [job] = process_company(session, company, [nj("1", title="Data Analyst"), nj("2")], prefs, later)
    assert job.external_id == "2"
    assert job.first_seen_at == later and not job.baseline and not job.is_repost
    assert is_alert_worthy(job, prefs)


def test_job_closes_after_two_missed_polls_and_can_reopen(session, now):
    company, prefs = make_company(session), get_prefs(session)
    [job] = process_company(session, company, [nj("1")], prefs, now)
    process_company(session, company, [], prefs, now + timedelta(minutes=5))
    assert job.closed_at is None and job.missed_polls == 1
    process_company(session, company, [], prefs, now + timedelta(minutes=10))
    assert job.closed_at is not None
    process_company(session, company, [nj("1")], prefs, now + timedelta(minutes=15))
    assert job.closed_at is None
    kinds = [e.kind for e in session.query(JobEvent).filter_by(job_id=job.id).order_by(JobEvent.id)]
    assert kinds == ["seen", "closed", "reopened"]


def test_repost_with_new_id_is_flagged(session, now):
    company, prefs = make_company(session), get_prefs(session)
    [original] = process_company(session, company, [nj("1")], prefs, now)
    for minutes in (5, 10):  # original disappears and closes
        process_company(session, company, [], prefs, now + timedelta(minutes=minutes))
    [repost] = process_company(session, company, [nj("99", title="Software Engineer (Remote)")], prefs, now + timedelta(days=3))
    assert repost.is_repost and repost.repost_of_id == original.id
    assert not is_alert_worthy(repost, prefs)


def test_same_role_in_another_city_is_a_separate_opening_while_the_first_is_open(session, now):
    company, prefs = make_company(session), get_prefs(session)
    process_company(session, company, [nj("1")], prefs, now)
    [sibling] = process_company(session, company, [nj("1"), nj("2", location="Austin, TX")], prefs, now + timedelta(hours=1))
    assert not sibling.is_repost


def test_same_role_relisted_in_another_city_after_closing_is_repost(session, now):
    company, prefs = make_company(session), get_prefs(session)
    [original] = process_company(session, company, [nj("1")], prefs, now)
    for minutes in (5, 10):  # the original disappears and closes
        process_company(session, company, [], prefs, now + timedelta(minutes=minutes))
    [dup] = process_company(session, company, [nj("2", location="Austin, TX")], prefs, now + timedelta(days=2))
    assert dup.is_repost and dup.repost_of_id == original.id


def test_identical_openings_in_same_poll_are_not_reposts(session, now):
    company, prefs = make_company(session), get_prefs(session)
    process_company(session, company, [], prefs, now)
    jobs = process_company(session, company, [nj("1"), nj("2")], prefs, now + timedelta(minutes=5))
    assert [j.is_repost for j in jobs] == [False, False]


def test_old_job_listed_now_is_resurfaced_not_fresh(session, now):
    company, prefs = make_company(session), get_prefs(session)
    process_company(session, company, [], prefs, now)
    old = nj("1")
    old.posted_at = now - timedelta(days=90)
    [job] = process_company(session, company, [old], prefs, now + timedelta(minutes=5))
    assert job.is_repost and job.events[0].kind == "resurfaced"
    assert not is_alert_worthy(job, prefs)


def test_no_sponsor_job_not_alerted(session, now):
    company, prefs = make_company(session), get_prefs(session)
    process_company(session, company, [], prefs, now)
    [job] = process_company(session, company, [nj("1", description=DESC + " Must be a US citizen.")], prefs, now + timedelta(minutes=5))
    assert job.no_sponsor and not is_alert_worthy(job, prefs)


@pytest.mark.asyncio
async def test_adapter_bug_only_fails_its_own_source(monkeypatch):
    import asyncio

    import httpx

    from app.pipeline import poller
    from app.sources.base import Adapter

    class Broken(Adapter):
        ats = "broken"

        async def fetch(self, client, slug, **_):
            raise AttributeError("'str' object has no attribute 'get'")

    monkeypatch.setitem(poller.ADAPTERS, "broken", Broken())
    company = Company(name="Bad", ats="broken", slug="x", poll_count=0)
    async with httpx.AsyncClient() as client:
        _, jobs, complete, error = await poller._fetch(client, asyncio.Semaphore(1), company, set())
    assert jobs is None and not complete and error.startswith("AttributeError")


def test_empty_and_failing_boards_are_polled_less_often(now):
    from app.pipeline.poller import _due

    busy = Company(name="Busy", ats="greenhouse", slug="busy", last_polled_at=now - timedelta(minutes=10), last_status="ok (40 open)")
    empty = Company(name="Empty", ats="greenhouse", slug="empty", last_polled_at=now - timedelta(minutes=10), last_status="ok (0 open)")
    broken = Company(name="Broken", ats="greenhouse", slug="broken", last_polled_at=now - timedelta(hours=1), last_status="error: HTTP 404")
    assert _due(busy, now) and not _due(empty, now) and not _due(broken, now)
    assert _due(empty, now + timedelta(hours=3))


def test_job_saved_without_description_is_enriched_later(session, now):
    company, prefs = make_company(session), get_prefs(session)
    [job] = process_company(session, company, [nj("1", location="Pullman, WA", description="")], prefs, now)
    assert job.listed_at is None  # baseline and undated: unknown, not "posted just now"
    detailed = nj("1", title="Data Engineer", location="Washington - Pullman", description=DESC + " Note: US Citizenship or Green Card Required.")
    detailed.posted_at = now - timedelta(days=1)
    process_company(session, company, [detailed], prefs, now + timedelta(minutes=10))
    assert job.no_sponsor and "Green Card" in job.description
    assert job.posted_at == now - timedelta(days=1) and job.listed_at == job.posted_at


def test_job_found_after_a_gap_gets_a_posted_window(session, now):
    company, prefs = make_company(session), get_prefs(session)
    process_company(session, company, [nj("1")], prefs, now)
    soon = now + timedelta(minutes=5)
    [quick] = process_company(session, company, [nj("1"), nj("2", title="Data Analyst")], prefs, soon)
    assert quick.listed_at == soon and quick.listed_until is None  # normal polling: found minutes after it went live

    # The server was off for 6 hours: an undated job went live some time in that gap, not "just now".
    later = soon + timedelta(hours=6)
    [gap] = process_company(session, company, [nj("1"), nj("2", title="Data Analyst"), nj("3", title="ML Engineer")], prefs, later)
    assert gap.first_seen_at == later
    assert gap.listed_at == soon and gap.listed_until == later

    # A failed check in between doesn't count as having looked.
    company.last_polled_at, company.last_status = later + timedelta(hours=1), "error: timeout"
    after_error = later + timedelta(hours=1, minutes=5)
    [job] = process_company(session, company, [nj("1"), nj("2", title="Data Analyst"), nj("3", title="ML Engineer"), nj("4", title="Data Engineer")], prefs, after_error)
    assert job.listed_at == later and job.listed_until == after_error


@pytest.mark.asyncio
async def test_poll_stops_at_its_deadline_and_leaves_the_rest_for_next_time(session, monkeypatch, tmp_path):
    import asyncio

    from app.config import settings
    from app.pipeline import poller
    from app.sources import ADAPTERS
    from app.sources.base import Adapter

    class Quick(Adapter):
        ats = "quick"

        async def fetch(self, client, slug, **_):
            return [nj("1")]

    class Stuck(Adapter):
        ats = "stuck"

        async def fetch(self, client, slug, **_):
            await asyncio.sleep(30)
            return []

    monkeypatch.setitem(ADAPTERS, "quick", Quick())
    monkeypatch.setitem(ADAPTERS, "stuck", Stuck())
    empty = tmp_path / "companies.yaml"
    empty.write_text("[]")
    monkeypatch.setattr(settings, "companies_file", empty)
    monkeypatch.setattr(settings, "poll_deadline_minutes", {"fast": 0.02})  # 1.2 s

    async def nothing(*a, **k):
        return []

    monkeypatch.setattr(poller, "maybe_sync_yc", nothing)
    monkeypatch.setattr(poller, "maybe_sync_lists", nothing)
    monkeypatch.setattr(poller, "verify_due", lambda *a, **k: asyncio.sleep(0, 0))
    session.add_all([Company(name="Quick", ats="quick", slug="q"), Company(name="Stuck", ats="stuck", slug="s")])
    session.commit()

    summary = await poller.poll_once("fast")
    session.expire_all()
    quick, stuck = session.query(Company).filter_by(ats="quick").one(), session.query(Company).filter_by(ats="stuck").one()
    assert summary["cut_short"] == 1 and summary["new_jobs"] == 1
    assert quick.poll_count == 1 and stuck.last_polled_at is None  # still due, and first in line next time


def test_still_listed_jobs_are_not_rewritten_every_poll(session, now):
    company, prefs = make_company(session), get_prefs(session)
    [job] = process_company(session, company, [nj("1")], prefs, now)
    session.commit()
    process_company(session, company, [nj("1")], prefs, now + timedelta(minutes=5))
    assert job.last_seen_at == now and job not in session.dirty  # nothing to write for the job
    process_company(session, company, [nj("1")], prefs, now + timedelta(hours=7))
    assert job.last_seen_at == now + timedelta(hours=7)
