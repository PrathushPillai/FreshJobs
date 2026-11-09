"""Parse free-text job locations ("Hybrid - San Francisco, CA | Remote - Canada") into city / state / country."""

import re
from dataclasses import dataclass

from app.pipeline.filters import US_STATES

STATE_NAME_TO_CODE = {name: code for code, name in US_STATES.items()}

CA_PROVINCES = {
    "ON": "ontario", "BC": "british columbia", "QC": "quebec", "AB": "alberta", "MB": "manitoba",
    "NS": "nova scotia", "SK": "saskatchewan", "NB": "new brunswick", "NL": "newfoundland and labrador",
}
PROVINCE_NAME_TO_CODE = {name: code for code, name in CA_PROVINCES.items()}

# alias -> canonical country name
COUNTRIES = {
    "united states": "United States", "united states of america": "United States", "usa": "United States",
    "us": "United States", "u.s.": "United States", "u.s.a.": "United States", "america": "United States",
    "canada": "Canada", "mexico": "Mexico", "brazil": "Brazil", "argentina": "Argentina", "chile": "Chile",
    "colombia": "Colombia", "peru": "Peru", "costa rica": "Costa Rica", "uruguay": "Uruguay",
    "united kingdom": "United Kingdom", "uk": "United Kingdom", "u.k.": "United Kingdom", "england": "United Kingdom",
    "scotland": "United Kingdom", "great britain": "United Kingdom", "ireland": "Ireland", "germany": "Germany",
    "deutschland": "Germany", "france": "France", "spain": "Spain", "portugal": "Portugal", "italy": "Italy",
    "netherlands": "Netherlands", "the netherlands": "Netherlands", "belgium": "Belgium", "switzerland": "Switzerland",
    "austria": "Austria", "sweden": "Sweden", "norway": "Norway", "denmark": "Denmark", "finland": "Finland",
    "poland": "Poland", "czech republic": "Czechia", "czechia": "Czechia", "romania": "Romania", "hungary": "Hungary",
    "greece": "Greece", "ukraine": "Ukraine", "serbia": "Serbia", "bulgaria": "Bulgaria", "croatia": "Croatia",
    "estonia": "Estonia", "lithuania": "Lithuania", "latvia": "Latvia", "luxembourg": "Luxembourg",
    "turkey": "Turkey", "türkiye": "Turkey", "israel": "Israel", "united arab emirates": "United Arab Emirates",
    "uae": "United Arab Emirates", "saudi arabia": "Saudi Arabia", "qatar": "Qatar", "egypt": "Egypt",
    "south africa": "South Africa", "nigeria": "Nigeria", "kenya": "Kenya", "morocco": "Morocco",
    "india": "India", "pakistan": "Pakistan", "bangladesh": "Bangladesh", "sri lanka": "Sri Lanka",
    "singapore": "Singapore", "malaysia": "Malaysia", "indonesia": "Indonesia", "philippines": "Philippines",
    "thailand": "Thailand", "vietnam": "Vietnam", "china": "China", "hong kong": "Hong Kong", "taiwan": "Taiwan",
    "japan": "Japan", "south korea": "South Korea", "korea": "South Korea", "australia": "Australia",
    "new zealand": "New Zealand",
}

