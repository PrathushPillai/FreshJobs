from urllib.parse import parse_qs, urlparse

from app.recruiters import core_title, hiring_posts_url, search_links, short_company


def test_core_title_drops_team_and_place():
    assert core_title("Data Engineer, Payments (Remote)") == "Data Engineer"
    assert core_title("Software Engineer - Agent Auth") == "Software Engineer"
    assert core_title("Urgent Need : Data Analyst : Alpharetta, GA - Hybrid") == "Data Analyst"
    assert core_title("Summer 2027 (Software Engineering Intern)") == "Software Engineering Intern"


def test_short_company_drops_legal_suffixes():
    assert short_company("Navtech, Inc.") == "Navtech"
    assert short_company("Acme Robotics LLC") == "Acme Robotics"
    assert short_company("Stripe") == "Stripe"


def test_hiring_posts_url_is_a_past_month_linkedin_post_search():
    url = hiring_posts_url("Stripe", "Data Engineer, Payments (Remote)")
    parsed = urlparse(url)
    assert parsed.netloc == "www.linkedin.com" and parsed.path == "/search/results/content/"
    q = parse_qs(parsed.query)
    assert q["keywords"] == ['hiring "Data Engineer" "Stripe"']
    assert q["datePosted"] == ['"past-month"'] and q["sortBy"] == ['"date_posted"']
    # The recruiter finder's LinkedIn posts link is the same search.
    assert url in [link["url"] for link in search_links("Stripe", "Data Engineer, Payments (Remote)")]
