"""detect_board on real careers URLs, and detect_from_page on respx-mocked careers pages."""

import httpx
import pytest
import respx

from app.pipeline.detect import detect_board, detect_from_page, detect_in_html
from app.sources import ADAPTERS


@pytest.mark.parametrize(
    "url, expected",
    [
        # Greenhouse
        ("https://boards.greenhouse.io/anthropic", ("greenhouse", "anthropic")),
        ("https://boards.greenhouse.io/airbnb/jobs/7212345?gh_jid=7212345", ("greenhouse", "airbnb")),
        ("https://job-boards.greenhouse.io/stripe", ("greenhouse", "stripe")),
        ("https://job-boards.greenhouse.io/databricks/jobs/8042213002", ("greenhouse", "databricks")),
        ("https://boards.greenhouse.io/embed/job_board?for=figma&b=https://www.figma.com/careers", ("greenhouse", "figma")),
        ("https://boards.greenhouse.io/embed/job_app?for=Brex&token=123", ("greenhouse", "brex")),
        ("https://boards-api.greenhouse.io/v1/boards/asana/jobs?content=true", ("greenhouse", "asana")),
        # Lever, Ashby
        ("https://jobs.lever.co/palantir", ("lever", "palantir")),
        ("https://jobs.lever.co/spotify/0a1b2c3d-4e5f/apply", ("lever", "spotify")),
        ("https://jobs.ashbyhq.com/openai", ("ashby", "openai")),
        ("https://jobs.ashbyhq.com/Ramp/6a7b8c?utm_source=x", ("ashby", "Ramp")),
        # Workday
        ("https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite", ("workday", "nvidia|wd5|NVIDIAExternalCareerSite")),
        ("https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite/job/US-CA-Santa-Clara/Engineer_JR1",
         ("workday", "nvidia|wd5|NVIDIAExternalCareerSite")),
        ("https://salesforce.wd12.myworkdayjobs.com/External_Career_Site", ("workday", "salesforce|wd12|External_Career_Site")),
        ("https://citi.wd5.myworkdayjobs.com/wday/cxs/citi/2/jobs", ("workday", "citi|wd5|2")),
        # SmartRecruiters, Workable
        ("https://jobs.smartrecruiters.com/ServiceNow", ("smartrecruiters", "ServiceNow")),
        ("https://jobs.smartrecruiters.com/Visa/744000012345-software-engineer", ("smartrecruiters", "Visa")),
        ("https://apply.workable.com/huggingface/", ("workable", "huggingface")),
        ("https://apply.workable.com/api/v1/widget/accounts/ecp-123", ("workable", "ecp-123")),
        # iCIMS
        ("https://careers-peraton.icims.com/jobs/search?ss=1", ("icims", "peraton")),
        ("https://careers-teksystems.icims.com/jobs/13992/business-support-associate/job", ("icims", "teksystems")),
        ("https://uscareers-acme.icims.com/jobs/intro", ("icims", "uscareers-acme.icims.com")),
        # Oracle Recruiting Cloud
        ("https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/requisitions",
         ("oracle_hcm", "jpmc.fa.oraclecloud.com|CX_1001")),
        ("https://eeho.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/jobsearch/job/312345",
         ("oracle_hcm", "eeho.fa.us2.oraclecloud.com|jobsearch")),
        # Eightfold
        ("https://paypal.eightfold.ai/careers?domain=paypal.com", ("eightfold", "paypal|paypal.com")),
        ("https://qualcomm.eightfold.ai/careers/job/446712345", ("eightfold", "qualcomm|qualcomm.com")),
        # Big companies
        ("https://www.amazon.jobs/en/search?base_query=software", ("amazon", "country=USA")),
        ("https://amazon.jobs/en/jobs/10595504/applied-scientist-agi", ("amazon", "country=USA")),
        ("https://careers.microsoft.com/us/en/search-results", ("microsoft", "United States")),
        ("https://jobs.careers.microsoft.com/global/en/search?q=engineer", ("microsoft", "United States")),
        ("https://apply.careers.microsoft.com/careers?domain=microsoft.com", ("microsoft", "United States")),
        ("https://www.google.com/about/careers/applications/jobs/results/?location=United%20States",
         ("google", "location=United States")),
        ("https://careers.google.com/jobs/results/", ("google", "location=United States")),
        ("https://jobs.apple.com/en-us/search?location=united-states-USA", ("apple", "postLocation-USA")),
        # Not boards
        ("https://www.metacareers.com/jobs", None),
        ("https://stripe.com/jobs", None),
        ("https://www.google.com/search?q=jobs", None),
        ("https://boards.greenhouse.io/", None),
        ("https://www.myworkdayjobs.com/", None),
        ("not a url", None),
        ("", None),
    ],
)
def test_detect_board(url, expected):
    assert detect_board(url) == expected


