"""GitHub job lists (pipeline/startup_lists.py): board datasets and job tables with ATS links."""

import httpx
import pytest
import respx

from app.models import Company
from app.pipeline.startup_lists import BOARD_LISTS, JOB_TABLES, board_from_row, links_in, sync_lists


def test_board_rows_map_to_our_slugs():
    assert board_from_row({"ats": "greenhouse", "slug": "1800contacts"}) == ("greenhouse", "1800contacts")
    assert board_from_row({"ats": "workday", "slug": "2020companies", "wd": "wd1", "site": "external_careers"}) == (
        "workday", "2020companies|wd1|external_careers")
    assert board_from_row({"ats": "oracle", "slug": "x", "host": "eczy.fa.us2.oraclecloud.com", "site": "CX_1"}) == (
        "oracle_hcm", "eczy.fa.us2.oraclecloud.com|CX_1")
    assert board_from_row({"ats": "amazon", "slug": "amazon"}) is None  # tracked once by its own adapter
    assert board_from_row({"ats": "workday", "slug": "x"}) is None  # no host or site: can't poll it
    assert board_from_row({"ats": "unknownats", "slug": "x"}) is None


def test_links_are_read_from_html_and_markdown_tables():
    md = '<a href="https://jobs.ashbyhq.com/acme/1">Apply</a> | [Apply](https://boards.greenhouse.io/beta/jobs/2)'
    assert links_in(md) == ["https://jobs.ashbyhq.com/acme/1", "https://boards.greenhouse.io/beta/jobs/2"]


@pytest.mark.asyncio
@respx.mock
async def test_sync_adds_listed_boards_and_boards_behind_job_links(session):
    session.add(Company(name="Known", ats="greenhouse", slug="known"))
    session.commit()
    respx.get(BOARD_LISTS[0]).mock(return_value=httpx.Response(200, json=[
        {"name": "Known", "ats": "greenhouse", "slug": "KNOWN"}, {"name": "Acme AI", "ats": "ashby", "slug": "acme-ai"}]))
    for i, url in enumerate(JOB_TABLES):
        body = '<a href="https://jobs.lever.co/zeta/123">Apply</a>' if i == 0 else ""
        respx.get(url).mock(return_value=httpx.Response(200, text=body))
    async with httpx.AsyncClient() as client:
        added = await sync_lists(session, client)
    assert sorted((c.ats, c.slug) for c in added) == [("ashby", "acme-ai"), ("lever", "zeta")]
