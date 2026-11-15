"""Place parsing for the location filters and the US check (app/pipeline/locations.py, filters.is_us_location)."""

import pytest

from app.pipeline.locations import parse_locations


@pytest.mark.parametrize("text, place, us", [
    ("Hyderabad, IN", ("Hyderabad", None, "India"), False),       # SuccessFactors' "City, country code"
    ("Bengaluru, IN", ("Bangalore", None, "India"), False),
    ("Remote - Hyderabad, IN", ("Hyderabad", None, "India"), False),
    ("Toronto, CA", ("Toronto", "ON", "Canada"), False),
    ("Berlin, DE", ("Berlin", None, "Germany"), False),
    ("Tel Aviv, IL", ("Tel Aviv", None, "Israel"), False),
    ("Indianapolis, IN", ("Indianapolis", "IN", "United States"), True),   # the US states keep their cities
    ("San Jose, CA", ("San Jose", "CA", "United States"), True),
    ("Wilmington, DE", ("Wilmington", "DE", "United States"), True),
    ("Chicago, IL", ("Chicago", "IL", "United States"), True),
])
def test_state_codes_that_are_also_country_codes(text, place, us):
    from app.pipeline.filters import is_us_location

    assert (lambda p: (p.city, p.state, p.country))(parse_locations(text)[0]) == place
    assert is_us_location(text, "remote" in text.lower()) is us


def test_a_job_in_india_and_the_us_still_counts_as_us():
    from app.pipeline.filters import is_us_location

    assert is_us_location("Hyderabad, IN | Austin, TX", False)


@pytest.mark.parametrize("text, place, us", [
    ("Bangalore, CA", ("Bangalore", None, "India"), False),        # a never-US city, whatever code follows
    ("Montreal, QC, CA", ("Montreal", "QC", "Canada"), False),      # CA after a province is Canada
    ("Montreal - QC - CA", ("Montreal", "QC", "Canada"), False),
    ("Toronto, Ontario, CA", ("Toronto", "ON", "Canada"), False),
    ("Vancouver, WA", ("Vancouver", "WA", "United States"), True),  # US namesakes stay US
    ("Berlin, NH", ("Berlin", "NH", "United States"), True),
    ("Salem, OR", ("Salem", "OR", "United States"), True),
    ("Sacramento, CA", ("Sacramento", "CA", "United States"), True),
])
def test_foreign_cities_with_other_codes_and_canadian_provinces(text, place, us):
    from app.pipeline.filters import is_us_location

    places = parse_locations(text)
    assert [(p.city, p.state, p.country) for p in places] == [place]  # no stray city called "CA"
    assert is_us_location(text, False) is us


def test_ontario_california_is_still_us():
    from app.pipeline.filters import is_us_location

    assert is_us_location("Ontario, CA", False) and not is_us_location("Toronto, Ontario, CA", False)


@pytest.mark.parametrize("text,places", [
    ("Remote in USA", [(None, None, "United States")]),  # was Indiana: "in USA" read as IN
    ("Remote - IN", [(None, "IN", "United States")]),
    ("(USA) SC ROCK HILL 06236 SAM'S CLUB", [("Rock Hill", "SC", "United States")]),  # Walmart's stores
    ("(USA) CA PERRIS 08799 ECOMM WHSE LOGISTICS", [("Perris", "CA", "United States")]),
])
def test_remote_in_usa_and_walmart_store_places(text, places):
    assert [(p.city, p.state, p.country) for p in parse_locations(text)] == places


@pytest.mark.parametrize("text,us", [
    # Abroad, though a part looks like a US state code or name (found in the US feed, 2026-11-15).
    ("Bangalore, KA, IN", False), ("Chennai, Chennai-MRV, IN", False), ("Hyderabad, TG, IN", False),
    ("Madrid, MD, Spain", False), ("Cork, CO, Ireland", False), ("Lagos, LA, Nigeria", False),
    ("Rozenburg, NH, Netherlands", False), ("Washington, England, United Kingdom", False),
    ("Remote - Colombia", False), ("Saudi Arabia - Remote", False), ("Latin America", False),
    ("Mexico - Remote | Colombia - Remote", False), ("Cambridge, Ontario", False), ("Remote (EMEA)", False),
    # US, including US places named like foreign ones.
    ("LATAM (Remote), US (Remote)", True), ("United States or Ontario, Canada", True), ("Remote in USA", True),
    ("Dublin, OH", True), ("Melbourne, Florida, US", True), ("Deming, New Mexico", True), ("Paris, TX", True),
    ("Ontario, CA", True), ("Ontario, California", True), ("Remote - North America", True),
    ("New York, NY | London, UK", True), ("(USA) OH ONTARIO 06407 SAM'S CLUB", True), ("Remote", True),
    ("Canada (Remote), United States (Remote), United Kingdom (Remote)", True), ("Remote (US, Canada & UK)", True),
    ("NY-New York, TX-Frisco, DE-Greenville", True),  # state-first: DE is Delaware here
    ("Paris, TX 75460", True), ("Dublin, OH 0755", True), ("New Brunswick, NJ 08903", True), ("IL-Chicago", True),
    ("IL-Tel Aviv", False), ("Amsterdam, NH, Netherlands", False), ("Perth, WA, Australia", False),
    ("DE-Munich-MSO", False), ("Remote, South Korea", False), ("Ontario - Remote", False), ("Remote-EU", False),
])
def test_us_feed_keeps_only_us_places(text, us):
    from app.pipeline.filters import is_us_location

    assert is_us_location(text, False) is us
