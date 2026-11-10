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
    # "active U.S. Secret security clearance": words may carry dots and slashes ("U.S.", "TS/SCI")
    r"(active|current|ability to obtain)\s+([\w./-]+\s+){0,3}(security\s+)?clearance",
    # "Eligible to obtain and maintain an active U.S. Secret security clearance" (Anduril)
    r"(eligib\w*|able)\s+to\s+(obtain|hold|maintain)\s+(and\s+maintain\s+)?(an?\s+)?(active\s+)?([\w./-]+\s+){0,3}(security\s+)?clearance",
    r"(ts/sci|top secret|secret clearance)",
    r"green card holders? only",
    r"(u\.?s\.?\s+)?persons?\s+as\s+defined\s+(by|in|under)\s+itar",
    # Found missing in a 100-job check (2026-11-09), each with the posting it came from:
    # "This position is not eligible for Intel immigration sponsorship" (Intel, Qualcomm)
    r"not\s+eligible\s+for\s+(\w+\s+){0,3}(immigration|visa|h-?1b)\s+sponsorship",
    # "Sponsorship not available." (Switchboard), "Sponsorship is not offered"
    r"sponsorship\s+(is\s+)?(not|un)\s*(available|offered|provided|possible)",
    # "The company will not consider candidates who require sponsorship for a work-authorized visa" (Philips)
    r"(will\s+not|won'?t|does\s+not|do\s+not|cannot|can ?not|unable\s+to)\s+(consider|accept|hire|employ)\s+"
    r"([\w-]+\s+){0,4}(who|that)\s+(will\s+)?(now\s+or\s+in\s+the\s+future\s+)?(require|need)s?\s+([\w-]+\s+){0,3}sponsorship",
    # "PwC does not intend to hire ... job seekers who will need, now or in the future, PwC sponsorship" (PwC)
    r"(not|no)\s+intend\s+to\s+hire\s+([\w-]+\s+){0,8}(who|that)\s+(will\s+)?(need|require)",
    # "may not be able to employ candidates who have ... work authorization ... or support future H-1B sponsorship" (Roblox)
    r"not\s+be\s+able\s+to\s+(employ|hire|support|sponsor)[^.;]{0,160}(visa|h-?1b|sponsorship)",
    # Export control: "must be a U.S. citizen or national, (ii) U.S. lawful, permanent resident ..., (iii) Refugee" (SpaceX)
    r"citizen\s+or\s+national\W{1,6}(\(?ii\)?\s*)?(u\.?s\.?\s+)?lawful,?\s+permanent\s+resident",
    # "U.S. Citizen, U.S. National, lawful permanent resident, refugee, or asylee" (Boeing, export control)
    r"citizen,?\s+(u\.?s\.?\s+)?national,?\s+(\w+\s+)?(lawful\s+)?permanent\s+resident,?\s+refugee",
    # "U.S. Person status is required" (CHAOS Industries), "must be a U.S. person"
    r"(u\.?s\.?|united states)\s+person\s+(status\s+)?(is\s+)?(required|only)",
    r"must\s+be\s+(a\s+)?(u\.?s\.?|united states)\s+person",
    # "The person hired for this role must therefore either be a "U.S. person" as defined by 22 C.F.R. § 120.15 or
    # otherwise eligible for a federally issued export control license" (K2 Space, ITAR). An export licence for
    # one foreign hire is rare, so this counts as ruling F-1/H-1B candidates out.
    r"must\s+(\w+\s+){0,3}be\s+(an?\s+)?(us|united states)\s+persons?\b",
    r"(us|united states)\s+persons?\s+as\s+defined\s+(by|in|under)\s+(the\s+)?(itar|ear|22\s*c\.?\s?f\.?\s?r)",
    # "As defined in the ITAR, "U.S. Persons" include U.S. citizens, lawful permanent residents ..."
    r"as\s+defined\s+(by|in|under)\s+(the\s+)?(itar|ear|export\s+administration\s+regulations)\W{1,4}(us\s+)?persons?\s+include",
    # "requires U.S. citizenship and residence in an approved hiring hub" (Second Front)
    r"requires?\s+(u\.?s\.?|united states)\s+citizenship",
    r"(u\.?s\.?|united states)\s+citizenship\s+(is\s+)?(a\s+)?(requirement|mandatory)",
    # "U.S. citizenship and residence in an approved hiring hub" as a requirement bullet (Second Front); not the
    # EEO line "regardless of ... citizenship status"
    r"(u\.?s\.?|united states)\s+citizenship(?!\s+status)\s+(and\s+(residence|residency|the\s+ability)|only)",
    # Second, unseen sample (2026-11-09):
    # "This role is not open for work sponsorship at this time." (Lovelytics)
    r"not\s+open\s+(for|to)\s+(work\s+|visa\s+|h-?1b\s+)?sponsorship",
    # "WSI will not now or in the future commence an immigration case or 'sponsor' an individual" (Williams-Sonoma)
    r"(will\s+not|won'?t|does\s+not|do\s+not)\s+(now\s+or\s+in\s+the\s+future\s+)?(commence|file|initiate|start|pursue)\s+(an?\s+)?immigration\s+(case|petition|process)",
    # "This role requires access to export-controlled information ... that require 'U.S. persons'" (Aurelius)
    r"export[- ]controlled[^.;]{0,160}(require|requires|required|restricted\s+to)[^.;]{0,60}(us\s+persons?|citizens)",
    # "Must be a U.S. Citizen or Permanent Resident", "US citizens or permanent residents only"
    r"(must\s+be|requires?|required:?|only)\s+(a\s+)?(us\s+)?citizens?\s+or\s+(a\s+)?(lawful\s+)?permanent\s+residents?",
    r"(us\s+)?citizens?\s+or\s+(lawful\s+)?permanent\s+residents?\s+(only|required)",
    # --- Every common way of saying "US citizens (or green card holders) only" (2026-11-10, after a K2 Space
    # ITAR posting showed "May sponsor"). Matched on text where "U.S." is "US" and quotes are removed. ---
    # Citizenship: "must hold/possess/have US citizenship", "must be citizens of the United States",
    # "applicants must be United States citizens", "US citizen required", "US citizenship mandatory/a must"
    r"must\s+(hold|possess|have|maintain)\s+(a\s+)?(current\s+|valid\s+)?(us|united states)\s+citizenship",
    r"must\s+be\s+(a\s+)?citizens?\s+of\s+the\s+(us|united states|usa)\b",
    r"(must|should|shall)\s+be\s+(us|united states|american)\s+citizens",
    r"(us|united states)\s+citizen(ship)?\s+(is\s+)?(required|mandatory|a\s+must|needed|necessary|only)\b",
    r"(us|united states)\s+citizen(ship)?\s+(status\s+)?(is\s+)?(a\s+)?(prerequisite|condition\s+of\s+employment|must)",
    r"requires?\s+(applicants\s+|candidates\s+|you\s+)?(to\s+)?(be\s+|being\s+)(a\s+)?(us|united states)\s+citizens?",
    r"citizenship\s*(requirements?)?\s*:\s*(us|united states|usa)\b(\s+citizens?)?(\s+only)?",
    # "US citizens only", "open only to US citizens", "only US citizens will be considered", "limited/restricted to US citizens"
    r"(us|united states|american)\s+citizens\s+(only|exclusively)\b",
    r"only\s+(us|united states|american)\s+citizens\b",
    r"(limited|restricted|open)\s+(only\s+)?to\s+(us|united states|american)\s+citizens",
    r"(open|available)\s+only\s+to\s+(us|united states|american)\s+citizens",
    # Staffing shorthand: "USC only", "USC/GC only", "USC or GC", "GC/USC", "GC holders and US citizens only"
    r"\b(usc|gc)\s*(/|&|and|or|,)\s*(gc|usc|green\s*card(\s+holders?)?|ead)\b",
    r"\busc\s+(only|required)\b",
    r"(green\s*card|gc|permanent\s+residents?)\s+(holders?\s+)?(and|or|&|/)\s+(us\s+)?citizens\s+only",
    r"(only|must\s+be)\s+(a\s+)?(us\s+)?citizens?\s*(and|or|&|/)\s*(green\s*card|gc)\s*(holders?)?",
    # Visa types ruled out: "No H1B", "No OPT/CPT", "No visa holders", "H1B candidates need not apply",
    # "unable to work with H1B or OPT candidates", "not accepting H-1B transfers"
    r"\bno\s+(h-?1-?b|opt|cpt|ead|f-?1|visa)s?(?![\w-])(\s+(holders?|candidates?|transfers?|sponsorship))?(?!\s+(required|needed|necessary))",
    r"(h-?1-?b|opt|cpt|f-?1|visa)\s+(holders?|candidates?|applicants?)\s+(need\s+not|should\s+not|cannot|can\s?not|will\s+not\s+be\s+considered|are\s+not\s+(eligible|considered))",
    r"(unable|not\s+able|cannot|can\s?not)\s+to\s+(work|consider|proceed)\s+with\s+([\w/-]+\s+){0,3}(h-?1-?b|opt|cpt|visa)",
    r"not\s+(accepting|considering)\s+(any\s+)?(h-?1-?b|opt|cpt|visa)",
    # Sponsorship wording not caught above: "applicants requiring sponsorship will not be considered",
    # "must not require sponsorship", "does not qualify for sponsorship", "we are not sponsoring"
    r"(requir|need)\w*\s+([\w-]+\s+){0,3}sponsorship\s+(now\s+or\s+in\s+the\s+future\s+)?(will\s+not|won'?t|cannot|are\s+not|is\s+not)\s+(be\s+)?(considered|eligible|accepted)",
    r"(must|should)\s+not\s+(now\s+or\s+in\s+the\s+future\s+)?(require|need)\s+([\w-]+\s+){0,3}sponsorship",
    r"(does\s+not|doesn'?t|won'?t|will\s+not)\s+qualify\s+for\s+([\w-]+\s+){0,2}sponsorship",
    r"(are|is)\s+not\s+(currently\s+)?(sponsoring|able\s+to\s+sponsor|offering\s+sponsorship)",
    r"not\s+(a\s+)?(visa\s+)?sponsorship\s+(eligible|opportunity)",
    # Export control and US persons: "protected individual as defined by 8 USC 1324b(a)(3)", "22 CFR 120.62",
    # "must qualify as / meet the definition of a US person", "US person status", "only US persons"
    # Only as a requirement: "proof of citizenship ... or protected status (e.g. under 8 USC 1324b(a)(3)) for assessment"
    # (an export licence can then be sought) doesn't rule anyone out.
    r"(must\s+be|only|limited\s+to|restricted\s+to|requires?|eligible)[^.;]{0,160}protected\s+individuals?\s+(as\s+defined|under|pursuant)[^.;]{0,40}1324b",
    r"(must\s+be|only|limited\s+to|restricted\s+to)[^.;]{0,200}1324b\s*\(a\)\s*\(3\)",
    r"22\s*cfr\s*(§\s*)?120\.(15|62)",
    r"(qualify|qualifies)\s+as\s+(an?\s+)?(us|united states)\s+persons?",
    r"(meet|meets|satisfy|satisfies)\s+the\s+definition\s+of\s+(an?\s+)?(us|united states)\s+persons?",
    r"(us|united states)\s+person\s+status",
    r"(only|restricted|limited)\s+(to\s+)?(us|united states)\s+persons",
    r"(itar|ear|export[- ]control\w*)[^.;]{0,160}(us|united states)\s+(persons?|citizens?)\s+(only|required|is\s+required)",
    r"(itar|export[- ]control\w*)[^.;]{0,120}(must|required\s+to)\s+be\s+(an?\s+)?(us|united states)\s+(citizen|person|national)",
    # Clearances (only US citizens can hold one): "security clearance required", "public trust clearance",
    # "polygraph", "must be clearable", "SCI eligibility", "DoD/DOE/Q/L clearance". "No clearance required" is
    # removed before matching (_NOT_REQUIRED).
    # (A "medical clearance" or "background clearance" for nurses isn't one: the kind must be named.)
    r"(security|secret|ts|government|federal|dod|doe)\s+clearance\s+(is\s+)?(required|mandatory|a\s+must)\b",
    r"(must|required\s+to)\s+(have|hold|possess|maintain|obtain)\s+(an?\s+)?(active\s+|current\s+|interim\s+)?([\w./-]+\s+){0,2}(security|secret|ts/?sci|top\s+secret|government|federal|dod|doe|q|l)\s+clearance",
    r"public\s+trust\s+(clearance|position|investigation|background)",
    r"\b(ci|full[- ]scope|lifestyle)\s+poly(graph)?\b",
    # (Not a bare "polygraph": Massachusetts' lie detector notice, "It is unlawful ... to require ... a polygraph",
    # is in many postings.)
    r"(with|and|plus|including|requires?|required)\s+(an?\s+)?(\w+\s+)?polygraph\b(?!\s+(examination|test)\s+or\s+similar)",
    r"(must\s+be\s+|be\s+)clearable\b",
    r"\bsci\s+eligib",
    r"\b(dod|doe|dhs|nrc|q|l)\s+(security\s+)?clearance",
]
# "must be legally authorized to work in the United States on a permanent basis" (Latitude AI): OPT and H-1B
# are temporary, so it rules them out, unless the same posting also says it sponsors (then it's mixed: left
# for the reader, with the "says it sponsors" badge showing).
_PERMANENT_BASIS = re.compile(r"authori[sz]ed\s+to\s+work\s+(in\s+the\s+(u\.?s\.?|united states)\s+)?on\s+a\s+permanent\s+basis", re.I)
_NO_SPONSOR = re.compile("|".join(f"(?:{p})" for p in _NO_SPONSOR_PATTERNS), re.I)
# Phrases that say a clearance, citizenship or polygraph is NOT needed; dropped before matching.
_NOT_REQUIRED = re.compile(
    r"employee\s+polygraph\s+protection\s+act|"
    r"(no|not\s+an?|without\s+an?|does\s+not\s+require\s+an?|doesn'?t\s+require\s+an?|no\s+need\s+for\s+an?)\s+"
    r"([\w./-]+\s+){0,3}(clearance|polygraph|citizenship)(\s+(is\s+)?(required|needed|necessary))?"
    r"|(clearance|polygraph|citizenship)\s+(is\s+)?not\s+(required|needed|necessary)",
    re.I,
)
# Staffing visa lists ("USC/GC", "GC and Citizens Only") and the visas that would make them welcoming.
_SHORTHAND = re.compile(r"\b(usc|gc|ead|green\s*card)\b|citizens\s+only", re.I)
_STUDENT_OR_H1B = re.compile(r"\b(opt|cpt|h-?1-?b|f-?1|stem)\b|all\s+visas|any\s+visa", re.I)
_NO_STUDENT_OR_H1B = re.compile(r"\bno\s+(h-?1-?b|opt|cpt|f-?1)", re.I)
_QUOTES = re.compile(r"[\"“”‘’«»]")
_US_DOTS = re.compile(r"\bU\.\s?S\.(?:\s?A\.)?", re.I)


