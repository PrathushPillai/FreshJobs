import re

DEFAULT_INCLUDE = [
    "software engineer", "software developer", "swe", "backend", "back end", "frontend", "front end",
    "full stack", "fullstack", "data scientist", "data science", "data engineer", "data analyst",
    "machine learning", "ml engineer", "ai engineer", "applied scientist", "research engineer",
    "analytics engineer", "business intelligence", "platform engineer", "infrastructure engineer",
    "devops", "site reliability", "sre", "cloud engineer", "mobile engineer", "ios engineer",
    "android engineer", "new grad", "computer vision", "nlp",
]
DEFAULT_EXCLUDE = ["intern", "internship", "co-op", "contract", "sales", "recruiter", "account executive"]

US_STATES = {
    "AL": "alabama", "AK": "alaska", "AZ": "arizona", "AR": "arkansas", "CA": "california", "CO": "colorado",
    "CT": "connecticut", "DE": "delaware", "FL": "florida", "GA": "georgia", "HI": "hawaii", "ID": "idaho",
    "IL": "illinois", "IN": "indiana", "IA": "iowa", "KS": "kansas", "KY": "kentucky", "LA": "louisiana",
    "ME": "maine", "MD": "maryland", "MA": "massachusetts", "MI": "michigan", "MN": "minnesota",
    "MS": "mississippi", "MO": "missouri", "MT": "montana", "NE": "nebraska", "NV": "nevada",
    "NH": "new hampshire", "NJ": "new jersey", "NM": "new mexico", "NY": "new york", "NC": "north carolina",
    "ND": "north dakota", "OH": "ohio", "OK": "oklahoma", "OR": "oregon", "PA": "pennsylvania",
    "RI": "rhode island", "SC": "south carolina", "SD": "south dakota", "TN": "tennessee", "TX": "texas",
    "UT": "utah", "VT": "vermont", "VA": "virginia", "WA": "washington", "WV": "west virginia",
    "WI": "wisconsin", "WY": "wyoming", "DC": "district of columbia",
}
US_CITIES = [
    "san francisco", "new york", "nyc", "seattle", "boston", "austin", "chicago", "los angeles",
    "san jose", "mountain view", "palo alto", "menlo park", "sunnyvale", "redwood city", "san mateo",
    "denver", "atlanta", "miami", "washington", "dallas", "houston", "san diego", "pittsburgh",
    "philadelphia", "portland", "salt lake city", "raleigh", "bay area", "silicon valley", "cambridge",
]
_US_WORDS = re.compile(r"\b(united states|usa|u\.s\.a?\.?|us|america)\b", re.I)
_STATE_CODE = re.compile(r",\s*(" + "|".join(US_STATES) + r")\b")
_STATE_NAME = re.compile(r"\b(" + "|".join(US_STATES.values()) + r")\b", re.I)
_CITY = re.compile(r"\b(" + "|".join(US_CITIES) + r")\b", re.I)
_NON_US = re.compile(
    r"\b(canada|toronto|vancouver|montreal|uk|united kingdom|london|ireland|dublin|germany|berlin|munich|"
    r"france|paris|netherlands|amsterdam|spain|madrid|barcelona|poland|warsaw|india|bangalore|bengaluru|"
    r"hyderabad|pune|singapore|japan|tokyo|australia|sydney|melbourne|brazil|sao paulo|mexico|"
    r"argentina|israel|tel aviv|emea|apac|latam|europe|portugal|lisbon|sweden|stockholm|switzerland|zurich)\b",
    re.I,
)


def is_us_location(location: str, remote: bool) -> bool:
    """True if at least one of the job's locations is in the US (or remote without a non-US region)."""
    if not location:
        return remote
    for part in re.split(r"\s*\|\s*|;", location):
        if _US_WORDS.search(part) or _STATE_CODE.search(part) or _STATE_NAME.search(part) or _CITY.search(part):
            return True
        if "remote" in part.lower() and not _NON_US.search(part):
            return True
        # "Washington" alone could be the state; "New Mexico" contains "mexico" — both handled above.
    return False


def _contains(title: str, keyword: str) -> bool:
    return re.search(r"(?<![a-z])" + re.escape(keyword.lower()) + r"(?![a-z])", title) is not None


def role_matches(title: str, include: list[str], exclude: list[str]) -> bool:
    t = title.lower()
    if any(_contains(t, k) for k in exclude):
        return False
    return any(_contains(t, k) for k in include)
