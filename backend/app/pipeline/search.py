"""Related-role search: "data analyst" also finds BI analysts, analytics engineers, data scientists, ..."""

import re

# Role families. A search that matches any term in a family also searches the family's other terms.
ROLE_FAMILIES: dict[str, list[str]] = {
    "data": [
        "data analyst", "data analytics", "data scientist", "data science", "data engineer", "analytics engineer",
        "business intelligence", "bi analyst", "bi developer", "business analyst", "reporting analyst",
        "insights analyst", "product analyst", "marketing analyst", "quantitative analyst", "statistician",
        "decision scientist", "data architect", "etl developer", "analytics", "data visualization", "tableau",
        "power bi", "sql developer",
    ],
    "machine learning": [
        "machine learning", "ml engineer", "mle", "ai engineer", "applied scientist", "research scientist",
        "deep learning", "computer vision", "nlp", "llm", "generative ai", "mlops", "ai researcher",
        "research engineer", "data scientist",
    ],
    "software": [
        "software engineer", "software developer", "swe", "backend", "back end", "back-end", "frontend",
        "front end", "front-end", "full stack", "fullstack", "full-stack", "web developer", "application developer",
        "programmer", "developer", "platform engineer", "member of technical staff",
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
        if any(q == m or (len(q) >= 4 and (q in m.split() or m in q)) for m in vocab):
            for m in members:
                if m not in terms:
                    terms.append(m)
    return terms


def _fts_term(term: str) -> str | None:
    """One search term as an FTS5 phrase. The last word is a prefix ("analyst" also finds "analysts")
    unless it's 3 letters or fewer, where a prefix would be too loose ("ai" would find "aid")."""
    words = re.findall(r"\w+", term.lower())
    if not words:
        return None
    phrase = '"' + " ".join(words) + '"'
    return phrase + "*" if len(words[-1]) > 3 else phrase


def fts_all_words(q: str) -> str | None:
    """FTS5 query: the title has every word of q, in any order."""
    parts = [_fts_term(w) for w in re.findall(r"\w+", q.lower()) if w not in STOPWORDS]
    parts = [p for p in parts if p]
    return "title : (" + " AND ".join(parts) + ")" if parts else None


def fts_any_term(terms: list[str]) -> str | None:
    """FTS5 query: the title contains any of the terms (phrases)."""
    parts = [p for p in map(_fts_term, terms) if p]
    return "title : (" + " OR ".join(parts) + ")" if parts else None


def fts_company(q: str) -> str | None:
    phrase = _fts_term(q)
    return f"company_name : {phrase}" if phrase else None


STOPWORDS = {"and", "or", "of", "the", "a", "an", "in", "for", "to", "not", "near"}
