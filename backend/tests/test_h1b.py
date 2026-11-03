from app import h1b
from app.h1b import SponsorIndex, aggregate, name_tokens
from app.models import Company, H1BSponsor
from app.pipeline.poller import process_company
from app.prefs import get_prefs
from app.sources import NormalizedJob

CSV = """CASE_STATUS,VISA_CLASS,EMPLOYER_NAME,JOB_TITLE,WORKSITE_STATE
Certified,H-1B,AMAZON.COM SERVICES LLC,SOFTWARE DEVELOPMENT ENGINEER,WA
Certified,H-1B,AMAZON.COM SERVICES LLC,DATA ENGINEER,WA
Certified,H-1B,Amazon Web Services Inc.,SOLUTIONS ARCHITECT,VA
Certified - Withdrawn,H-1B,META PLATFORMS INC,DATA SCIENTIST,CA
Denied,H-1B,META PLATFORMS INC,DATA SCIENTIST,CA
Certified,E-3 Australian,META PLATFORMS INC,ENGINEER,CA
Certified,H-1B,VIA TRANSPORTATION INC,ANALYST,NY
Certified,H-1B,VIAVI SOLUTIONS INC,ENGINEER,CA
"""


def test_name_tokens_drop_legal_words():
    assert name_tokens("AMAZON.COM SERVICES LLC") == ("amazon",)
    assert name_tokens("JPMorgan Chase & Co.") == ("jpmorgan", "chase")
    assert name_tokens("Meta Platforms, Inc.") == ("meta", "platforms")


def test_aggregate_counts_certified_h1b_only(tmp_path):
    path = tmp_path / "lca.csv"
    path.write_text(CSV, encoding="utf-8")
    rows = {r["name_key"]: r for r in aggregate(path)}
    assert rows["amazon"]["filings"] == 2 and rows["amazon web"]["filings"] == 1
    assert rows["meta platforms"]["filings"] == 1  # "Certified - Withdrawn" counts; Denied and E-3 don't
    assert rows["amazon"]["top_states"] == ["WA"]


def test_index_prefix_matching():
    index = SponsorIndex([("amazon", 2), ("amazon web", 1), ("meta platforms", 1), ("via transportation", 1), ("viavi", 1)])
    assert index.filings("Amazon") == 3
    assert index.filings("Amazon Web Services") == 1
    assert index.filings("Meta") == 1
    assert index.filings("Via") == 0  # short single word: generic suffixes only, so no "Via Transportation"/"Viavi"
    assert index.filings("Stripe") == 0
    assert SponsorIndex([("virtual staffing", 50), ("virtual", 2)]).filings("Virtual") == 2


def test_import_sets_job_counts(session, now, tmp_path):
    company = Company(name="Amazon", ats="greenhouse", slug="amazon")
    session.add(company)
    session.flush()
    process_company(session, company, [NormalizedJob(external_id="1", title="SDE", url="u", locations=["Seattle, WA"])],
                    get_prefs(session), now)
    session.commit()
    path = tmp_path / "lca.csv"
    path.write_text(CSV, encoding="utf-8")
    assert h1b.import_file(session, path, "lca.csv") == 5
    assert session.query(H1BSponsor).count() == 5
    assert company.jobs[0].h1b_filings == 3
    h1b._index = None
