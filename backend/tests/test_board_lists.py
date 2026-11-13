"""Board-slug lists: slugs normalized, each board checked once for US jobs, only those added."""

import asyncio

import httpx
import respx

from app.models import Company, ProbeCache
from app.pipeline import board_lists
from app.pipeline.board_lists import ALL_KNOWN, normalize_slug, parse_lists, vet


def test_slugs_are_put_in_our_form():
    assert normalize_slug("icims", "careers-bancorpbank") == "bancorpbank"
    assert normalize_slug("icims", "us-erac") == "us-erac.icims.com"
    assert normalize_slug("workday", "23andme|wd5|23") == "23andme|wd5|23"
    assert normalize_slug("workday", "slr|wd103|robots.txt") is None
    assert normalize_slug("workday", "acme|wd5") is None
    assert normalize_slug("greenhouse", "a|b") is None
    assert normalize_slug("greenhouse", "  stripe/ ") == "stripe"
    assert normalize_slug("lever", "") is None


def test_lists_are_merged_without_duplicates():
    riley = {"greenhouse": ["Stripe", "stripe", "acme"], "icims": ["careers-peraton"]}
    open_ats = {"providers": {"greenhouse": {"live": [["stripe", 50], ["newco", 3]]},
                              "teamtailor": {"live": [["eu-co", 4]]}, "breezy": {"live": [["shop", 2]]}}}
    got = parse_lists(riley, open_ats)
    assert sorted(got) == [("breezy", "shop"), ("greenhouse", "Stripe"), ("greenhouse", "acme"),
                           ("greenhouse", "newco"), ("icims", "peraton")]


def test_all_known_skips_details_but_is_truthy():
    assert "anything" in ALL_KNOWN and bool(ALL_KNOWN)


@respx.mock
def test_vet_counts_us_jobs_from_one_list_request():
    respx.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs").mock(return_value=httpx.Response(200, json={
        "jobs": [{"id": 1, "title": "Engineer", "absolute_url": "https://x/1", "location": {"name": "Austin, TX"},
                  "updated_at": "2026-11-01T00:00:00Z"},
                 {"id": 2, "title": "Engineer", "absolute_url": "https://x/2", "location": {"name": "Berlin, Germany"},
                  "updated_at": "2026-11-01T00:00:00Z"}]}))
    detail = respx.get(url__regex=r"https://boards-api.greenhouse.io/v1/boards/acme/jobs/\d+").mock(
        return_value=httpx.Response(500))

    async def run():
        async with httpx.AsyncClient() as client:
            return await vet(client, "greenhouse", "acme")

    assert asyncio.run(run()) == (2, 1)
    assert not detail.called


@respx.mock
def test_vet_workday_takes_one_list_call_and_ignores_location_counts():
    respx.post("https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/External/jobs").mock(
        return_value=httpx.Response(200, json={"total": 3, "jobPostings": [
            {"title": "A", "externalPath": "/job/a", "locationsText": "3 Locations"},
            {"title": "B", "externalPath": "/job/b", "locationsText": "Toronto, Ontario, Canada"},
            {"title": "C", "externalPath": "/job/c", "locationsText": "Chicago, IL"}]}))

    async def run():
        async with httpx.AsyncClient() as client:
            return await vet(client, "workday", "acme|wd5|External")

    assert asyncio.run(run()) == (2, 1)


@respx.mock
def test_vet_returns_none_for_a_dead_board():
    respx.get("https://api.lever.co/v0/postings/gone").mock(return_value=httpx.Response(404))

    async def run():
        async with httpx.AsyncClient() as client:
            return await vet(client, "lever", "gone")

    assert asyncio.run(run()) is None


def test_only_boards_with_us_jobs_are_added_and_verdicts_are_kept(session, monkeypatch):
    session.add(Company(name="Tracked", ats="greenhouse", slug="tracked"))
    session.commit()
    candidates = [("greenhouse", "tracked"), ("greenhouse", "usco"), ("greenhouse", "euco"), ("lever", "dead")]
    checked = []

    async def fake_lists(client):
        return candidates

    async def fake_vet(client, ats, slug):
        checked.append(slug)
        return {"usco": (5, 2), "euco": (4, 0), "dead": None}[slug]

    monkeypatch.setattr(board_lists, "fetch_lists", fake_lists)
    monkeypatch.setattr(board_lists, "vet", fake_vet)

    async def run(**kw):
        async with httpx.AsyncClient() as client:
            return await board_lists.from_board_lists(client, **kw)

    assert asyncio.run(run(dry_run=True)) == 1
    assert sorted(checked) == ["dead", "euco", "usco"]  # the tracked board isn't checked
    assert session.query(Company).count() == 1  # a dry run adds nothing
    found = {p.company_key: p.found for p in session.query(ProbeCache)}
    assert found == {"bl:greenhouse:usco": "us:2", "bl:greenhouse:euco": "no", "bl:lever:dead": "no"}

    checked.clear()
    assert asyncio.run(run()) == 1  # the dry run's verdicts stand: nothing is checked again
    assert checked == []
    session.expire_all()
    assert sorted(c.slug for c in session.query(Company)) == ["tracked", "usco"]


def test_max_add_adds_the_biggest_boards_first_and_leaves_the_rest_for_later(session, monkeypatch):
    checked = []

    async def fake_lists(client):
        return [("greenhouse", "small"), ("lever", "big"), ("ashby", "mid")]

    async def fake_vet(client, ats, slug):
        checked.append(slug)
        return {"small": (3, 1), "big": (90, 40), "mid": (10, 7)}[slug]

    monkeypatch.setattr(board_lists, "fetch_lists", fake_lists)
    monkeypatch.setattr(board_lists, "vet", fake_vet)

    async def run(**kw):
        async with httpx.AsyncClient() as client:
            return await board_lists.from_board_lists(client, **kw)

    assert asyncio.run(run(max_add=1)) == 1
    assert [c.slug for c in session.query(Company)] == ["big"]
    assert asyncio.run(run(max_add=1)) == 1  # the next batch comes from the saved verdicts
    session.expire_all()
    assert sorted(c.slug for c in session.query(Company)) == ["big", "mid"]
    assert sorted(checked) == ["big", "mid", "small"]  # each board was checked once
    assert asyncio.run(run(max_add=5)) == 1
    session.expire_all()
    assert session.query(Company).count() == 3
