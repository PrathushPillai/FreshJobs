"""SBIR/STTR awardees: recent firms with staff, one per website; contact details never kept."""

import asyncio

import httpx
import respx

from app.pipeline import sbir
from app.pipeline.sbir import firms

HEADER = ('Company,"Award Title",Agency,"Award Year","Number Employees","Company Url",City,State,'
          '"Contact Name","Contact Email","PI Name","PI Email"\n')


def row(company, year, staff, url):
    return {"Company": company, "Award Year": str(year), "Number Employees": str(staff), "Company Url": url}


def test_recent_firms_with_staff_one_per_website():
    got = firms([
        row("AGILE DATA DECISIONS, INC.", 2024, 6, "http://agiledd.ai"),
        row("AGILE DATA DECISIONS, INC.", 2025, 6, "https://www.agiledd.ai/about"),  # same website
        row("ATA ENGINEERING, INC.", 2024, 205, "www.ata-e.com"),
        row("Old Co LLC", 2019, 50, "oldco.com"),  # no recent award
        row("Two People LLC", 2025, 2, "tiny.io"),  # too small to have a job board
        row("No Site Inc", 2025, 40, ""),
        row("Campus Lab", 2025, 40, "lab.university.edu"),
    ], since_year=2023, min_employees=5)
    assert got == [("Agile Data Decisions", "https://agiledd.ai"), ("Ata Engineering", "https://www.ata-e.com")]


@respx.mock
def test_the_file_is_streamed_read_and_deleted_and_contacts_are_dropped(tmp_path, monkeypatch):
    csv_text = HEADER + ('"ROBO WORKS INC","Arm",DoD,2025,30,https://roboworks.com,Austin,TX,'
                         '"Jane Doe",jane@roboworks.com,"John Roe",john@roboworks.com\n')
    respx.get(sbir.URL).mock(return_value=httpx.Response(200, text=csv_text, headers={"Last-Modified": "Sun, 01 Feb 2026"}))
    seen = {}

    async def fake_behind(client, sites, label, dry_run=False):
        seen["sites"] = sites
        return 1

    from app.pipeline import bulk_discovery

    monkeypatch.setattr(bulk_discovery, "_boards_behind", fake_behind)
    monkeypatch.setattr(sbir.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr("app.config.settings.database_url", "postgresql://x")  # temp file goes to tmp_path

    async def run():
        async with httpx.AsyncClient() as client:
            return await sbir.from_sbir(client)

    assert asyncio.run(run()) == (1, "Sun, 01 Feb 2026")
    assert seen["sites"] == [("Robo Works", "https://roboworks.com")]
    assert list(tmp_path.iterdir()) == []  # the download is deleted


def test_reader_keeps_only_company_columns(tmp_path):
    path = tmp_path / "a.csv"
    path.write_text(HEADER + '"X CO",t,NIH,2025,9,x.com,Boston,MA,"Jane",jane@x.com,"Bob",bob@x.com\n', encoding="utf-8")
    captured = []
    real = sbir.firms

    def spy(rows, *a):
        rows = list(rows)
        captured.extend(rows)
        return real(rows, *a)

    sbir.firms, old = spy, sbir.firms
    try:
        sbir._read(path, 2023, 5)
    finally:
        sbir.firms = old
    assert set(captured[0]) == set(sbir.COLUMNS) and "jane@x.com" not in str(captured)
