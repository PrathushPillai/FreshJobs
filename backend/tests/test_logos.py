import pytest

from app.logos import logo_domain


@pytest.mark.parametrize("url, ats, slug, company, expected", [
    ("https://careers.mayoclinic.org/job/1", "website", "x", "Mayo Clinic Health", "mayoclinic.org"),
    ("https://jobs.apple.com/en-us/details/1", "apple", "apple", "Apple", "apple.com"),
    ("https://www.amazon.jobs/en/jobs/1", "amazon", "amazon", "Amazon", "amazon.com"),
    ("https://job-boards.greenhouse.io/stripe/jobs/1", "greenhouse", "stripe", "Stripe", "stripe.com"),
    ("https://jobs.lever.co/acmerobotics/1", "lever", "acmerobotics", "Acme Robotics, Inc.", "acmerobotics.com"),
    ("https://nvidia.wd5.myworkdayjobs.com/x/job/1", "workday", "nvidia|wd5|NVIDIAExternalCareerSite", "NVIDIA", "nvidia.com"),
    ("https://dollartree.wd5.myworkdayjobs.com/x/job/1", "workday", "dollartree|wd5|dollartreeus", "Dollar Tree Stores", "dollartree.com"),
    # A board name that isn't the employer's: no guess (it would show another site's logo).
    ("https://myview.wd3.myworkdayjobs.com/x/job/1", "workday", "myview|wd3|paradox", "Loblaw Companies", None),
    ("https://jobs.lever.co/xyz/1", "lever", "xyz", "Totally Different Corp", None),
    ("https://jobs.lever.co/acme/1", "lever", "acme", "Acme Robotics", None),  # acme.com is someone else
    # Aggregator links on a job board, unknown employer: nothing.
    ("https://apply.workable.com/j/ABC", "workable_search", "us", "Small Bakery LLC", None),
    ("https://acme.applytojob.com/apply/1", "jazzhr", "all", "Acme Plumbing", None),
    ("https://careers.example.co.uk/1", "website", "x", "Example", "example.co.uk"),
    ("https://x.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/en/sites/StaplesInc/job/1", "oracle_hcm",
     "x.fa.ocs.oraclecloud.com|StaplesInc", "Staples", "staples.com"),
    ("https://jobs.smartrecruiters.com/PilotCompany/1", "smartrecruiters", "PilotCompany", "Pilot Company", "pilotcompany.com"),
])
def test_logo_domain(url, ats, slug, company, expected):
    assert logo_domain(url, ats, slug, company) == expected