def test_detect_board_accepts_bare_hosts():
    assert detect_board("jobs.lever.co/netflix") == ("lever", "netflix")
    assert detect_board("careers-peraton.icims.com") == ("icims", "peraton")


def test_every_detected_ats_is_registered():
    urls = [
        "https://boards.greenhouse.io/a", "https://jobs.lever.co/a", "https://jobs.ashbyhq.com/a",
        "https://a.wd1.myworkdayjobs.com/s", "https://jobs.smartrecruiters.com/A", "https://apply.workable.com/a",
        "https://careers-a.icims.com", "https://a.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1",
        "https://a.eightfold.ai", "https://amazon.jobs", "https://careers.microsoft.com",
        "https://careers.google.com", "https://jobs.apple.com",
    ]
    for url in urls:
        ats, _ = detect_board(url)
        assert ats in ADAPTERS, url


def test_detect_in_html_finds_embedded_boards():
    # Greenhouse's embed script
    page = '<div id="grnhse_app"></div><script src="https://boards.greenhouse.io/embed/job_board/js?for=stripe"></script>'
    assert detect_in_html(page, "https://stripe.com/jobs") == ("greenhouse", "stripe")
    # An iframe
    page = '<iframe src="https://careers-peraton.icims.com/jobs/search?in_iframe=1"></iframe>'
    assert detect_in_html(page, "https://www.peraton.com/careers") == ("icims", "peraton")
    # Links: the most common board wins over a stray one
    page = (
        '<a href="https://jobs.lever.co/acme/1">a</a><a href="https://jobs.lever.co/acme/2">b</a>'
        '<a href="https://boards.greenhouse.io/partner">partner</a>'
    )
    assert detect_in_html(page, "https://acme.com/careers") == ("lever", "acme")
    # A JSON-escaped URL inside a script
    page = '<script>window.__cfg={"jobsUrl":"https:\\/\\/acme.wd5.myworkdayjobs.com\\/AcmeCareers"}</script>'
    assert detect_in_html(page, "https://acme.com/careers") == ("workday", "acme|wd5|AcmeCareers")
    # A SuccessFactors RMK site is recognised by its CDN
    page = '<link href="https://rmkcdn.successfactors.com/bcfdbc8a/a8d83cb6.css" rel="stylesheet">'
    assert detect_in_html(page, "https://careers.ey.com/") == ("successfactors", "careers.ey.com")
    assert detect_in_html("<html>No jobs here</html>", "https://acme.com/") is None


@pytest.mark.asyncio
@respx.mock
async def test_detect_from_page_fetches_and_follows_redirects():
    respx.get("https://stripe.com/jobs").mock(return_value=httpx.Response(
        200, html='<script src="https://boards.greenhouse.io/embed/job_board/js?for=stripe"></script>'
    ))
    respx.get("https://acme.com/careers").mock(return_value=httpx.Response(
        302, headers={"Location": "https://jobs.ashbyhq.com/acme"}
    ))
    respx.get("https://jobs.ashbyhq.com/acme").mock(return_value=httpx.Response(200, html="<html></html>"))
    respx.get("https://broken.example/careers").mock(side_effect=httpx.ConnectError("boom"))
    respx.get("https://gone.example/careers").mock(return_value=httpx.Response(404, html="nope"))
    async with httpx.AsyncClient() as client:
        assert await detect_from_page(client, "https://stripe.com/jobs") == ("greenhouse", "stripe")
        assert await detect_from_page(client, "https://acme.com/careers") == ("ashby", "acme")
        assert await detect_from_page(client, "https://broken.example/careers") is None
        assert await detect_from_page(client, "https://gone.example/careers") is None
        # A board URL is answered without any request.
        assert await detect_from_page(client, "https://jobs.lever.co/acme") == ("lever", "acme")
    assert not any(c.request.url.host == "jobs.lever.co" for c in respx.calls)
