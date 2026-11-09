"""Robotics and healthcare discovery (bulk_discovery niche): list pages, robots.txt, H-1B sponsors, dry runs."""

import httpx
import pytest
import respx
from sqlalchemy import select

from app.models import Company, H1BRole, H1BSponsor
from app.pipeline import bulk_discovery as bd


def test_company_sites_skip_universities_docs_and_social():
    page = """
    - [Acme Robotics](https://www.acmerobotics.com/about) ROS 2 arms
    - [Paper](https://arxiv.org/abs/1234) and [docs](https://docs.beta.io/start)
    - <a href="https://robotics.mit.edu/lab">MIT lab</a> <a href="https://www.cs.ox.ac.uk/">Oxford</a>
    - <a href="https://twitter.com/acme">Twitter</a> <a href="https://github.com/acme/ros">code</a>
    - [Gamma Health](https://gammahealth.com) [again](https://gammahealth.com/careers)
    """
    assert bd.company_sites(page, "https://raw.githubusercontent.com/x/list/README.md") == [
        "https://acmerobotics.com", "https://gammahealth.com"]


def test_sector_sponsors_are_it_filers_in_the_sector_most_it_filings_first(session):
    def sponsor(name, key, it, other=0):
        session.add(H1BSponsor(name=name, name_key=key, filings=it + other))
        session.add(H1BRole(name_key=key, soc_code="15-1252", soc_title="Software Developers", filings=it))
        if other:
            session.add(H1BRole(name_key=key, soc_code="29-1141", soc_title="Registered Nurses", filings=other))

    sponsor("Mercy Health System", "mercy health system", it=5, other=400)  # nurses mostly: still 5 IT filings
    sponsor("Cerner Healthcare Solutions LLC", "cerner healthcare solutions", it=120)
    sponsor("Acme Software Inc", "acme software", it=300)  # not healthcare
    sponsor("Vecna Robotics Inc", "vecna robotics", it=12)
    session.add(H1BSponsor(name="City Hospital", name_key="city hospital", filings=80))  # no IT filings at all
    session.add(H1BRole(name_key="city hospital", soc_code="29-1141", soc_title="Registered Nurses", filings=80))
    session.commit()
    assert bd.niche_sponsors(session, "health", 10) == ["Cerner Healthcare Solutions LLC", "Mercy Health System"]
    assert bd.niche_sponsors(session, "robotics", 10) == ["Vecna Robotics Inc"]
    assert bd.niche_sponsors(session, "health", 1) == ["Cerner Healthcare Solutions LLC"]


@pytest.mark.asyncio
@respx.mock
async def test_niche_reads_allowed_lists_and_adds_boards_behind_their_websites(session, monkeypatch):
    allowed, blocked = "https://lists.example.org/robots-list", "https://blocked.example.com/portfolio"
    monkeypatch.setitem(bd.NICHE_PAGES, "robotics", [blocked, allowed])
    respx.get("https://blocked.example.com/robots.txt").mock(return_value=httpx.Response(200, text="User-agent: *\nDisallow: /"))
    blocked_page = respx.get(blocked).mock(return_value=httpx.Response(200, text='<a href="https://never.com">x</a>'))
    respx.get("https://lists.example.org/robots.txt").mock(return_value=httpx.Response(404))
    respx.get(allowed).mock(return_value=httpx.Response(200, text=(
        '<a href="https://acmebots.com">Acme</a> <a href="https://knownbots.com">Known</a> <a href="https://noboard.ai">x</a>')))
    session.add(Company(name="Known Bots", ats="lever", slug="knownbots"))
    session.commit()

    boards = {"https://acmebots.com/careers": ("greenhouse", "acmebots"), "https://knownbots.com/careers": ("lever", "knownbots")}

    async def fake_detect(client, url):
        return boards.get(url)

    monkeypatch.setattr(bd, "detect_from_page", fake_detect)
    async with httpx.AsyncClient(headers={"User-Agent": "FreshJobs test"}) as client:
        # A dry run finds the one untracked board and saves nothing.
        assert await bd.from_niche(client, "robotics", dry_run=True, sponsors=0) == 1
        assert session.scalar(select(Company).where(Company.slug == "acmebots")) is None
        # The real run adds it; the board we already track isn't added again.
        assert await bd.from_niche(client, "robotics", sponsors=0) == 1
    session.expire_all()
    assert [(c.ats, c.slug) for c in session.scalars(select(Company).order_by(Company.slug))] == [
        ("greenhouse", "acmebots"), ("lever", "knownbots")]
    assert not blocked_page.called  # robots.txt disallowed it


@pytest.mark.asyncio
async def test_workday_probe_skips_names_tracked_or_already_probed(session, monkeypatch):
    from app.models import ProbeCache
    from app.pipeline.normalize import company_key

    session.add(Company(name="Tracked Health", ats="greenhouse", slug="trackedhealth"))
    session.add(ProbeCache(company_key=f"wd:{company_key('Probed Health')}", found=None))
    session.commit()
    asked: list[str] = []

    async def fake_find(client, name):
        asked.append(name)
        return ("workday", "newhealth|wd5|External") if name == "New Health" else None

    monkeypatch.setattr(bd, "find_workday", fake_find)
    async with httpx.AsyncClient() as client:
        added = await bd._workday_for(client, ["Tracked Health", "Probed Health", "New Health", "New Health", "Other Health"])
    assert added == 1 and asked == ["New Health", "Other Health"]
    session.expire_all()
    assert session.scalar(select(Company).where(Company.ats == "workday")).slug == "newhealth|wd5|External"
