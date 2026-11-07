import re

# Phrases that mean an international student on F-1/OPT/H-1B can't be hired.
_NO_SPONSOR_PATTERNS = [
    r"(not|unable to|cannot|can ?not|won'?t|will not|do not|does not|don'?t|doesn'?t|no longer)\s+(be\s+)?(able to\s+)?(offer|provide|sponsor|support)\w*\s+(\w+\s+){0,4}(visa|sponsorship|h-?1b|work authori[sz]ation)",
    r"(visa|h-?1b|immigration)\s+sponsorship\s+(is\s+)?(not|unavailable)",
    r"no\s+(visa\s+|h-?1b\s+)?sponsorship",
    r"without\s+(the\s+)?(need\s+for\s+)?(current\s+or\s+future\s+)?(visa\s+|employer\s+)?sponsorship",
    r"must\s+be\s+(a\s+)?(u\.?s\.?|united states)\s+citizen",
    r"(u\.?s\.?|united states)\s+citizenship\s+(is\s+)?required",
    # "US Citizenship or Green Card Required", "U.S. citizen or permanent resident required/only"
    r"(u\.?s\.?|united states)\s+citizen(ship)?\s+(or|/)\s+(a\s+)?(green\s*card|permanent\s+residen\w*|lawful\s+permanent\s+residen\w*)"
    r"(\s+holders?)?\s+(status\s+)?(is\s+|are\s+)?(required|only)",
    r"must\s+be\s+(a\s+)?(u\.?s\.?\s+citizen|green\s*card\s+holder|(lawful\s+)?permanent\s+resident)\s+or\s+",
    r"(only|exclusively)\s+(open|available)\s+to\s+(u\.?s\.?\s+)?citizens",
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


# F-1 student work permits. Phrases that can only mean them:
_STUDENT = re.compile(
    r"\bstem[\s-]*opt\b|optional practical training|curricular practical training|"
    r"\b(opt|cpt)\s*/\s*(cpt|opt)\b|\bopt[\s-]*ead\b|\bf-?1\s*(visa|student|opt|status)",
    re.I,
)
# Short tokens that often mean something else ("CPT and ICD-10 coding", "F1 score", "F1-F5" pay grades,
# "OPT" for ophthalmology, "opt in"): they count only in a clause that also talks about visas or students.
_STUDENT_WEAK = re.compile(r"\bOPT\b|\b(?i:cpt|f-?1)\b")
_STUDENT_CONTEXT = re.compile(
    r"\bvisas?\b|students?\b|authori[sz]|sponsor|\bead\b|immigration|work permit|candidates?\b|applicants?\b|"
    r"eligib|intern",
    re.I,
)


def _mentions_students(clause: str) -> bool:
    return bool(_STUDENT.search(clause)) or bool(_STUDENT_WEAK.search(clause) and _STUDENT_CONTEXT.search(clause))


# Words that make a sentence about F-1 students a "no": "not eligible for sponsorship (e.g., H-1B, F-1 OPT)",
# "unable to accept CPT", "student visas ... are considered restricted".
_STUDENT_NO = re.compile(
    r"\b(not|no|unable|cannot|can ?not|can'?t|won'?t|ineligible|excluding|exclude[sd]?|except|unfortunately|"
    r"does ?n[o']t|do ?n[o']t|neither|nor|restricted|without)\b",
    re.I,
)
# Words that make it a "yes": "happy to sponsor international candidates (e.g., STEM OPT, OPT)",
# "open to F-1 (OPT/CPT)", "sponsorship available if you hold ... OPT EAD".
_STUDENT_YES = re.compile(
    r"\b(open to|happy to|glad to|welcome[sd]?|will consider|consider(ing)? (candidates|applications|applicants)|"
    r"accept(s|ed|ing)?|available|eligible|qualify|qualified|can sponsor|we sponsor|sponsoring|support(s|ing)? "
    r"(for )?(opt|stem|f-?1|cpt|international)|including those)\b",
    re.I,
)
# Sentence ends. Abbreviations ("e.g.", "i.e.", "etc.", "U.S.") are de-dotted first so a list of visa types
# stays in the sentence that says what happens to them.
_ABBREV = re.compile(r"\b(e\.g|i\.e|etc|u\.s\.a|u\.s|vs|inc|incl|approx|no)\.", re.I)
_SENTENCES = re.compile(r"(?<=[.!?])\s+|[\n\r•·▪◦�|]+|\s[-*]\s")
_EVERIFY = re.compile(r"\be-?verify\b", re.I)
_EVERIFY_NO = re.compile(r"\b(not|no|does ?n[o']t|do ?n[o']t)\b[^.;\n]{0,40}\be-?verify\b", re.I)


def _sentences(text: str) -> list[str]:
    text = _ABBREV.sub(lambda m: m.group(1).replace(".", ""), text)
    return [s for s in _SENTENCES.split(text) if s and s.strip()]


def student_visa_signal(text: str) -> str | None:
    """What a job description says about F-1 students on OPT, STEM OPT or CPT.

    Each sentence that mentions them is judged by its own words, or by the sentence before it when it is
    only a list ("This includes, but is not limited to: H-1B, F-1 OPT, ..."). A "no" anywhere wins, since
    most mentions are in lists of visas an employer won't support. Returns "excluded", "welcome" or None.
    """
    if not text or not (_STUDENT.search(text) or _STUDENT_WEAK.search(text)):
        return None
    verdict = None
    sentences = _sentences(text)
    for i, sentence in enumerate(sentences):
        if not _mentions_students(sentence):
            continue
        before = sentences[i - 1] if i else ""
        if _STUDENT_NO.search(sentence):
            return "excluded"
        if _STUDENT_YES.search(sentence):
            verdict = "welcome"
        elif _STUDENT_NO.search(before):
            return "excluded"
        elif _STUDENT_YES.search(before):
            verdict = "welcome"
    return verdict


def mentions_everify(text: str) -> bool:
    """The description says the employer uses E-Verify ("We participate in E-Verify")."""
    return bool(text) and bool(_EVERIFY.search(text)) and not _EVERIFY_NO.search(text)
