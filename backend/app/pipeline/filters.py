import re
from functools import lru_cache

from app.pipeline.search import query_variants

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
# "City, XX" where XX is both a US state and a country's code: SuccessFactors and others write "Hyderabad, IN"
# (India), and every such job counted as Indiana, so Indian, Canadian, German and Israeli jobs showed in the US
# feed (2026-11-12, "Consultant - ServiceNow Reporting", Yash Technologies, Hyderabad, IN). Only well-known cities
# of those countries are listed, and none that also exists in that US state (Ontario, California is left out).
FOREIGN_CITIES = {
    "IN": ("India", {
        "hyderabad", "secunderabad", "bangalore", "bengaluru", "pune", "chennai", "mumbai", "navi mumbai", "thane",
        "delhi", "new delhi", "gurgaon", "gurugram", "noida", "greater noida", "kolkata", "ahmedabad", "kochi",
        "cochin", "coimbatore", "trivandrum", "thiruvananthapuram", "mohali", "chandigarh", "jaipur", "indore",
        "nagpur", "vadodara", "visakhapatnam", "vizag", "mysore", "mysuru", "bhubaneswar", "lucknow", "mangalore",
        "mangaluru", "madurai", "nashik", "surat", "goa", "panaji", "vijayawada", "warangal", "hubli", "dehradun",
        "bhopal", "raipur", "ranchi", "patna", "guwahati", "gandhinagar", "vellore", "tiruchirappalli", "trichy",
        "faridabad", "ghaziabad", "manesar", "hinjewadi", "whitefield", "gachibowli", "madhapur", "kharadi",
        "aurangabad", "rajkot", "kozhikode", "calicut", "thrissur", "nellore", "guntur", "kakinada", "salem",
    }),
    "CA": ("Canada", {
        "toronto", "vancouver", "montreal", "montréal", "ottawa", "calgary", "edmonton", "waterloo", "kitchener",
        "mississauga", "markham", "winnipeg", "halifax", "quebec city", "québec", "burnaby", "brampton",
        "saskatoon", "regina", "oakville", "richmond hill", "vaughan", "laval", "gatineau", "guelph", "kanata",
    }),
    "DE": ("Germany", {
        "berlin", "munich", "münchen", "muenchen", "frankfurt", "frankfurt am main", "hamburg", "cologne", "köln",
        "stuttgart", "düsseldorf", "dusseldorf", "leipzig", "dresden", "nuremberg", "nürnberg", "hannover",
        "heidelberg", "karlsruhe", "bonn", "mannheim", "walldorf", "darmstadt", "aachen", "potsdam",
    }),
    "IL": ("Israel", {
        "tel aviv", "tel aviv-yafo", "jerusalem", "haifa", "herzliya", "petah tikva", "petach tikva", "ra'anana",
        "raanana", "netanya", "beer sheva", "be'er sheva", "rehovot", "yokneam", "caesarea", "kfar saba",
        "hod hasharon", "rosh haayin", "ramat gan", "airport city", "modiin", "or yehuda",
    }),
    "CO": ("Colombia", {"bogota", "bogotá", "medellin", "medellín", "cali", "barranquilla", "cartagena"}),
    "AR": ("Argentina", {"buenos aires", "cordoba", "córdoba", "rosario", "mendoza"}),
    "MA": ("Morocco", {"casablanca", "rabat", "marrakech", "tangier", "fes"}),
    "ID": ("Indonesia", {"jakarta", "bandung", "surabaya", "denpasar", "bali"}),
    "PA": ("Panama", {"panama city", "ciudad de panamá"}),
}


# Cities that are never a US place whatever state code follows them: "Bangalore, CA" (478 jobs) is India. Only
# cities with no US namesake of note (not Vancouver, WA; Berlin, NH; Salem, OR; Delhi, NY; Ottawa, IL).
NEVER_US = {city: country for country, cities in (
    ("India", ("hyderabad", "secunderabad", "bangalore", "bengaluru", "pune", "chennai", "mumbai", "navi mumbai",
               "gurgaon", "gurugram", "noida", "greater noida", "kolkata", "kochi", "cochin", "coimbatore",
               "trivandrum", "thiruvananthapuram", "mohali", "ahmedabad", "visakhapatnam", "bhubaneswar", "mysuru",
               "hinjewadi", "whitefield", "gachibowli", "madhapur")),
    ("Canada", ("toronto", "montreal", "montréal", "mississauga", "brampton")),
    ("Israel", ("tel aviv", "tel aviv-yafo", "herzliya", "petah tikva", "petach tikva", "ra'anana", "raanana")),
    ("Germany", ("munich", "münchen", "frankfurt am main", "düsseldorf")),
    ("Colombia", ("bogotá", "medellín", "medellin")),
    ("Argentina", ("buenos aires",)),
    ("Indonesia", ("jakarta",)),
    ("Morocco", ("casablanca",)),
) for city in cities}