def find_no_sponsor_phrase(text: str) -> str | None:
    """Return the matching phrase if the text rules out visa sponsorship, else None."""
    # "U.S." -> "US" first: the dots ended "[^.]"-style spans early ("... certain U.S. visa categories, or support
    # future H-1B sponsorship" at Roblox wasn't caught).
    text = _US_DOTS.sub("US", text or "")
    text = _QUOTES.sub("", text)  # "a “U.S. person”" -> "a US person"
    text = _NOT_REQUIRED.sub(" ", text)  # "No security clearance required" says the opposite
    for m in _NO_SPONSOR.finditer(text):
        if _SHORTHAND.search(m.group(0)):
            # A staffing visa list counts only if it leaves students and H-1B out: "Need GC,USC,OPT,EAD" and
            # "H1B GC and Citizens Only" welcome them.
            window = text[max(0, m.start() - 40): m.end() + 40]
            if _STUDENT_OR_H1B.search(window) and not _NO_STUDENT_OR_H1B.search(window):
                continue
        return m.group(0)
    p = _PERMANENT_BASIS.search(text or "")
    return p.group(0) if p and not says_sponsors(text) else None


# Postings that say they *do* sponsor: "We do sponsor visas!" (Anthropic), "While Etsy supports visa sponsorship",
# "Visa sponsorship is available for this position" (Latitude AI). A sentence with a "no" in it doesn't count,
# and neither does a posting that rules sponsorship out anywhere (see says_sponsors).
_SPONSOR_YES = re.compile(
    r"\b(we|company|firm|employer|\w+)\s+(do|does|will|can|is\s+able\s+to|are\s+able\s+to|happily|proudly)?\s*"
    r"(sponsor|sponsors)\s+(\w+\s+){0,2}(visas?|h-?1bs?|work\s+visas?|green\s+cards?|immigration)\b"
    r"|\b(supports?|offers?|provides?|provide)\s+(\w+\s+){0,2}(visa|h-?1b|immigration)\s+sponsorship"
    r"|\b(visa|h-?1b|immigration)\s+sponsorships?\s+(is\s+|are\s+)?(available|offered|provided|possible|supported)"
    # "Is role eligible for Immigration Sponsorship?: Yes" (Palo Alto Networks)
    r"|\beligible\s+for\s+(immigration|visa)\s+sponsorship\W{0,4}\s*yes\b"
    # "Visa transfers are supported" (Raydar): an H-1B transfer is a sponsorship
    r"|\b(visa|h-?1b)\s+transfers?\s+(are\s+|is\s+)?(supported|available|welcome)"
    # "Visa Sponsorship & Immigration Support" in a benefits list (Resolve AI)
    r"|\bvisa\s+sponsorship\s*(&|and)\s*immigration\s+support"
    r"|\bopen\s+to\s+(sponsoring|sponsor)\b",
    re.I,
)
_SPONSOR_YES_NEGATED = re.compile(r"\b(not|no|unable|cannot|can ?not|can'?t|won'?t|never|neither|nor)\b", re.I)


