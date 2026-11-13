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
    empty_bulk = Company(name="EmptyW", ats="workable", slug="emptyw", last_polled_at=now - timedelta(hours=2), last_status="ok (0 open)")
    broken = Company(name="Broken", ats="greenhouse", slug="broken", last_polled_at=now - timedelta(hours=1), last_status="error: HTTP 404")
    assert _due(busy, now) and not _due(broken, now)
    assert _due(empty, now)  # an empty board's first new job matters: normal pace (fast and slow lanes)
    assert not _due(empty_bulk, now) and _due(empty_bulk, now + timedelta(hours=1))  # rate-limited bulk lane
    assert _due(broken, now + timedelta(hours=3))


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
    assert job.last_seen_at == now  # refreshed at most once a day (LAST_SEEN_RESOLUTION)
    process_company(session, company, [nj("1")], prefs, now + timedelta(hours=25))
    assert job.last_seen_at == now + timedelta(hours=25)


@pytest.mark.asyncio
async def test_dead_boards_are_switched_off_after_three_404s(session, monkeypatch, tmp_path):
    import asyncio

    import httpx

    from app.config import settings
    from app.pipeline import poller
    from app.sources import ADAPTERS
    from app.sources.base import Adapter

    class Gone(Adapter):
        ats = "gone"

        async def fetch(self, client, slug, **_):
            request = httpx.Request("GET", f"https://example.com/{slug}")
            raise httpx.HTTPStatusError("not found", request=request, response=httpx.Response(404, request=request))

    monkeypatch.setitem(ADAPTERS, "gone", Gone())
    empty = tmp_path / "companies.yaml"
    empty.write_text("[]")
    monkeypatch.setattr(settings, "companies_file", empty)

    async def nothing(*a, **k):
        return []

    monkeypatch.setattr(poller, "maybe_sync_yc", nothing)
    monkeypatch.setattr(poller, "maybe_sync_lists", nothing)
    monkeypatch.setattr(poller, "verify_due", lambda *a, **k: asyncio.sleep(0, 0))
    from datetime import datetime, timezone

    session.add_all([Company(name="Dead", ats="gone", slug="dead"),
                     Company(name="Was Fine", ats="gone", slug="fine", last_ok_at=datetime(2026, 1, 1, tzinfo=timezone.utc))])
    session.commit()
    for _ in range(3):
        session.query(Company).update({Company.last_polled_at: None})  # due again
        session.commit()
        await poller.poll_once("fast")
    session.expire_all()
    dead, fine = session.query(Company).filter_by(slug="dead").one(), session.query(Company).filter_by(slug="fine").one()
    assert not dead.enabled and "never worked" in dead.last_status and dead.fail_streak == 3
    assert fine.enabled and fine.fail_streak == 3  # it worked before: a 404 may be temporary


@pytest.mark.asyncio
async def test_network_down_does_not_count_as_a_failing_board(session, monkeypatch, tmp_path):
    """A poll while the laptop is offline (Wi-Fi not back after sleep) leaves boards due for the next poll;
    a lookup failure while the machine is online still counts against the board."""
    import asyncio
    from datetime import datetime, timezone

    import httpx

    from app.config import settings
    from app.pipeline import poller
    from app.sources import ADAPTERS
    from app.sources.base import Adapter

    class NoDns(Adapter):
        ats = "nodns"

        async def fetch(self, client, slug, **_):
            raise httpx.ConnectError("[Errno 11001] getaddrinfo failed")

    monkeypatch.setitem(ADAPTERS, "nodns", NoDns())
    empty = tmp_path / "companies.yaml"
    empty.write_text("[]")
    monkeypatch.setattr(settings, "companies_file", empty)

    async def nothing(*a, **k):
        return []

    monkeypatch.setattr(poller, "maybe_sync_yc", nothing)
    monkeypatch.setattr(poller, "maybe_sync_lists", nothing)
    monkeypatch.setattr(poller, "verify_due", lambda *a, **k: asyncio.sleep(0, 0))
    earlier = datetime(2026, 11, 10, 3, 0, tzinfo=timezone.utc)
    session.add(Company(name="Acme", ats="nodns", slug="acme", poll_count=5, last_polled_at=earlier,
                        last_ok_at=earlier, last_status="ok (12 open)"))
    session.commit()

    monkeypatch.setattr(poller, "_online_cache", None)
    monkeypatch.setattr(poller, "_resolves", lambda host: False)  # offline
    summary = await poller.poll_once("fast")
    session.expire_all()
    acme = session.query(Company).one()
    assert summary["offline"] == 1 and summary["errors"] == 0
    assert acme.fail_streak == 0 and acme.last_status == "ok (12 open)" and acme.last_polled_at == earlier
    assert poller._due(acme, datetime.now(timezone.utc))  # first in line next poll

    monkeypatch.setattr(poller, "_online_cache", None)
    monkeypatch.setattr(poller, "_resolves", lambda host: True)  # online: this board's host really is gone
    summary = await poller.poll_once("fast")
    session.expire_all()
    acme = session.query(Company).one()
    assert summary["offline"] == 0 and summary["errors"] == 1
    assert acme.fail_streak == 1 and acme.last_status.startswith("error: ConnectError")