# city -> (state code or None, country). Used when a location names only a city.
CITIES = {
    # United States
    "new york": ("NY", "United States"), "new york city": ("NY", "United States"), "nyc": ("NY", "United States"),
    "brooklyn": ("NY", "United States"), "san francisco": ("CA", "United States"), "sf": ("CA", "United States"),
    "san jose": ("CA", "United States"), "mountain view": ("CA", "United States"), "palo alto": ("CA", "United States"),
    "menlo park": ("CA", "United States"), "sunnyvale": ("CA", "United States"), "santa clara": ("CA", "United States"),
    "cupertino": ("CA", "United States"), "redwood city": ("CA", "United States"), "san mateo": ("CA", "United States"),
    "foster city": ("CA", "United States"), "south san francisco": ("CA", "United States"), "oakland": ("CA", "United States"),
    "berkeley": ("CA", "United States"), "emeryville": ("CA", "United States"), "milpitas": ("CA", "United States"),
    "los angeles": ("CA", "United States"), "santa monica": ("CA", "United States"), "culver city": ("CA", "United States"),
    "irvine": ("CA", "United States"), "san diego": ("CA", "United States"), "sacramento": ("CA", "United States"),
    "seattle": ("WA", "United States"), "bellevue": ("WA", "United States"), "redmond": ("WA", "United States"),
    "kirkland": ("WA", "United States"), "boston": ("MA", "United States"), "cambridge": ("MA", "United States"),
    "somerville": ("MA", "United States"), "waltham": ("MA", "United States"), "austin": ("TX", "United States"),
    "dallas": ("TX", "United States"), "houston": ("TX", "United States"), "san antonio": ("TX", "United States"),
    "plano": ("TX", "United States"), "irving": ("TX", "United States"), "fort worth": ("TX", "United States"),
    "chicago": ("IL", "United States"), "denver": ("CO", "United States"), "boulder": ("CO", "United States"),
    "atlanta": ("GA", "United States"), "miami": ("FL", "United States"), "tampa": ("FL", "United States"),
    "orlando": ("FL", "United States"), "jacksonville": ("FL", "United States"), "washington": ("DC", "United States"),
    "washington dc": ("DC", "United States"), "washington d.c": ("DC", "United States"), "arlington": ("VA", "United States"),
    "reston": ("VA", "United States"), "mclean": ("VA", "United States"), "herndon": ("VA", "United States"),
    "philadelphia": ("PA", "United States"), "pittsburgh": ("PA", "United States"), "portland": ("OR", "United States"),
    "salt lake city": ("UT", "United States"), "lehi": ("UT", "United States"), "raleigh": ("NC", "United States"),
    "durham": ("NC", "United States"), "charlotte": ("NC", "United States"), "phoenix": ("AZ", "United States"),
    "scottsdale": ("AZ", "United States"), "tempe": ("AZ", "United States"), "minneapolis": ("MN", "United States"),
    "detroit": ("MI", "United States"), "ann arbor": ("MI", "United States"), "columbus": ("OH", "United States"),
    "cleveland": ("OH", "United States"), "cincinnati": ("OH", "United States"), "nashville": ("TN", "United States"),
    "st. louis": ("MO", "United States"), "kansas city": ("MO", "United States"), "indianapolis": ("IN", "United States"),
    "madison": ("WI", "United States"), "milwaukee": ("WI", "United States"), "las vegas": ("NV", "United States"),
    "baltimore": ("MD", "United States"), "new haven": ("CT", "United States"), "stamford": ("CT", "United States"),
    "jersey city": ("NJ", "United States"), "hoboken": ("NJ", "United States"), "princeton": ("NJ", "United States"),
    "honolulu": ("HI", "United States"), "bay area": ("CA", "United States"), "silicon valley": ("CA", "United States"),
    # Canada
    "toronto": ("ON", "Canada"), "ottawa": ("ON", "Canada"), "waterloo": ("ON", "Canada"), "vancouver": ("BC", "Canada"),
    "montreal": ("QC", "Canada"), "montréal": ("QC", "Canada"), "calgary": ("AB", "Canada"), "edmonton": ("AB", "Canada"),
    # Rest of the world
    "london": (None, "United Kingdom"), "manchester": (None, "United Kingdom"), "edinburgh": (None, "United Kingdom"),
    "dublin": (None, "Ireland"), "berlin": (None, "Germany"), "munich": (None, "Germany"), "hamburg": (None, "Germany"),
    "frankfurt": (None, "Germany"), "paris": (None, "France"), "amsterdam": (None, "Netherlands"),
    "madrid": (None, "Spain"), "barcelona": (None, "Spain"), "lisbon": (None, "Portugal"), "milan": (None, "Italy"),
    "zurich": (None, "Switzerland"), "zürich": (None, "Switzerland"), "geneva": (None, "Switzerland"),
    "stockholm": (None, "Sweden"), "copenhagen": (None, "Denmark"), "oslo": (None, "Norway"), "helsinki": (None, "Finland"),
    "warsaw": (None, "Poland"), "krakow": (None, "Poland"), "prague": (None, "Czechia"), "vienna": (None, "Austria"),
    "tel aviv": (None, "Israel"), "dubai": (None, "United Arab Emirates"), "bangalore": (None, "India"),
    "bengaluru": (None, "India"), "hyderabad": (None, "India"), "pune": (None, "India"), "mumbai": (None, "India"),
    "chennai": (None, "India"), "gurgaon": (None, "India"), "gurugram": (None, "India"), "noida": (None, "India"),
    "new delhi": (None, "India"), "delhi": (None, "India"), "tokyo": (None, "Japan"), "seoul": (None, "South Korea"),
    "beijing": (None, "China"), "shanghai": (None, "China"), "shenzhen": (None, "China"), "taipei": (None, "Taiwan"),
    "sydney": (None, "Australia"), "melbourne": (None, "Australia"), "auckland": (None, "New Zealand"),
    "sao paulo": (None, "Brazil"), "são paulo": (None, "Brazil"), "mexico city": (None, "Mexico"),
    "buenos aires": (None, "Argentina"), "bogota": (None, "Colombia"), "bogotá": (None, "Colombia"),
    "manila": (None, "Philippines"), "kuala lumpur": (None, "Malaysia"), "jakarta": (None, "Indonesia"),
}

