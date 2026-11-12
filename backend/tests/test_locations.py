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