@pytest.mark.asyncio
async def test_is_offline_only_for_lookup_failures(monkeypatch):
    from app.pipeline import poller

    monkeypatch.setattr(poller, "_online_cache", None)
    monkeypatch.setattr(poller, "_resolves", lambda host: False)
    assert await poller.is_offline("ConnectError: [Errno 11001] getaddrinfo failed")
    assert not await poller.is_offline("HTTP 404")
    assert not await poller.is_offline("ReadTimeout: ")


@pytest.mark.asyncio
async def test_deadline_also_bounds_saving(session, monkeypatch, tmp_path):
    """Every fetch finishes at once, but saving is slow: the poll still ends at its deadline."""
    import asyncio
    import time as _time

    from app.config import settings
    from app.pipeline import poller
    from app.sources import ADAPTERS
    from app.sources.base import Adapter

    class Quick(Adapter):
        ats = "quick"

        async def fetch(self, client, slug, **_):
            return [nj(slug)]

    monkeypatch.setitem(ADAPTERS, "quick", Quick())
    empty = tmp_path / "companies.yaml"
    empty.write_text("[]")
    monkeypatch.setattr(settings, "companies_file", empty)
    monkeypatch.setattr(settings, "poll_deadline_minutes", {"fast": 0.01})  # 0.6 s

    async def nothing(*a, **k):
        return []

    monkeypatch.setattr(poller, "maybe_sync_yc", nothing)
    monkeypatch.setattr(poller, "maybe_sync_lists", nothing)
    monkeypatch.setattr(poller, "verify_due", lambda *a, **k: asyncio.sleep(0, 0))
    real_save = poller._save

    def slow_save(*a, **k):
        _time.sleep(0.25)
        return real_save(*a, **k)

    monkeypatch.setattr(poller, "_save", slow_save)
    session.add_all([Company(name=f"Q{i}", ats="quick", slug=f"q{i}") for i in range(10)])
    session.commit()

    summary = await poller.poll_once("fast")
    session.expire_all()
    saved = session.query(Company).filter(Company.poll_count > 0).count()
    assert 0 < saved < 10 and summary["cut_short"] == 10 - saved
    assert session.query(Company).filter(Company.last_polled_at.is_(None)).count() == 10 - saved


def test_save_reads_existing_jobs_from_the_covering_index(session):
    """process_company's read of a board's jobs must be index-only (no job rows, whose columns after the
    description sit past its overflow pages: loading full rows was 95% of a save)."""
    from sqlalchemy import select, text

    from app.models import Job

    stmt = select(Job.external_id, Job.id, Job.last_seen_at, Job.missed_polls, Job.closed_at, Job.described).where(
        Job.company_id == 1)
    sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
    plan = " ".join(str(r[-1]) for r in session.execute(text("EXPLAIN QUERY PLAN " + sql)))
    assert "COVERING INDEX ix_jobs_company_sync" in plan, plan


@pytest.mark.parametrize("error, streak, worked, dead", [
    ("HTTP 404", 3, False, True),
    ("HTTP 422", 3, False, True),     # Workday: no such tenant or site
    ("HTTP 422", 2, False, False),
    ("HTTP 403", 9, False, False),    # may be a temporary block: takes longer
    ("HTTP 403", 10, False, True),
    ("HTTP 422", 50, True, False),    # it worked before: never switched off this way
    ("HTTP 400", 3, False, True),     # a wrong slug (SmartRecruiters "robots.txt")
    ("HTTP 401", 3, False, True),     # a private board
    ("HTTP 502", 19, False, False),   # server errors take longer
    ("HTTP 502", 20, False, True),    # Whole Foods' Workday sites: 502 every time since they were found
    ("HTTP 500", 50, True, False),    # it worked before: never switched off this way
    ("ReadTimeout: ", 50, False, False),
])
def test_dead_board_rules(error, streak, worked, dead):
    from datetime import datetime, timezone

    from app.pipeline.poller import is_dead_board

    company = Company(name="X", ats="workday", slug="x", fail_streak=streak,
                      last_ok_at=datetime(2026, 1, 1, tzinfo=timezone.utc) if worked else None)
    assert is_dead_board(company, error) is dead