# ISO codes seen in ATS location strings ("US-CA-Menlo Park", "Mohali, IND").
COUNTRY_CODES = {
    "US": "United States", "USA": "United States", "CA": "Canada", "CAN": "Canada", "GB": "United Kingdom",
    "GBR": "United Kingdom", "UK": "United Kingdom", "IE": "Ireland", "IRL": "Ireland", "DE": "Germany",
    "DEU": "Germany", "FR": "France", "FRA": "France", "NL": "Netherlands", "NLD": "Netherlands", "ES": "Spain",
    "ESP": "Spain", "PT": "Portugal", "PRT": "Portugal", "IT": "Italy", "ITA": "Italy", "PL": "Poland",
    "POL": "Poland", "CH": "Switzerland", "CHE": "Switzerland", "SE": "Sweden", "SWE": "Sweden", "NO": "Norway",
    "NOR": "Norway", "DK": "Denmark", "DNK": "Denmark", "FI": "Finland", "FIN": "Finland", "AT": "Austria",
    "BE": "Belgium", "CZ": "Czechia", "RO": "Romania", "IL": "Israel", "ISR": "Israel", "IN": "India",
    "IND": "India", "SG": "Singapore", "SGP": "Singapore", "JP": "Japan", "JPN": "Japan", "KR": "South Korea",
    "KOR": "South Korea", "CN": "China", "CHN": "China", "TW": "Taiwan", "AU": "Australia", "AUS": "Australia",
    "NZ": "New Zealand", "BR": "Brazil", "BRA": "Brazil", "MX": "Mexico", "MEX": "Mexico", "AR": "Argentina",
    "ARG": "Argentina", "CO": "Colombia", "COL": "Colombia", "CR": "Costa Rica", "CRI": "Costa Rica",
    "CL": "Chile", "MY": "Malaysia", "MYS": "Malaysia", "PH": "Philippines", "PHL": "Philippines",
    "AE": "United Arab Emirates", "ARE": "United Arab Emirates", "ZA": "South Africa",
}
CITY_ABBREVIATIONS = {"SF": "San Francisco", "SEA": "Seattle", "CHI": "Chicago", "NYC": "New York", "LA": "Los Angeles",
                      "ATX": "Austin", "DC": "Washington", "CDMX": "Mexico City"}

# Words that describe the work arrangement or region rather than a place.
_NOISE = re.compile(
    r"\b(remote|hybrid|on-?site|in-?office|office|flexible|friendly|anywhere|headquarters|hq|mso|virtual|"
    r"multiple locations|various locations|work from home|wfh|metro(politan)?( area)?|area|greater|region|"
    r"based|first|time zones?)\b",
    re.I,
)
_REGIONS = {"emea", "apac", "latam", "amer", "americas", "europe", "north america", "asia", "global", "worldwide",
            "east coast", "west coast", "central", "western", "eastern"}
_CODE_FORM = re.compile(r"^([A-Z]{2,3})-([A-Za-z]{2,3}-)?(.+)$")  # US-CA-Menlo Park, DE-Munich-MSO, KR-Seoul


@dataclass(frozen=True)
class Place:
    city: str | None = None
    state: str | None = None
    country: str | None = None


def _clean(token: str) -> str:
    token = token.replace("(", " ").replace(")", " ")
    token = _NOISE.sub(" ", token)
    return re.sub(r"\s+", " ", token).strip(" .:-–—/&").strip()


def _canonical_city(city: str) -> str:
    if city.upper() in CITY_ABBREVIATIONS:
        return CITY_ABBREVIATIONS[city.upper()]
    low = city.lower()
    aliases = {"new york city": "New York", "bengaluru": "Bangalore", "washington dc": "Washington",
               "washington d.c": "Washington", "gurugram": "Gurgaon"}
    return aliases.get(low, " ".join(w.capitalize() for w in low.split()))