def foreign_country(city: str, code: str) -> str | None:
    """The country for "City, XX" when XX looks like a US state code but the place isn't in the US: XX is also that
    country's code and the city one of its well-known ones (FOREIGN_CITIES: "Hyderabad, IN" -> "India"), or the
    city is never a US place (NEVER_US: "Bangalore, CA" -> "India"). Else None."""
    c, code = (city or "").strip().lower(), (code or "").upper()
    entry = FOREIGN_CITIES.get(code)
    if entry and c in entry[1]:
        return entry[0]
    return NEVER_US.get(c) if code in US_STATES else None


# "Montreal, QC, CA", "Montreal - QC - CA", "Toronto, Ontario, CA": CA after a Canadian province is Canada, not
# California. A place must come first, so "Ontario, CA" (a city in California) stays US.
_PROVINCE_THEN_CA = re.compile(r"(?:,|\s[-–]\s)\s*(?:AB|BC|MB|NB|NL|NS|NT|NU|ON|PE|QC|SK|YT|alberta|british columbia|manitoba|new brunswick|newfoundland(?: and labrador)?|nova scotia|ontario|prince edward island|qu[eé]bec|saskatchewan)\s*(?:,|\s[-–]\s)\s*CA(?![A-Za-z])", re.I)
_CITY_CODE = re.compile(r"(?:^|[-–—/(]\s*)([^,\-–—/()]+?)\s*,\s*([A-Za-z]{2})\s*(?:[,)]|$)")

_US_WORDS = re.compile(r"\b(united states|usa|u\.s\.a?\.?|us|america)\b", re.I)
_STATE_CODE = re.compile(r",\s*(" + "|".join(US_STATES) + r")\b")
# "Baja California" is in Mexico, not the US state inside its name.
_STATE_NAME = re.compile(r"(?<!baja )\b(" +"|".join(US_STATES.values()) + r")\b", re.I)
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
        if (m := _CITY_CODE.search(part)) and foreign_country(m.group(1), m.group(2)) and not _US_WORDS.search(part):
            continue  # "Hyderabad, IN" is India, not Indiana
        if _PROVINCE_THEN_CA.search(part) and not _US_WORDS.search(part):
            continue
        if _US_WORDS.search(part) or _STATE_CODE.search(part) or _STATE_NAME.search(part) or _CITY.search(part):
            return True
        if "remote" in part.lower() and not _NON_US.search(part):
            return True
        # "Washington" alone could be the state; "New Mexico" contains "mexico" — both handled above.
    return False


def _contains(title: str, keyword: str) -> bool:
    return re.search(r"(?<![a-z])" + re.escape(keyword.lower()) + r"(?![a-z])", title) is not None


def _norm(text: str) -> str:
    """Lowercase, with hyphens and slashes as spaces: "Full-Stack" matches "full stack"."""
    return re.sub(r"\s+", " ", re.sub(r"[-/]", " ", text.lower())).strip()


@lru_cache(maxsize=16)
def _pattern(keywords: tuple[str, ...], variants: bool) -> re.Pattern | None:
    """One regex for a keyword list: whole words, last word singular or plural, optionally in every
    spelling of the title (search.query_variants: "frontend" = "front end", "SDE" = "software development
    engineer", developer = engineer)."""
    terms = {_norm(v) for k in keywords for v in (query_variants(k) if variants else [k]) if _norm(v)}
    if not terms:
        return None
    alternatives = [re.escape(t) + "s?" for t in sorted(terms, key=len, reverse=True)]
    return re.compile(r"(?<![a-z0-9])(?:" + "|".join(alternatives) + r")(?![a-z0-9])")


def role_matches(title: str, include: list[str], exclude: list[str]) -> bool:
    """Whether a title is one of the user's roles ("For you" and alerts).

    Exactly the roles the user picked, in any spelling of them: related roles are only suggested
    (GET /api/roles/suggestions), never added behind the user's back. Exclude keywords are literal.
    """
    t = _norm(title)
    ex = _pattern(tuple(exclude), False)
    if ex is not None and ex.search(t):
        return False
    inc = _pattern(tuple(include), True)
    return inc is not None and inc.search(t) is not None