def test_a_working_board_that_fails_once_is_retried_soon(now):
    from app.pipeline.poller import _due

    worked = now - timedelta(days=1)
    blip = Company(name="Big", ats="greenhouse", slug="big", last_polled_at=now - timedelta(minutes=10),
                   last_status="error: ReadTimeout: ", fail_streak=1, last_ok_at=worked)
    still = Company(name="Big2", ats="greenhouse", slug="big2", last_polled_at=now - timedelta(minutes=10),
                    last_status="error: HTTP 502", fail_streak=3, last_ok_at=worked)
    never = Company(name="New", ats="greenhouse", slug="new", last_polled_at=now - timedelta(minutes=10),
                    last_status="error: ReadTimeout: ", fail_streak=1, last_ok_at=None)
    assert _due(blip, now)            # one blip on a board that works: try again at the normal pace
    assert not _due(still, now)       # failing for a while: back to every idle_poll_minutes
    assert not _due(never, now)       # never worked: no hurry


def test_repost_check_reads_an_employers_titles_once_per_save(session, now):
    # Reading every recent title at the employer for each new job made big saves crawl (2026-11-11 profile).
    from sqlalchemy import event

    company, prefs = make_company(session), get_prefs(session)
    [original] = process_company(session, company, [nj("1")], prefs, now)
    for minutes in (5, 10):  # the original closes
        process_company(session, company, [], prefs, now + timedelta(minutes=minutes))
    title_reads = []

    def count(conn, cursor, statement, *args):
        if statement.lstrip().startswith("SELECT jobs.id, jobs.title"):
            title_reads.append(statement)

    engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", count)
    try:
        fresh = [nj(str(i), title=f"Role {i}", location=f"City {i}, TX") for i in range(2, 12)]
        jobs = process_company(session, company, [nj("50", location="Austin, TX")] + fresh, prefs,
                               now + timedelta(days=2))
    finally:
        event.remove(engine, "before_cursor_execute", count)
    assert len(title_reads) == 1
    assert jobs[0].is_repost and jobs[0].repost_of_id == original.id  # still found through the cached titles
    assert not any(j.is_repost for j in jobs[1:])


def test_repost_title_check_reads_only_the_covering_index(session, now):
    """find_original's read of an employer's recent titles must be index-only (ix_jobs_company_titles)."""
    from sqlalchemy import or_, select, text

    from app.models import Job

    stmt = select(Job.id, Job.title, Job.company_id, Job.closed_at).where(
        Job.company_key == "amazon", or_(Job.closed_at.is_(None), Job.closed_at >= now - timedelta(days=60)),
        Job.first_seen_at >= now - timedelta(days=60))
    sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
    plan = " ".join(str(r[-1]) for r in session.execute(text("EXPLAIN QUERY PLAN " + sql)))
    assert "COVERING INDEX ix_jobs_company_titles" in plan, plan


def test_repost_fingerprint_check_uses_the_fingerprint_index(session, now):
    from sqlalchemy import or_, select, text

    from app.models import Job

    stmt = select(Job.id).where(Job.fingerprint == "abc", or_(Job.closed_at.is_(None),
                                Job.closed_at >= now - timedelta(days=60))).order_by(Job.first_seen_at)
    sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
    plan = " ".join(str(r[-1]) for r in session.execute(text("EXPLAIN QUERY PLAN " + sql)))
    assert "ix_jobs_fingerprint" in plan, plan


def test_quiet_boards_are_checked_less_often(now, monkeypatch):
    from app.pipeline import poller
    from app.pipeline.poller import _due, activity_factor

    monkeypatch.setattr(poller.settings, "activity_pacing", True)
    monkeypatch.setitem(poller._activity, "cuts", {1: 1000, 7: 500})
    monkeypatch.setitem(poller._activity, "max_id", {1: 1200, 2: 800, 3: 100})  # posted today / this week / long ago
    boards = [Company(id=i, name=f"B{i}", ats="greenhouse", slug=f"b{i}", last_status="ok (5 open)") for i in (1, 2, 3, 4)]
    assert [activity_factor(b) for b in boards] == [1, 2, 4, 4]  # board 4 has no jobs at all
    for b in boards:
        b.last_polled_at = now - timedelta(minutes=12)
    assert [_due(b, now) for b in boards] == [True, True, False, False]  # 5, 10, 20, 20 minutes
    poller.note_new_jobs(3)  # it just posted: full pace again
    assert activity_factor(boards[2]) == 1


