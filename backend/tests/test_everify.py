from app import everify
from app.models import Company, Job
from app.pipeline.poller import process_company
from app.prefs import get_prefs
from app.sources import NormalizedJob

HEADER = ["Last Updated Date", "Day of Last Updated Date", "Employer", "Doing Business As", "Account Status",
          "Opted into E-Verify+", "Date Enrolled", "Date Terminated", "Workforce Size", "Hiring Site Locations",
          "Number of Hiring Sites", "Number of Records"]
ROWS = [
    ["11/5/2026", "", "NVIDIA CORPORATION", "", "Open", "No", "3/6/2009", "", "10,000 and over", "CA", "1", "1"],
    ["11/5/2026", "", "Stripe, Inc.", "", "Terminated", "No", "9/5/2012", "07/18/2025", "20 to 99", "CA", "1", "1"],
    ["11/5/2026", "", "Stripe Lines Inc", "", "Open", "No", "7/1/2024", "", "20 to 99", "TX", "1", "1"],
    ["11/5/2026", "", "Meta Platforms, Inc.", "", "Open", "No", "1/2/2015", "", "10,000 and over", "CA", "1", "1"],
    ["11/5/2026", "", "Coffee Holdings LLC", "Baristas", "Open", "No", "1/2/2020", "", "5 to 9", "WA", "1", "1"],
]


def write_export(folder, rows):
    folder.mkdir(exist_ok=True)
    text = "\n".join("\t".join(r) for r in [HEADER, *rows]) + "\n"
    (folder / "Employer List.csv").write_text(text, encoding="utf-16")


def test_lookup_statuses(session, tmp_path):
    write_export(tmp_path / "everify", ROWS + ROWS[:1])  # a duplicate row across industry files
    assert everify.import_folder(session, tmp_path / "everify") == 6  # Baristas counts as a second name
    nvidia = everify.lookup(session, "NVIDIA")
    assert nvidia["status"] == "yes" and nvidia["since"].year == 2009
    stripe = everify.lookup(session, "Stripe")  # "Stripe Lines" is another company: not a generic suffix
    assert stripe["status"] == "ended" and stripe["ended"].year == 2025
    assert everify.lookup(session, "Meta")["status"] == "yes"
    assert everify.lookup(session, "Baristas")["status"] == "yes"  # found by its DBA name
    assert everify.lookup(session, "Globex") is None


def test_jobs_get_everify_status(session, now, tmp_path):
    write_export(tmp_path / "everify", ROWS)
    everify.import_folder(session, tmp_path / "everify")
    company = Company(name="NVIDIA", ats="workday", slug="nvidia")
    session.add(company)
    session.flush()
    process_company(session, company, [NormalizedJob(external_id="1", title="SDE", url="u", locations=["CA"])],
                    get_prefs(session), now)
    session.commit()
    assert session.query(Job).one().everify == "yes"
