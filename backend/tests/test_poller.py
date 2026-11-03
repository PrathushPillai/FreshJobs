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


def test_same_title_new_location_with_same_description_is_repost(session, now):
    company, prefs = make_company(session), get_prefs(session)
    [original] = process_company(session, company, [nj("1")], prefs, now)
    [dup] = process_company(session, company, [nj("1"), nj("2", location="Austin, TX")], prefs, now + timedelta(hours=1))
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