def says_sponsors(text: str) -> bool:
    """The posting says it sponsors visas (and nowhere says it doesn't)."""
    text = _US_DOTS.sub("US", text or "")
    if not text or not _SPONSOR_YES.search(text) or _NO_SPONSOR.search(text):
        return False
    return any(_SPONSOR_YES.search(s) and not _SPONSOR_YES_NEGATED.search(s) for s in _sentences(text))


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


# --- One verdict per job: how likely the employer is to sponsor an H-1B for it ---------------------------
# From what every job already carries: the posting's own words, the employer's certified H-1B filings (DOL,
# latest two fiscal years) and E-Verify (needed for STEM OPT). The Feed shows it on each card, and the Visa
# pill filters by it (main.list_jobs, sponsor_likely=). Shown, never used to hide jobs by default.
LIKELY_FILINGS = 10


def sponsor_likelihood(no_sponsor: bool, sponsors_text: bool, h1b_filings: int | None, everify: str | None) -> str:
    """"ruled_out" (the posting rules it out), "likely" (says it sponsors, or 10+ filings), "possible"
    (1-9 filings, or E-Verify for STEM OPT) or "unlikely" (no sign at all)."""
    if no_sponsor:
        return "ruled_out"
    if sponsors_text or (h1b_filings or 0) >= LIKELY_FILINGS:
        return "likely"
    if (h1b_filings or 0) > 0 or everify == "yes":
        return "possible"
    return "unlikely"
