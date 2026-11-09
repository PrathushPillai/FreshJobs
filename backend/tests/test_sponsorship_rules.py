"""Visa and experience rules on real posting text, from two hand-read samples of For you jobs (2026-11-09).

The first 100 jobs tuned the rules: no-sponsorship went from 84/96 to 96/96 right, catching all 45 jobs that
rule an F-1/H-1B candidate out (33 before), and years of experience from 43/53 to 50/53. A second, unseen
sample then checked them: 30 of 30 newly flagged jobs were real exclusions, 15 of 15 "says it sponsors" were
real, and misses among unflagged jobs that mention visas fell from about 27% to about 11% (whose phrasings are
below too). Every phrase here comes from a real posting; keep them passing when the rules change.
"""

import pytest

from app.pipeline.normalize import experience_years
from app.pipeline.sponsorship import find_no_sponsor_phrase, says_sponsors


@pytest.mark.parametrize("text", [
    "This position is not eligible for Intel immigration sponsorship Minimum Qualifications Bachelor's degree",
    "***This position is not eligible for Qualcomm immigration sponsorship***",
    "Schedule: Full-time, exempt. Sponsorship not available. Position only available to those with legal authorization.",
    "The company will not consider candidates who require sponsorship for a work-authorized visa, now or in the future.",
    "PwC does not intend to hire experienced or entry level job seekers who will need, now or in the future, PwC sponsorship",
    "For US based roles only, please note the Company may not be able to employ candidates for this role who have "
    "United States work authorization related to certain U.S. visa categories, or support future H-1B sponsorship at this time.",
    "must be a U.S. citizen or national, (ii) U.S. lawful, permanent resident (aka green card holder), (iii) Refugee",
    "Applicants must be a U.S. Citizen, U.S. National, lawful permanent resident, refugee, or asylee.",
    "U.S. Person status is required as this position will require the ability to access U.S.-only data system",
    "US person status required, with eligibility to obtain and maintain a US security clearance.",
    "Eligible to obtain and maintain an active U.S. Secret security clearance",
    "Requires U.S. citizenship and residence in approved hiring hub: DC/Maryland/Virginia",
    "Candidates must be legally authorized to work in the United States on a permanent basis.",
    "This role is not open for work sponsorship at this time.",
    "WSI will not now or in the future commence an immigration case or \"sponsor\" an individual for this position",
    'This role requires access to export-controlled information or items that require "U.S. persons"',
    "Please note that this position is not eligible for visa sponsorship.",
])
def test_rules_out_sponsorship(text):
    assert find_no_sponsor_phrase(text), text


@pytest.mark.parametrize("text", [
    # EEO boilerplate and look-alikes: not exclusions
    "Applicants are considered without regard to race, color, religion, national origin, age, citizenship status, or veteran status.",
    "WHOOP is an Equal Opportunity Employer and participates in E-verify to determine employment eligibility.",
    "If access to export-controlled technology is required, it is within Employer's discretion whether to apply for a U.S. export license.",
    "If you are a national of one of the listed countries, an export license may be required as a condition of your employment.",
    "Experience building user authentication and authorization services using JWT and cookies.",
    "This position is eligible for company sponsored benefits, including medical, dental and vision insurance.",
    "Authorized to work in the US.",
    # A posting that says "permanent basis" but also says it sponsors is mixed: shown with "Says it sponsors"
    "Candidates must be legally authorized to work in the United States on a permanent basis. "
    "Visa sponsorship is available for this position.",
])
def test_does_not_rule_out(text):
    assert find_no_sponsor_phrase(text) is None, text


@pytest.mark.parametrize("text", [
    "Visa sponsorship: We do sponsor visas! However, we aren't able to successfully sponsor visas for every role.",
    "While Etsy supports visa sponsorship, sponsorship opportunities may be limited to certain roles and skills.",
    "Visa sponsorship is available for this position.",
    "We are open to sponsoring international visas.",
    "We sponsor visas for in-person employees, and can hire remotely in most countries.",
    "Visa sponsorships are available.",
    "Is role eligible for Immigration Sponsorship?: Yes",
    "• Visa transfers are supported.",
    "- Visa Sponsorship & Immigration Support",
])
def test_says_it_sponsors(text):
    assert says_sponsors(text), text


