"""Closed-job detection (pipeline/liveness.py), Workable's job search, and junk Workday URLs."""

from datetime import timedelta

import httpx
import pytest
import respx

from app.models import Company, Job
from app.pipeline.detect import detect_board
from app.pipeline.liveness import _workday_api, check, verify_due
from app.pipeline.poller import process_company
from app.prefs import get_prefs
from app.sources import ADAPTERS, NormalizedJob


async def run_check(url: str):
    async with httpx.AsyncClient(follow_redirects=True) as client:
        return await check(client, url)


@pytest.mark.asyncio
@respx.mock
async def test_check_reads_status_redirects_and_closed_messages():
    respx.get("https://jobs.lever.co/acme/1").mock(return_value=httpx.Response(404))
    respx.get("https://boards.greenhouse.io/acme/jobs/2").mock(
        return_value=httpx.Response(302, headers={"Location": "https://boards.greenhouse.io/acme?error=true"}))
    respx.get("https://boards.greenhouse.io/acme?error=true").mock(return_value=httpx.Response(200, text="Jobs"))
    respx.get("https://acme.com/careers/3").mock(
        return_value=httpx.Response(200, text="<h1>Data Analyst</h1><p>This position is no longer accepting applications.</p>"))
    respx.get("https://acme.com/careers/4").mock(return_value=httpx.Response(200, text="<h1>Data Analyst</h1><p>Apply now</p>"))
    respx.get("https://acme.com/careers/5").mock(return_value=httpx.Response(403))

    assert (await run_check("https://jobs.lever.co/acme/1"))[0] is False
    assert (await run_check("https://boards.greenhouse.io/acme/jobs/2"))[0] is False
    assert (await run_check("https://acme.com/careers/3"))[0] is False
    assert (await run_check("https://acme.com/careers/4"))[0] is True
    assert (await run_check("https://acme.com/careers/5"))[0] is None  # bot protection: unknown, left open
    assert (await run_check("https://www.linkedin.com/jobs/view/123"))[0] is None  # never fetched


def test_workday_job_pages_are_checked_through_the_api():
    assert _workday_api("https://sel.wd1.myworkdayjobs.com/en-US/SEL/job/Pullman-WA/Data-Engineer_R1") == (
        "https://sel.wd1.myworkdayjobs.com/wday/cxs/sel/SEL/job/Pullman-WA/Data-Engineer_R1")
    assert _workday_api("https://acme.com/job/1") is None


@pytest.mark.asyncio
@respx.mock
async def test_verify_due_closes_taken_down_aggregator_jobs(session, now):
    muse = Company(name="The Muse", ats="themuse", slug="")
    session.add(muse)
    session.flush()
    prefs = get_prefs(session)
    process_company(session, muse, [], prefs, now - timedelta(hours=1))
    gone, live = process_company(session, muse, [
        NormalizedJob(external_id="1", title="Data Analyst", url="https://acme.com/jobs/1", locations=["Boston, MA"], company_name="Acme"),
        NormalizedJob(external_id="2", title="Data Engineer", url="https://acme.com/jobs/2", locations=["Boston, MA"], company_name="Acme"),
    ], prefs, now)
    respx.get("https://acme.com/jobs/1").mock(return_value=httpx.Response(404))
    respx.get("https://acme.com/jobs/2").mock(return_value=httpx.Response(200, text="Apply"))
    async with httpx.AsyncClient() as client:
        assert await verify_due(session, client, 10, now) == 1
        assert gone.closed_at == now and live.closed_at is None
        assert live.checked_at == now
        assert await verify_due(session, client, 10, now + timedelta(hours=1)) == 0  # checked within the day
    assert session.query(Job).filter(Job.checked_at.is_(None)).count() == 0


