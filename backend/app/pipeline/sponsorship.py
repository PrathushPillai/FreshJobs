import re

# Phrases that mean an international student on F-1/OPT/H-1B can't be hired.
_NO_SPONSOR_PATTERNS = [
    r"(not|unable to|cannot|can ?not|won'?t|will not|do not|does not|don'?t|doesn'?t|no longer)\s+(be\s+)?(able to\s+)?(offer|provide|sponsor|support)\w*\s+(\w+\s+){0,4}(visa|sponsorship|h-?1b|work authori[sz]ation)",
    r"(visa|h-?1b|immigration)\s+sponsorship\s+(is\s+)?(not|unavailable)",
    r"no\s+(visa\s+|h-?1b\s+)?sponsorship",
    r"without\s+(the\s+)?(need\s+for\s+)?(current\s+or\s+future\s+)?(visa\s+|employer\s+)?sponsorship",
    r"must\s+be\s+(a\s+)?(u\.?s\.?|united states)\s+citizen",
    r"(u\.?s\.?|united states)\s+citizenship\s+(is\s+)?required",
    r"(active|current|ability to obtain)\s+(\w+\s+){0,3}(security\s+)?clearance",
    r"(ts/sci|top secret|secret clearance)",
    r"green card holders? only",
    r"(u\.?s\.?\s+)?persons?\s+as\s+defined\s+(by|in|under)\s+itar",
]
_NO_SPONSOR = re.compile("|".join(f"(?:{p})" for p in _NO_SPONSOR_PATTERNS), re.I)


def find_no_sponsor_phrase(text: str) -> str | None:
    """Return the matching phrase if the text rules out visa sponsorship, else None."""
    m = _NO_SPONSOR.search(text or "")
    return m.group(0) if m else None
