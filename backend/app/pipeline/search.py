"""Related-role search: "data analyst" also finds BI analysts, analytics engineers, data scientists, ..."""

import re

# Role families. A search that matches any term in a family also searches the family's other terms.
ROLE_FAMILIES: dict[str, list[str]] = {
    # Data is three families, so "data engineer" brings data-platform roles, not marketing analysts.
    "data analytics": [
        "data analyst", "data analytics", "analytics", "business intelligence", "bi analyst", "bi developer",
        "business analyst", "reporting analyst", "insights analyst", "product analyst", "marketing analyst",
        "analytics engineer", "data visualization", "tableau", "power bi", "data scientist",
    ],
    "data science": [
        "data scientist", "data science", "quantitative analyst", "statistician", "decision scientist",
        "applied scientist",
    ],
    "data engineering": [
        "data engineer", "analytics engineer", "data platform", "analytics platform", "data infrastructure",
        "big data", "etl developer", "etl engineer", "data pipeline", "data architect", "data warehouse",
        "database engineer", "database developer", "sql developer", "data integration", "dataops",
        "snowflake developer", "databricks engineer",
    ],
    "machine learning": [
        "machine learning", "ml engineer", "mle", "ai engineer", "applied scientist", "research scientist",
        "deep learning", "computer vision", "nlp", "llm", "generative ai", "mlops", "ai researcher",
        "research engineer", "data scientist",
    ],
    "software": [
        "software engineer", "software developer", "swe", "backend", "back end", "back-end", "frontend",
        "front end", "front-end", "full stack", "fullstack", "full-stack", "web developer", "application developer",
        "programmer", "developer", "platform engineer", "member of technical staff", "software development engineer",
        "sde", "development engineer", "java developer", "python developer", ".net developer",
    ],
    "frontend": ["frontend", "front end", "front-end", "ui engineer", "web developer", "react developer", "javascript"],
    "backend": ["backend", "back end", "back-end", "api engineer", "server engineer", "distributed systems"],
    "mobile": ["mobile", "ios", "android", "react native", "flutter", "swift developer", "kotlin"],
    "devops": [
        "devops", "site reliability", "sre", "platform engineer", "infrastructure engineer", "cloud engineer",
        "systems engineer", "production engineer", "build engineer", "release engineer", "kubernetes",
    ],
    "security": [
        "security engineer", "security analyst", "cybersecurity", "information security", "appsec",
        "application security", "penetration tester", "soc analyst", "security architect", "infosec",
    ],
    "qa": ["qa engineer", "quality assurance", "test engineer", "sdet", "automation engineer", "quality engineer"],
    "product": ["product manager", "product owner", "technical product manager", "product lead", "group product manager"],
    "program": ["program manager", "project manager", "technical program manager", "tpm", "scrum master", "delivery manager"],
    "design": ["product designer", "ux designer", "ui designer", "ux researcher", "user researcher", "visual designer",
               "interaction designer", "design lead"],
    "marketing": ["marketing", "growth", "seo", "content marketing", "product marketing", "demand generation",
                  "brand", "social media", "communications"],
    "sales": ["sales", "account executive", "business development", "sales development", "sdr", "bdr",
              "account manager", "solutions engineer", "sales engineer", "partnerships"],
    "it support": [
        "it support", "help desk", "helpdesk", "service desk", "desktop support", "deskside support",
        "technical support", "tech support", "end user support", "end user computing", "support technician",
        "it technician", "it specialist", "it analyst", "it administrator", "it coordinator", "it engineer",
        "it operations", "systems administrator", "system administrator", "sysadmin", "network administrator",
        "noc technician", "noc engineer", "field technician", "computer technician", "it associate",
    ],
    "customer": ["customer success", "customer support", "support engineer", "technical support", "customer experience",
                 "implementation", "onboarding specialist"],
    "finance": ["finance", "financial analyst", "accountant", "accounting", "fp&a", "controller", "treasury",
                "tax", "auditor", "investment analyst"],
    "people": ["recruiter", "talent acquisition", "sourcer", "hr", "human resources", "people operations",
               "people partner", "hr business partner"],
    "operations": ["operations", "business operations", "strategy & operations", "bizops", "supply chain",
                   "logistics", "procurement"],
    "hardware": ["hardware engineer", "electrical engineer", "embedded", "firmware", "asic", "fpga",
                 "mechanical engineer", "robotics"],
    "solutions": ["solutions architect", "solutions engineer", "sales engineer", "forward deployed", "field engineer",
                  "technical account manager", "customer engineer"],
}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.lower()).strip()


