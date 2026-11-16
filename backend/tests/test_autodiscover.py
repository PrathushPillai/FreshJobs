from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx

from app.models import Company, EVerifyAccount, H1BRole, H1BSponsor, ProbeCache
from app.pipeline import autodiscover
from app.pipeline.autodiscover import Finder, brand_variants, career_links, names_employer, next_names


def test_brand_variants_drop_generic_words():
    v = brand_variants("Fidelity Technology Group, LLC")
    assert v[0] == "fidelity"
    assert "fidelitytechnologygroup" in v
    assert brand_variants("Capital One Services, LLC")[0] == "capitalone"
    assert brand_variants("WAL-MART ASSOCIATES, INC.")[0] == "walmart"


def test_names_employer_rejects_parked_and_unrelated_pages():
    assert names_employer("<title>Acme Robotics | Home</title>", "Acme Robotics Inc")
    assert not names_employer("<title>Welcome</title> This domain is for sale!", "Acme Robotics Inc")
    assert not names_employer("<title>Shoes and more</title><body>shop now</body>", "Acme Robotics Inc")


def test_career_links_by_url_or_label():
    html = ('<a href="/about">About</a><a href="/company/join">Join our team</a>'
            '<a href="https://acme.com/careers">x</a><a href="/files/jobs.pdf">Jobs PDF</a>')
    assert career_links(html, "https://acme.com/") == ["https://acme.com/company/join", "https://acme.com/careers"]


@pytest.mark.asyncio
@respx.mock
async def test_finds_board_behind_careers_page():
    respx.get(url__regex=r".*/robots\.txt").mock(return_value=httpx.Response(404))
    respx.get(url__regex=r"https://www.acmerobotics.com/?$").mock(return_value=httpx.Response(
        200, html='<title>Acme Robotics</title><a href="/careers">Careers</a>'))
    respx.get("https://www.acmerobotics.com/careers").mock(return_value=httpx.Response(
        200, html='<a href="https://job-boards.greenhouse.io/acmerobotics">See open roles</a>'))
    respx.route().mock(return_value=httpx.Response(404))  # every other guess
    async with httpx.AsyncClient(follow_redirects=True) as client:
        assert await Finder(client).board_for("Acme Robotics Inc") == ("greenhouse", "acmerobotics")


@pytest.mark.asyncio
@respx.mock
async def test_careers_page_with_jobposting_becomes_website_source():
    respx.get(url__regex=r".*/robots\.txt").mock(return_value=httpx.Response(404))
    respx.get(url__regex=r"https://www.smallco.com/?$").mock(return_value=httpx.Response(
        200, html='<title>SmallCo</title><a href="/careers">Careers</a>'))
    respx.get("https://www.smallco.com/careers").mock(return_value=httpx.Response(
        200, html='<script type="application/ld+json">{"@type": "JobPosting", "title": "Engineer"}</script>'))
    respx.route().mock(return_value=httpx.Response(404))
    async with httpx.AsyncClient(follow_redirects=True) as client:
        assert await Finder(client).board_for("SmallCo LLC") == ("website", "https://www.smallco.com/careers")


@pytest.mark.asyncio
@respx.mock
async def test_robots_txt_is_honoured():
    respx.get("https://www.acmerobotics.com/robots.txt").mock(return_value=httpx.Response(
        200, text="User-agent: *\nDisallow: /"))
    respx.get(url__regex=r"https://(www\.)?acme[a-z-]*\.com/robots\.txt").mock(return_value=httpx.Response(
        200, text="User-agent: *\nDisallow: /"))
    page = respx.get(url__regex=r"https://(www\.)?acme[a-z-]*\.com/?$").mock(return_value=httpx.Response(200, html="x"))
    async with httpx.AsyncClient(follow_redirects=True) as client:
        assert await Finder(client).board_for("Acme Robotics Inc") is None
    assert not page.called


