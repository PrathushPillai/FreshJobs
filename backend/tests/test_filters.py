import pytest

from app.pipeline.filters import DEFAULT_EXCLUDE, DEFAULT_INCLUDE, is_us_location, role_matches
from app.pipeline.normalize import normalize_title, seniority
from app.pipeline.sponsorship import find_no_sponsor_phrase


@pytest.mark.parametrize(
    "location,remote,expected",
    [
        ("New York, NY", False, True),
        ("San Francisco, California", False, True),
        ("Remote - US", True, True),
        ("Remote", True, True),
        ("United States", False, True),
        ("London, UK", False, False),
        ("Remote - Canada", True, False),
        ("Toronto | Seattle, WA", False, True),
        ("Bengaluru, India", False, False),
        ("", False, False),
    ],
)
def test_us_location(location, remote, expected):
    assert is_us_location(location, remote) is expected


@pytest.mark.parametrize(
    "title,expected",
    [
        ("Software Engineer, New Grad", True),
        ("Machine Learning Engineer", True),
        ("Data Analyst II", True),
        ("Software Engineering Intern", False),
        ("Account Executive", False),
        ("Office Manager", False),
        ("Backend Engineer (Go)", True),
    ],
)
def test_role_matches(title, expected):
    assert role_matches(title, DEFAULT_INCLUDE, DEFAULT_EXCLUDE) is expected


@pytest.mark.parametrize(
    "title,expected",
    [
        ("Senior Software Engineer", "senior"),
        ("Sr. Data Scientist", "senior"),
        ("Staff ML Engineer", "senior"),
        ("Software Engineer I", "entry"),
        ("New Grad Software Engineer", "entry"),
        ("Software Engineer II", "mid"),
        ("Software Engineer", "unknown"),
        ("Product Manager", "mid"),
        ("Associate Product Manager", "entry"),
        ("Engineering Manager, Payments", "senior"),
        ("Senior Product Manager", "senior"),
    ],
)
def test_seniority(title, expected):
    assert seniority(title) == expected


@pytest.mark.parametrize(
    "text",
    [
        "We are unable to sponsor visas for this role.",
        "This company will not provide visa sponsorship.",
        "Candidates must be authorized to work without the need for current or future sponsorship.",
        "Must be a U.S. citizen.",
        "Requires an active Secret security clearance.",
        "No visa sponsorship is available.",
        "We do not offer H-1B sponsorship at this time.",
    ],
)
def test_no_sponsor_detected(text):
    assert find_no_sponsor_phrase(text)


@pytest.mark.parametrize(
    "text",
    [
        "We sponsor visas and support H-1B transfers.",
        "Visa sponsorship is available for this role.",
        "Build distributed systems with great people.",
    ],
)
def test_no_sponsor_not_detected(text):
    assert find_no_sponsor_phrase(text) is None


def test_normalize_title_strips_noise():
    assert normalize_title("Software Engineer (Remote) [R12345]") == "software engineer"
    assert normalize_title("Software Engineer - REQ-991") == normalize_title("Software Engineer")


from app.pipeline.locations import Place, parse_locations  # noqa: E402
from app.pipeline.normalize import experience_level, experience_years  # noqa: E402


@pytest.mark.parametrize(
    "location,expected",
    [
        ("New York, NY", [Place("New York", "NY", "United States")]),
        ("Hybrid - San Francisco", [Place("San Francisco", "CA", "United States")]),
        ("Remote - US", [Place(None, None, "United States")]),
        ("Remote", []),
        ("Toronto, ON", [Place("Toronto", "ON", "Canada")]),
        ("US-CA-Menlo Park | US-NY-New York", [Place("Menlo Park", "CA", "United States"), Place("New York", "NY", "United States")]),
        ("NYC, Chicago", [Place("New York", "NY", "United States"), Place("Chicago", "IL", "United States")]),
        ("Fort Worth TX", [Place("Fort Worth", "TX", "United States")]),
        ("Bengaluru, India", [Place("Bangalore", None, "India")]),
        ("Remote - Washington", [Place(None, "WA", "United States")]),
        ("Washington D.C.", [Place("Washington", "DC", "United States")]),
    ],
)
def test_parse_locations(location, expected):
    assert parse_locations(location) == expected


@pytest.mark.parametrize(
    "text,years",
    [
        ("3+ years of experience in Python", 3),
        ("Minimum of 5 years of professional software engineering experience", 5),
        ("2-4 years experience", 2),
        ("5+ years overall and 2+ years of Spark experience", 5),
        ("We've been around 15 years.", None),
    ],
)
def test_experience_years(text, years):
    assert experience_years(text) == years


def test_experience_level_takes_higher_of_title_and_years():
    assert experience_level(3, "senior") == "senior"
    assert experience_level(1, "unknown") == "entry"
    assert experience_level(7, "entry") == "senior"
    assert experience_level(None, "unknown") == "unknown"


def test_listed_at_prefers_detection_time_for_date_only_postings():
    from datetime import datetime, timezone

    from app.pipeline.normalize import listed_at

    found = datetime(2026, 11, 3, 14, 5, tzinfo=timezone.utc)
    midnight = datetime(2026, 11, 3, tzinfo=timezone.utc)
    exact = datetime(2026, 11, 3, 9, 30, tzinfo=timezone.utc)
    assert listed_at(midnight, found, baseline=False) == found  # "Posted Today" caught at 2:05 pm
    assert listed_at(exact, found, baseline=False) == exact  # an aggregator's exact time is kept
    assert listed_at(midnight, found, baseline=True) == midnight  # baseline: the date is all we know
    assert listed_at(None, found, baseline=False) == found
    assert listed_at(None, found, baseline=True) is None  # undated and already listed: unknown
    old_date = datetime(2026, 10, 1, tzinfo=timezone.utc)
    assert listed_at(old_date, found, baseline=False) == old_date  # re-listed old job keeps its date


def test_listed_window_after_a_gap_in_checking():
    from datetime import datetime, timezone

    from app.pipeline.normalize import listed_window

    prev = datetime(2026, 11, 3, 8, 0, tzinfo=timezone.utc)
    found = datetime(2026, 11, 3, 14, 5, tzinfo=timezone.utc)
    midnight = datetime(2026, 11, 3, tzinfo=timezone.utc)
    exact = datetime(2026, 11, 3, 9, 30, tzinfo=timezone.utc)
    assert listed_window(midnight, found, False, prev) == (prev, found)  # date-only: sometime since 8 am
    assert listed_window(None, found, False, prev) == (prev, found)
    assert listed_window(exact, found, False, prev) == (exact, None)  # an exact time needs no window
    late_prev = datetime(2026, 11, 2, 20, 0, tzinfo=timezone.utc)
    assert listed_window(midnight, found, False, late_prev) == (midnight, found)  # not before the posted date
    assert listed_window(None, found, True, prev) == (None, None)  # baseline stays unknown
