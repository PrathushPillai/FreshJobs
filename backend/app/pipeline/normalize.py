import re
from datetime import timedelta
from html.parser import HTMLParser

_BLOCK_TAGS = {"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr"}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        self.parts.append(data)


def html_to_text(raw: str) -> str:
    if not raw:
        return ""
    parser = _TextExtractor()
    parser.feed(raw)
    text = "".join(parser.parts)
    text = re.sub(r"[ \t\xa0]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


_SENIOR = re.compile(
    r"\b(senior|sr\.?|staff|principal|lead|director|head of|vp|vice president|architect|distinguished|fellow|iii|iv)\b",
    re.I,
)
# "Product Manager", "Account Manager", "Program Manager" are often early-career; people managers aren't.
_MANAGER = re.compile(r"\bmanager\b", re.I)
_PEOPLE_MANAGER = re.compile(
    r"\b(engineering|software|data science|analytics|general|regional|group|people)\s+manager\b|\bmanager,\s*(engineering|software)",
    re.I,
)
_ENTRY = re.compile(
    r"\b(new grad|new graduate|graduate|entry[- ]level|junior|jr\.?|associate|early career|university|campus|i|1|intern)\b",
    re.I,
)


def seniority(title: str) -> str:
    if _SENIOR.search(title) or _PEOPLE_MANAGER.search(title):
        return "senior"
    if _MANAGER.search(title):
        return "mid" if not _ENTRY.search(title) else "entry"
    if _ENTRY.search(title):
        return "entry"
    if re.search(r"\b(ii|2)\b", title, re.I):
        return "mid"
    return "unknown"


def normalize_title(title: str) -> str:
    """Lowercased title with noise (punctuation, req codes, location suffixes) removed, for fingerprinting."""
    t = title.lower()
    t = re.sub(r"\(.*?\)|\[.*?\]", " ", t)  # "(Remote)", "[R12345]"
    t = re.sub(r"\b(req|r|jr)[-#]?\d+\b", " ", t)
    t = re.sub(r"[^a-z0-9+#]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def normalize_location(location: str) -> str:
    loc = location.lower()
    loc = re.sub(r"[^a-z0-9|]+", " ", loc)
    return re.sub(r"\s+", " ", loc).strip()


_YEARS = re.compile(
    r"(?:(?:minimum|min\.?|at least)\s+(?:of\s+)?)?"
    r"(\d{1,2})\s*(?:\+|plus)?\s*(?:(?:-|–|to)\s*(\d{1,2})\s*\+?\s*)?(?:years?|yrs?)\b"
    r"(?:\s+of)?(?:\s+[\w/&,.+#-]+){0,6}?\s+(?:experience|exp\b)",
    re.I,
)
_YEARS_AFTER = re.compile(r"experience\s*(?:of|:)?\s*(\d{1,2})\s*\+?\s*(?:years?|yrs?)", re.I)

_LEVEL_RANK = {"entry": 0, "mid": 1, "senior": 2}


def experience_years(text: str) -> int | None:
    """Years of experience the posting asks for, e.g. "3+ years of Python experience" -> 3.

    With several mentions ("5+ years overall, 2+ with Spark") the largest is the overall bar.
    """
    found = [int(m.group(1)) for m in _YEARS.finditer(text or "")]
    found += [int(m.group(1)) for m in _YEARS_AFTER.finditer(text or "")]
    found = [y for y in found if 0 <= y <= 20]
    return max(found) if found else None


def experience_level(min_years: int | None, title_seniority: str) -> str:
    """entry (0-2 yrs) | mid (3-5) | senior (6+) | unknown. Takes the higher of the title and the years asked."""
    levels = []
    if min_years is not None:
        levels.append("entry" if min_years <= 2 else "mid" if min_years <= 5 else "senior")
    if title_seniority in _LEVEL_RANK:
        levels.append(title_seniority)
    return max(levels, key=_LEVEL_RANK.__getitem__) if levels else "unknown"


_COMPANY_SUFFIX = re.compile(r"\b(inc|incorporated|llc|l\.l\.c|ltd|limited|corp|corporation|co|plc|gmbh|s\.a|ag)\b\.?", re.I)


def company_key(name: str) -> str:
    """Normalized employer name for matching the same company across sources ("Stripe, Inc." -> "stripe")."""
    key = _COMPANY_SUFFIX.sub(" ", (name or "").lower())
    return re.sub(r"[^a-z0-9]+", "", key)


def listed_at(posted_at, first_seen_at, baseline: bool):
    """Best estimate of when a job went live.

    Many boards (Workday, BambooHR...) give only a date, which reads as midnight: a job posted at 2 pm
    would then sort below everything posted at 1 am and fail a "posted in the last hour" filter. A job we
    caught on a later poll within two days of that date went live when we found it (polls are minutes
    apart), so that's the better estimate. Exact timestamps (an aggregator that saw it hours before us)
    are kept, and a baseline job's time is all the source tells us.
    """
    if posted_at is None:
        # Caught on a later poll: it went live minutes ago. Listed before we started tracking the
        # source: unknown (our first scan's time would wrongly read as "posted just now").
        return None if baseline else first_seen_at
    if posted_at > first_seen_at:
        return first_seen_at
    if baseline:
        return posted_at
    date_only = (posted_at.hour, posted_at.minute, posted_at.second, posted_at.microsecond) == (0, 0, 0, 0)
    if date_only and first_seen_at - posted_at < timedelta(days=2):
        return first_seen_at
    return posted_at