def test_due_jobs_newest_first_from_sources_that_cant_close_their_own(session, now):
    from app.pipeline.liveness import _checked, due_jobs

    agg = Company(name="The Muse", ats="themuse", slug="")
    healthy = Company(name="Acme", ats="greenhouse", slug="acme", last_ok_at=now)
    broken = Company(name="Broken", ats="greenhouse", slug="broken", last_ok_at=now - timedelta(days=3))
    session.add_all([agg, healthy, broken])
    session.flush()

    def job(c, ext, hours, **kw):
        j = Job(company_id=c.id, external_id=ext, title="Data Analyst", company_name=c.name, url=f"https://x/{ext}", fingerprint=ext,
                first_seen_at=now, last_seen_at=now, listed_at=now - timedelta(hours=hours) if hours is not None else None,
                **{"is_repost": False, "us_eligible": True, **kw})
        session.add(j)
        return j

    old = job(agg, "old", 50)
    new = job(agg, "new", 1)
    undated = job(agg, "undated", None)
    job(agg, "abroad", 0, us_eligible=False)
    job(agg, "repost", 0, is_repost=True)
    job(agg, "closed", 0, closed_at=now)
    job(agg, "seen", 0, checked_at=now - timedelta(hours=2))  # checked by the job panel today
    stale = job(broken, "stale", 5)
    job(healthy, "fine", 0)  # its board closes it on its own
    session.commit()

    assert [j.external_id for j in due_jobs(session, 10, now)] == ["new", "stale", "old", "undated"]
    assert [j.external_id for j in due_jobs(session, 2, now)] == ["new", "stale"]
    _checked[new.id] = now
    assert [j.external_id for j in due_jobs(session, 2, now)] == ["stale", "old"]
    assert old.id and undated.id and stale.id


def test_due_jobs_reads_dates_from_the_index(session):
    from sqlalchemy import text

    plan = " ".join(r[-1] for r in session.execute(text(
        "EXPLAIN QUERY PLAN SELECT id, listed_at FROM jobs INDEXED BY ix_jobs_id_feed "
        "WHERE id IN (1, 2, 3) AND is_repost = 0")))
    assert "COVERING INDEX ix_jobs_id_feed" in plan
    plan = " ".join(r[-1] for r in session.execute(text(
        "EXPLAIN QUERY PLAN SELECT id FROM jobs INDEXED BY ix_jobs_company_sync "
        "WHERE company_id IN (1, 2) AND closed_at IS NULL")))
    assert "COVERING INDEX ix_jobs_company_sync" in plan
    plan = " ".join(r[-1] for r in session.execute(text(
        "EXPLAIN QUERY PLAN SELECT id FROM jobs INDEXED BY ix_jobs_by_listed_v2 "
        "WHERE closed_at IS NULL AND is_repost = 0 ORDER BY listed_at DESC")))
    assert "COVERING INDEX ix_jobs_by_listed_v2" in plan and "TEMP B-TREE" not in plan


def test_workday_files_are_not_boards():
    assert detect_board("https://acme.wd1.myworkdayjobs.com/robots.txt") is None
    assert detect_board("https://acme.wd1.myworkdayjobs.com/en-US/External") == ("workday", "acme|wd1|External")


@pytest.mark.asyncio
@respx.mock
async def test_workable_search_stops_at_jobs_already_seen():
    def job(i, featured=False):
        return {"id": f"id{i}", "title": f"Data Analyst {i}", "url": f"https://jobs.workable.com/view/{i}",
                "locations": ["TELECOMMUTE", "Austin, Texas, United States"], "workplace": "remote",
                "created": "2026-11-04T23:49:39.592Z", "description": "<p>SQL</p>", "isFeatured": featured,
                "company": {"title": "Acme"}}

    pages = {
        None: {"jobs": [job(0, True), job(1)], "nextPageToken": "p2"},
        "p2": {"jobs": [job(2), job(3)], "nextPageToken": "p3"},
        "p3": {"jobs": [job(4)], "nextPageToken": None},
    }
    respx.get("https://jobs.workable.com/api/v1/jobs").mock(
        side_effect=lambda req: httpx.Response(200, json=pages[req.url.params.get("pageToken")]))
    adapter = ADAPTERS["workable_search"]
    async with httpx.AsyncClient() as client:
        first = await adapter.fetch(client, "location=United States", first_scan=True)
        assert [j.external_id for j in first] == ["id0", "id1", "id2", "id3", "id4"]
        assert first[1].company_name == "Acme" and first[1].remote and first[1].locations == ["Austin, Texas, United States"]
        later = await adapter.fetch(client, "location=United States", known_ids={"id0", "id2", "id3"})
    assert [j.external_id for j in later] == ["id0", "id1", "id2", "id3"]  # page 2 was all known: stop


def test_messages_inside_scripts_dont_close_a_job():
    from app.pipeline.liveness import _CLOSED, visible_text

    page = ('<html><script>window.i18n={"noRoleFound":"Sorry, this role does not exist or is no longer available."}</script>'
            '<div id="app"><h1>Senior Engineer</h1><p>Apply now</p></div></html>')
    assert _CLOSED.search(page) and not _CLOSED.search(visible_text(page))
    assert _CLOSED.search(visible_text("<p>This job is no longer available.</p>"))