@pytest.mark.parametrize("text", [
    "We do not sponsor visas for this role.",
    "This position is eligible for company sponsored benefits and a company-sponsored 401(k).",
    "We sponsor hackathons and community events.",
    "While we sponsor visas for some roles, this position is not eligible for visa sponsorship.",  # a "no" anywhere wins
])
def test_does_not_say_it_sponsors(text):
    assert not says_sponsors(text), text


@pytest.mark.parametrize("text,years", [
    # Alternatives by degree: the Bachelor's line, not the largest number (Northrop, Walmart, Intuitive, Voyager)
    ("Basic Qualifications: A high school diploma with 6 years of relevant experience; OR a Bachelor's degree with 2 years of "
     "relevant experience; OR a Master's degree with 0 years of relevant experience", 2),
    ("Master's degree or equivalent in computer science and 1 year of experience in software engineering. "
     "OR Bachelor's degree or equivalent in computer science and 4 years of experience in software engineering", 4),
    ("12 years of experience and a Bachelor's degree in computer science, or 8 years of experience and a Master's degree, "
     "or 5 years of experience and a PhD degree; or equivalent experience", 12),
    ("BS in Computer Science with 5+ years of professional software development experience\n"
     "MS in Computer Science with 3+ years of professional software development experience", 5),
    # Years in lieu of a degree don't apply to someone who has one (SpaceX)
    ("BASIC QUALIFICATIONS: Bachelor's degree in computer science OR 7+ years of professional experience in software "
     "development in lieu of a degree. 3+ years of professional experience in full stack development", 3),
    # No word "experience" right after (OpenAI, Hex, Eight Sleep, Arista) and a curly apostrophe (Guidewire)
    ("You'll thrive in this role if you: - Have 8+ years of technical consulting (or equivalent) experience", 8),
    ("ABOUT YOU - 5+ years building and operating production infrastructure, including on-call", 5),
    ("WHAT YOU'LL NEED TO SUCCEED - 6+ years of shipping high-quality features for native iOS apps", 6),
    ("8+ years’ experience in software engineering roles focused on data platforms", 8),
    ("3+ years fluency in C or C++. Python experience an added bonus.", 3),
    # Company history isn't a requirement
    ("Before founding Sierra, Clay spent 18 years at Google. Nearly 30 years later, that question still fuels us.", None),
    ("We have served customers for over 25+ years.", None),
])
def test_years_of_experience(text, years):
    assert experience_years(text) == years


def test_reapplying_rules_gives_what_new_jobs_get(session, now):
    """visa_signals.derive (used to re-read saved jobs) must match apply_flags (used for new jobs)."""
    from app.models import Company, Job
    from app.pipeline.poller import process_company
    from app.pipeline.visa_signals import FIELDS, derive
    from app.prefs import get_prefs
    from app.sources import NormalizedJob

    company = Company(name="Acme", ats="greenhouse", slug="acme")
    session.add(company)
    session.flush()
    texts = ["Visa sponsorship: We do sponsor visas! 5+ years building backend systems in Python and Go.",
             "This position is not eligible for Acme immigration sponsorship. Bachelor's degree with 2 years of experience.",
             "Must be a U.S. citizen or national, (ii) U.S. lawful, permanent resident. OR 7+ years in lieu of a degree.",
             "We value curiosity. Experience with SQL and dashboards preferred, and a friendly attitude."]
    jobs = [NormalizedJob(external_id=str(i), title=t, locations=["Austin, TX"], url=f"https://x/{i}", description=d)
            for i, (t, d) in enumerate(zip(["Senior Data Engineer", "Data Analyst", "Software Engineer", "Analyst"], texts))]
    process_company(session, company, jobs, get_prefs(session), now)
    session.commit()
    for job in session.query(Job).all():
        assert derive(job.description, job.seniority) == {f: getattr(job, f) for f in FIELDS}, job.title
