"""Real employer names for boards named after their slug (app/pipeline/source_names.py)."""

from datetime import datetime, timedelta, timezone

import pytest

from app.models import Company, Job, JobEvent
from app.pipeline import source_names as sn


def co(name, ats, slug):
    return Company(name=name, ats=ats, slug=slug)


@pytest.mark.parametrize("name,company,ok", [
    ("Memorial Hermann Health System", co("Memorialhermann", "workday", "memorialhermann|wd5|external"), True),
    ("AO Garcia Agency", co("Aogarciaagency", "lever", "aogarciaagency"), True),
    ("Northeast Georgia Health System", co("Nghs", "workday", "nghs|wd1|external"), True),  # initials
    ("The TJX Companies", co("Tjx", "workday", "tjx|wd1|TJX_EXTERNAL"), True),
    ("Rambus", co("Careers Rambus.Icims.Com", "icims", "careers-rambus.icims.com"), True),
    ("Marshalls of MA", co("Tjx", "workday", "tjx|wd1|TJX_EXTERNAL"), False),  # a subsidiary's legal name
    ("Workday", co("Acme", "workday", "acme|wd1|x"), False),
])
def test_a_name_must_belong_to_the_board(name, company, ok):
    assert sn.belongs(name, company) is ok


def test_tidy_and_fallback():
    assert sn.tidy("WAL-MART ASSOCIATES, INC.") == "Wal-Mart Associates"
    assert sn.tidy("Acme &amp; Sons, LLC") == "Acme & Sons"
    assert sn.fallback(co("Tjx", "workday", "tjx|wd1|x")) == "TJX"
    assert sn.fallback(co("Acadiahealthcare", "icims", "acadiahealthcare")) is None


def test_only_slug_named_single_employer_boards_need_a_name():
    assert sn.needs_name(co("Tjx", "workday", "tjx|wd1|TJX_EXTERNAL"))
    assert not sn.needs_name(co("TJX Companies", "workday", "tjx|wd1|TJX_EXTERNAL"))
    assert not sn.needs_name(co("New Grad", "simplify", "new-grad"))  # an aggregator


def test_rename_updates_the_board_and_its_jobs(session):
    now = datetime.now(timezone.utc)
    c = co("Nghs", "workday", "nghs|wd1|external")
    session.add(c)
    session.flush()
    session.add(Job(company_id=c.id, external_id="1", title="Nurse", url="https://x/1", company_name="Nghs",
                    company_key="nghs", fingerprint="old", first_seen_at=now, last_seen_at=now))
    session.commit()
    assert sn.rename(session, c, "Northeast Georgia Health System") == 1
    session.commit()
    job = session.query(Job).one()
    session.refresh(job)
    assert c.name == job.company_name == "Northeast Georgia Health System"
    assert job.company_key != "nghs" and job.fingerprint != "old"


def test_stale_jobs_of_switched_off_sources_close(session):
    from app.pipeline.poller import close_stale_of_disabled

    now = datetime.now(timezone.utc)
    off = Company(name="The Muse", ats="themuse", slug="x", enabled=False)
    on = Company(name="Acme", ats="greenhouse", slug="acme", enabled=True)
    session.add_all([off, on])
    session.flush()
    old = now - timedelta(days=30)
    for c, ext, seen in ((off, "a", old), (off, "b", now), (on, "c", old)):
        session.add(Job(company_id=c.id, external_id=ext, title="T", url="https://x", fingerprint=ext,
                        first_seen_at=old, last_seen_at=seen))
    session.commit()
    assert close_stale_of_disabled(session, now) == 1
    closed = {j.external_id for j in session.query(Job).filter(Job.closed_at.is_not(None))}
    assert closed == {"a"}
    assert session.query(JobEvent).filter_by(kind="closed").count() == 1


@pytest.mark.parametrize("slug,answers,name", [
    ("thermofisher|wd5|x", ["0704 Thermo Fisher Scientific Australia Pty"], "Thermo Fisher"),
    ("accenture|wd3|x", ["1500 Accenture do Brasil Ltda"], "Accenture"),
    ("dominos|wd1|x", ["Domino's Assistant Manager(03528)"], "Domino's"),
    ("walmart|wd5|x", ["Wal-Mart Canada"], "Wal-Mart"),
    ("aecom2|wd1|x", ["AECOM Secure Facilities"], "AECOM"),
    ("tjx|wd1|x", ["The TJX Companies", "Marshalls of MA"], "TJX"),
    ("nghs|wd1|x", ["Northeast Georgia Health System"], "Northeast Georgia Health System"),
    ("citi|wd5|x", ["12542 Citicorp Services India Private Limited"], None),  # a legal entity abroad
])
def test_choose_takes_the_brand_not_the_legal_entity(slug, answers, name):
    assert sn.choose(co("x", "workday", slug), answers) == name


def test_stale_closing_query_reads_only_the_index(session):
    """It must stay index-only: SQLite otherwise walked every open job per 500 sources (hours on the server)."""
    from sqlalchemy import text

    plan = " ".join(str(r) for r in session.execute(text(
        "EXPLAIN QUERY PLAN SELECT id FROM jobs INDEXED BY ix_jobs_company_sync WHERE company_id IN (1, 2) "
        "AND closed_at IS NULL AND last_seen_at < '2026-01-01'")))
    assert "ix_jobs_company_sync" in plan and "COVERING" in plan.upper()