def test_next_names_order_and_skips(session):
    session.add_all([
        H1BSponsor(name="Big IT Sponsor Inc", name_key="big it sponsor", filings=500),
        H1BRole(name_key="big it sponsor", soc_code="15-1252", soc_title="Software Developers", filings=400),
        H1BSponsor(name="Small IT Shop LLC", name_key="small it shop", filings=20),
        H1BRole(name_key="small it shop", soc_code="15-1252", soc_title="Software Developers", filings=20),
        H1BSponsor(name="Nurses Only Hospital", name_key="nurses only hospital", filings=900),
        EVerifyAccount(first_token="huge", name_key="huge retailer", name="Huge Retailer Inc", open=True,
                       workforce="10,000 and over"),
        EVerifyAccount(first_token="mid", name_key="mid bank", name="Mid Bank", open=True, workforce="500 to 999"),
        EVerifyAccount(first_token="tiny", name_key="tiny cafe", name="Tiny Cafe", open=True, workforce="5 to 9"),
        EVerifyAccount(first_token="closed", name_key="closed co", name="Closed Co", open=False,
                       workforce="10,000 and over"),
        Company(name="Tracked Already", ats="greenhouse", slug="tracked"),
        EVerifyAccount(first_token="tracked", name_key="tracked already", name="Tracked Already", open=True,
                       workforce="10,000 and over"),
        ProbeCache(company_key="site:midbank", probed_at=datetime.now(timezone.utc)),
    ])
    session.commit()
    assert next_names(session, 10) == ["Big IT Sponsor Inc", "Small IT Shop LLC", "Huge Retailer Inc",
                                       "Nurses Only Hospital"]  # Mid Bank was looked for, Tiny Cafe is too small
    # Looked for long ago: looked for again.
    session.get(ProbeCache, "site:midbank").probed_at = datetime.now(timezone.utc) - timedelta(days=400)
    session.commit()
    assert "Mid Bank" in next_names(session, 10)
    assert next_names(session, 2) == ["Big IT Sponsor Inc", "Small IT Shop LLC"]


@pytest.mark.asyncio
async def test_run_once_adds_found_boards_and_caches(session, monkeypatch, tmp_path):
    session.add(H1BSponsor(name="Acme Robotics Inc", name_key="acme robotics", filings=10))
    session.add(H1BRole(name_key="acme robotics", soc_code="15-1252", soc_title="x", filings=10))
    session.add(H1BSponsor(name="Nothing Corp", name_key="nothing", filings=5))
    session.commit()

    async def fake_board_for(self, name):
        return ("greenhouse", "acmerobotics") if name.startswith("Acme") else None

    async def no_discover(session, client, names, limit):
        return []

    async def no_workday(client, names):
        return 0

    monkeypatch.setattr(Finder, "board_for", fake_board_for)
    monkeypatch.setattr("app.pipeline.discovery.discover", no_discover)
    monkeypatch.setattr("app.pipeline.bulk_discovery._workday_for", no_workday)
    monkeypatch.setattr(autodiscover, "_status_path", lambda: tmp_path / "autodiscover.json")
    summary = await autodiscover.run_once(10)
    assert summary["looked_for"] == 2 and summary["added_from_websites"] == 1
    session.expire_all()
    assert session.query(Company).filter_by(ats="greenhouse", slug="acmerobotics").one().name == "Acme Robotics Inc"
    assert session.get(ProbeCache, "site:nothing").found is None
    assert (await autodiscover.run_once(10))["looked_for"] == 0  # everything was looked for
    assert autodiscover.status()["total_added"] == 1


@pytest.mark.parametrize("name,hit,domain,want", [
    ("Deloitte Consulting LLP", ("avature", "apply.deloitte.com/careers"), "deloitte.com", True),
    ("Walmart Associates, Inc.", ("avature", "walmart.avature.net/militaryevents"), "walmart.com", True),
    ("Intuit Inc.", ("radancy", "jobs.intuit.com"), "intuit.com", True),
    ("Capital One Services, LLC", ("eightfold", "capitalone|capitalone.com"), "capitalone.com", True),
    ("Kforce Inc.", ("taleo", "myhiring.kforce.com|ex"), "kforce.com", True),
    ("ZS Associates, Inc.", ("icims", "zs"), "zs.com", True),
    # Found on real employer sites in the first run, and wrong:
    ("Wells Fargo Bank, N.A.", ("avature", "connect.secure.wellsfargo.com/auth"), "wellsfargo.com", False),
    ("Truist Bank", ("avature", "dias.bank.truist.com/ui"), "truist.com", False),
    ("Navy Federal Credit Union", ("avature", "navyfederal.lendkey.com/access"), "navyfederal.org", False),
    ("Aetna Resources LLC", ("avature", "www.aetnainternational.com/members"), "aetna.com", False),
    ("Goldman Sachs Bank USA", ("avature", "recruiting360.avature.net/candidates"), "goldmansachs.com", False),
    ("Zoom Communications, Inc.", ("workday", "fortune|wd108|Fortune"), "zoom.com", False),
    ("HTC Global Services Inc.", ("icims", "globalcareers-htcvive.icims.com"), "htcinc.com", False),
])
def test_plausible_board(name, hit, domain, want):
    assert autodiscover.plausible_board(hit, name, domain) is want