def test_pacing_is_off_by_default_so_every_board_keeps_its_pace(now, monkeypatch):
    from app.pipeline import poller

    assert poller.settings.activity_pacing is False
    monkeypatch.setitem(poller._activity, "cuts", {1: 1000, 7: 500})
    monkeypatch.setitem(poller._activity, "max_id", {3: 100})  # posted long ago
    board = Company(id=3, name="B3", ats="greenhouse", slug="b3", last_status="ok (5 open)")
    board.last_polled_at = now - timedelta(minutes=6)
    assert poller.activity_factor(board) == 1 and poller._due(board, now)


def test_a_young_database_keeps_every_board_at_full_pace(monkeypatch):
    from app.pipeline import poller

    monkeypatch.setattr(poller.settings, "activity_pacing", True)
    monkeypatch.setitem(poller._activity, "cuts", {1: 0, 7: 0})  # no history older than a day yet
    monkeypatch.setitem(poller._activity, "max_id", {})
    assert poller.activity_factor(Company(id=9, name="New", ats="greenhouse", slug="n")) == 1


def test_board_activity_loads_from_the_database(session, monkeypatch):
    from datetime import datetime, timezone

    from app.models import Job, JobEvent
    from app.pipeline import poller

    now = datetime.now(timezone.utc)  # the boundaries are taken from the real clock

    old, fresh = make_company(session), Company(name="Fresh", ats="greenhouse", slug="fresh")
    session.add(fresh)
    session.flush()
    for company, job_id, age in ((old, 1, 30), (fresh, 2, 0)):
        session.add(Job(id=job_id, company_id=company.id, external_id=str(job_id), title="T", url="u", fingerprint="f",
                        first_seen_at=now, last_seen_at=now))
        session.flush()
        session.add(JobEvent(job_id=job_id, kind="seen", at=now - timedelta(days=age)))
    session.commit()
    monkeypatch.setattr(poller.settings, "activity_pacing", True)
    poller.refresh_activity.__globals__["_activity"].update(at=None, max_id={}, cuts={})
    poller.refresh_activity(wait=True)
    assert poller._activity["max_id"] == {old.id: 1, fresh.id: 2}
    assert poller.activity_factor(fresh) == 1 and poller.activity_factor(old) == 4
    poller._activity.update(at=None, max_id={}, cuts={})


@pytest.mark.asyncio
async def test_each_platform_gets_its_own_small_connection_pool(session, monkeypatch, tmp_path):
    """One big shared pool slowed every request once thousands of one-off hosts were in it (2026-11-13)."""
    import asyncio

    from app.config import settings
    from app.pipeline import poller
    from app.sources import ADAPTERS
    from app.sources.base import Adapter

    seen = {}

    def make(name, slots):
        class A(Adapter):
            ats = name
            max_concurrency = slots

            async def fetch(self, client, slug, **_):
                seen.setdefault(name, set()).add(id(client))
                pool = client._transport._pool
                seen[name + ":limit"] = pool._max_connections
                return []
        return A()

    monkeypatch.setitem(ADAPTERS, "shared", make("shared", 25))
    monkeypatch.setitem(ADAPTERS, "perhost", make("perhost", 6))
    empty = tmp_path / "companies.yaml"
    empty.write_text("[]")
    monkeypatch.setattr(settings, "companies_file", empty)

    async def nothing(*a, **k):
        return []

    monkeypatch.setattr(poller, "maybe_sync_yc", nothing)
    monkeypatch.setattr(poller, "maybe_sync_lists", nothing)
    monkeypatch.setattr(poller, "verify_due", lambda *a, **k: asyncio.sleep(0, 0))
    session.add_all([Company(name="A", ats="shared", slug="a"), Company(name="B", ats="shared", slug="b"),
                     Company(name="C", ats="perhost", slug="c")])
    session.commit()

    await poller.poll_once("fast")
    assert len(seen["shared"]) == 1 and len(seen["perhost"]) == 1 and seen["shared"] != seen["perhost"]
    assert seen["shared:limit"] == 30 and seen["perhost:limit"] == 11  # its fetch slots, plus 5