def expand_query(q: str) -> list[str]:
    """Terms to search job titles for: the query itself plus related roles, most relevant first."""
    q = _norm(q)
    if not q:
        return []
    terms = [q]
    for family, members in ROLE_FAMILIES.items():
        vocab = [family, *members]
        # The query names a role in the family ("data analyst"), or is the family's word ("data", "sales").
        # Word order doesn't matter: "analyst data" is "data analyst".
        words = sorted(q.split())
        if any(q == m or sorted(m.split()) == words or (len(q) >= 4 and (q in m.split() or m in q)) for m in vocab):
            for m in members:
                if m not in terms:
                    terms.append(m)
    return terms


# Ways the same title is written. These count as what was typed, not as a related role: "frontend
# developer" means "Front-End Developer" and "Frontend Engineer" too. Keys and values are whole-word phrases.
VARIANTS: dict[str, list[str]] = {
    "frontend": ["front end"], "front end": ["frontend"], "backend": ["back end"], "back end": ["backend"],
    "fullstack": ["full stack"], "full stack": ["fullstack"], "devops": ["dev ops"],
    "sde": ["software development engineer"], "software development engineer": ["sde"],
    "swe": ["software engineer"], "software engineer": ["swe"],
    "ml": ["machine learning"], "machine learning": ["ml"], "ai": ["artificial intelligence"],
    "bi": ["business intelligence"], "business intelligence": ["bi"], "qa": ["quality assurance"],
    "quality assurance": ["qa"], "sre": ["site reliability"], "site reliability": ["sre"],
    "nlp": ["natural language processing"], "ux": ["user experience"], "ui": ["user interface"],
    "developer": ["engineer"], "engineer": ["developer"], "dev": ["developer"],
}
MAX_VARIANTS = 12


def _words(text: str) -> list[str]:
    """Lowercase title words as FTS5 sees them (hyphens and punctuation split words)."""
    return re.findall(r"[a-z0-9]+", text.lower())


def query_variants(q: str) -> list[str]:
    """q and the other ways its title is written ("frontend developer" -> "front end engineer", ...)."""
    found = [" ".join(_words(q))]
    for src in sorted(VARIANTS, key=lambda k: -len(k)):
        pattern = re.compile(rf"(?<![a-z0-9]){re.escape(src)}(?![a-z0-9])")
        for v in list(found):
            if pattern.search(v):
                for alt in VARIANTS[src]:
                    swapped = pattern.sub(alt, v)
                    if swapped not in found and len(found) < MAX_VARIANTS:
                        found.append(swapped)
    return [v for v in found if v]


def _word_forms(word: str) -> list[str]:
    """A word and its plural or singular: "engineer" also finds "engineers", but not "engineering", and
    "react" never finds "reactor" (a prefix search did both)."""
    if len(word) <= 2 or word.isdigit():
        return [word]
    if word.endswith("ies"):
        return [word, word[:-3] + "y"]
    if word.endswith("s") and not word.endswith("ss"):
        return [word, word[:-1]]
    if word.endswith(("x", "ch", "sh", "ss")):
        return [word, word + "es"]
    if word.endswith("y") and word[-2:-1] not in "aeiou":
        return [word, word[:-1] + "ies"]
    return [word, word + "s"]


def distinct_roles(terms: list[str], exclude: list[str] = ()) -> list[str]:
    """terms with each role once ("backend", "back end", "back-end" are one), minus spellings of `exclude`."""
    seen = {" ".join(_words(v)) for e in exclude for v in query_variants(e)}
    out = []
    for t in terms:
        spellings = {" ".join(_words(v)) for v in query_variants(t)}
        if not spellings & seen:
            out.append(t)
            seen |= spellings
    return out


def _fts_term(term: str) -> str | None:
    """One phrase for FTS5, its last word in singular or plural: ("data analyst" OR "data analysts")."""
    words = _words(term)
    if not words:
        return None
    forms = ['"' + " ".join(words[:-1] + [w]) + '"' for w in _word_forms(words[-1])]
    return forms[0] if len(forms) == 1 else "(" + " OR ".join(forms) + ")"


def fts_all_words(q: str) -> str | None:
    """FTS5 query: the title has every word of q (or of one of its variants), in any order."""
    groups = []
    for variant in query_variants(q):
        parts = [p for w in _words(variant) if w not in STOPWORDS and (p := _fts_term(w))]
        if parts:
            groups.append("(" + " AND ".join(parts) + ")")
    return "title : (" + " OR ".join(dict.fromkeys(groups)) + ")" if groups else None


def fts_any_term(terms: list[str]) -> str | None:
    """FTS5 query: the title contains any of the terms as a phrase (each in all its variants)."""
    parts = [p for t in terms for v in query_variants(t) if (p := _fts_term(v))]
    return "title : (" + " OR ".join(dict.fromkeys(parts)) + ")" if parts else None


def fts_company(q: str) -> str | None:
    phrase = _fts_term(q)
    return f"company_name : {phrase}" if phrase else None


STOPWORDS = {"and", "or", "of", "the", "a", "an", "in", "for", "to", "not", "near"}