def test_employee_only_icims_portal_is_not_a_board():
    from app.pipeline.detect import detect_board
    assert detect_board("https://employees-docusign.icims.com/jobs/search") is None


def test_harvest_skips_boards_whose_name_would_be_an_id(session):
    from app.pipeline.harvest import harvest
    url = ("https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html"
           "?cid=2151eaf7-7506-401d-a16c-71efc612b9e4&jobId=1")
    assert harvest(session, [("", url)], 10) == []
    assert [c.name for c in harvest(session, [("Acme Corp", url)], 10)] == ["Acme Corp"]


@pytest.mark.asyncio
async def test_discovery_skips_while_offline(session, monkeypatch):
    from app.pipeline import autodiscover, poller

    monkeypatch.setattr(poller, "_online_cache", None)
    monkeypatch.setattr(poller, "_resolves", lambda host: False)
    called = []
    monkeypatch.setattr(autodiscover, "next_names", lambda *a, **k: called.append(1) or ["Acme"])
    assert (await autodiscover.run_once(5))["offline"] is True
    assert not called


def test_next_names_leave_out_consultancies_and_staffing(session):
    session.add_all([
        H1BSponsor(name="Big Outsourcer Ltd", name_key="big outsourcer", filings=9000, kind="consultancy"),
        H1BRole(name_key="big outsourcer", soc_code="15-1252", soc_title="Software Developers", filings=9000),
        H1BSponsor(name="Product Co Inc", name_key="product co", filings=300, kind=""),
        H1BRole(name_key="product co", soc_code="15-1252", soc_title="Software Developers", filings=300),
        H1BSponsor(name="Placement Partners", name_key="placement partners", filings=5000, kind="staffing"),
        EVerifyAccount(first_token="apex", name_key="apex it consulting", name="Apex IT Consulting LLC", open=True,
                       workforce="10,000 and over"),
        EVerifyAccount(first_token="maker", name_key="maker robotics", name="Maker Robotics", open=True,
                       workforce="10,000 and over"),
    ])
    session.commit()
    assert next_names(session, 10) == ["Product Co Inc", "Maker Robotics"]


@pytest.mark.parametrize("name, naics, client, dependent, kind", [
    ("Google LLC", "519290", 0.0, 0.0, ""),                       # product company, own industry code
    ("Microsoft Corporation", "513210", 0.0, 0.0, ""),
    ("Infosys Limited", "541511", 0.4, 0.95, "consultancy"),       # workers at client sites
    ("Acme Software Inc", "541511", 0.0, 0.8, "consultancy"),      # H-1B dependent IT services
    ("Acme Software Inc", "541511", 0.0, 0.0, ""),                 # a software maker filed under 541511
    ("Bright Consulting LLC", "541512", 0.0, 0.0, "consultancy"),  # services name + IT-services code
    ("Robert Half", "561311", 0.0, 0.0, "staffing"),
    ("Retail Giant", "455110", 0.35, 0.0, "consultancy"),          # anyone placing many workers at clients
    ("Axtria Inc", "54151", 0.2, 1.0, "consultancy"),              # a 5-digit NAICS code
    ("Ernst & Young U.S. LLP", "541211", 0.185, 0.0, "consultancy"),
    ("Deloitte & Touche LLP", "541211", 0.018, 0.0, ""),           # audit work at its own offices
    ("Salesforce, Inc.", "541511", 0.0, 0.0, ""),
])
def test_employer_kind(name, naics, client, dependent, kind):
    from app.h1b import employer_kind

    assert employer_kind(name, naics, client, dependent) == kind


@pytest.mark.parametrize("url, hit", [
    ("https://boards.greenhouse.io/${boardToken}", None),
    ("https://jobs.lever.co/${options.accountName}", None),
    ("https://jobs.lever.co/%24%7BSITE%7D", None),
    ('https://jobs.ashbyhq.com/strava","color":"050', ("ashby", "strava")),
    ('https://jobs.lever.co/statespacelabs","is_external":"on"', ("lever", "statespacelabs")),
    ("https://jobs.ashbyhq.com/Honey Homes", ("ashby", "Honey Homes")),  # Ashby names may have spaces
])
def test_board_urls_from_page_scripts(url, hit):
    from app.pipeline.detect import detect_board

    assert detect_board(url) == hit