def _city_place(raw: str, state: str | None, country: str | None) -> Place | None:
    if raw.upper() in CITY_ABBREVIATIONS and raw.isupper():
        raw = CITY_ABBREVIATIONS[raw.upper()]
    low = raw.lower()
    if low in CITIES:
        c_state, c_country = CITIES[low]
        if country in (None, c_country):
            return Place(_canonical_city(raw), state or c_state, c_country)
    if len(raw) <= 40 and not any(ch.isdigit() for ch in raw) and low not in _REGIONS:
        return Place(raw.title() if raw.islower() else raw, state, country)
    return None


def _from_code_form(part: str) -> Place | None:
    m = _CODE_FORM.match(part.strip())
    if not m or m.group(1) not in COUNTRY_CODES:
        return None
    country = COUNTRY_CODES[m.group(1)]
    sub = (m.group(2) or "").rstrip("-").upper() or None
    rest = _clean(m.group(3).replace("-", " - "))
    state = sub if (country == "United States" and sub in US_STATES) or country == "Canada" else None
    if sub and state is None and sub in CITY_ABBREVIATIONS:  # "US-SF-HQ"
        return _city_place(sub, None, country)
    if not rest or rest.lower() in COUNTRIES:
        return Place(None, state, country)
    return _city_place(rest, state, country)


def _parse_part(part: str) -> list[Place]:
    pieces = [p for p in re.split(r",\s*", part) if p.strip()]
    if pieces and all(_CODE_FORM.match(p.strip()) for p in pieces):  # "US-SF-HQ, US-SEA"
        return [pl for p in pieces if (pl := _from_code_form(p))]
    is_remote = "remote" in part.lower()
    tokens = [_clean(t) for t in re.split(r",|\s[-–—]\s|\s/\s|:|\s&\s", part)]
    tokens = [t for t in tokens if t]
    state = country = None
    leftovers = []
    for i, raw in enumerate(tokens):
        low = raw.lower()
        if low in COUNTRIES and not (low == "us" and raw != raw.upper()):
            country = COUNTRIES[low]
        elif len(raw) == 2 and raw.upper() in US_STATES and (raw.isupper() or i > 0) and country in (None, "United States"):
            state, country = raw.upper(), "United States"
        elif low in STATE_NAME_TO_CODE and (low not in CITIES or (is_remote and low == "washington")):
            state, country = STATE_NAME_TO_CODE[low], country or "United States"
        elif len(raw) == 2 and raw.isupper() and raw in CA_PROVINCES and country in (None, "Canada"):
            state, country = raw, "Canada"
        elif low in PROVINCE_NAME_TO_CODE:
            state, country = PROVINCE_NAME_TO_CODE[low], "Canada"
        elif i > 0 and len(raw) == 3 and raw.isupper() and raw in COUNTRY_CODES:
            country = COUNTRY_CODES[raw]
        elif low in _REGIONS:
            continue
        elif (cs := re.fullmatch(r"([A-Z]{2,3})-([A-Z]{2,3})", raw)) and cs.group(1) in COUNTRY_CODES:
            # "Denver, USA-CO, USA, 12421" (SuccessFactors): country code and state, not a city called USA-CO
            country = COUNTRY_CODES[cs.group(1)]
            if country == "United States" and cs.group(2) in US_STATES or country == "Canada" and cs.group(2) in CA_PROVINCES:
                state = cs.group(2)
        else:
            # "Fort Worth TX", "San Mateo, CA United States", "CA United States"
            m = re.match(r"^(?:(.*?)\s+)?([A-Z]{2})(?:\s+(?:united states|usa|us))?$", raw, re.I)
            if m and m.group(2).upper() in US_STATES and (m.group(2).isupper() or m.group(1) is None):
                state, country = m.group(2).upper(), "United States"
                if m.group(1):
                    leftovers.append(m.group(1))
                continue
            leftovers.append(raw)

    places = [p for raw in leftovers if (p := _city_place(raw, state, country))]
    if not places and (state or country):
        places = [Place(None, state, country)]
    return [Place(p.city, p.state or state, p.country or country) for p in places]


def parse_locations(location: str) -> list[Place]:
    """All distinct places a job lists. Unrecognizable parts (e.g. bare "Remote") are skipped."""
    places: list[Place] = []
    for part in re.split(r"\s*[|;]\s*|\s+or\s+|\s*\n\s*", location or ""):
        for place in _parse_part(part):
            if place not in places:
                places.append(place)
    return places
