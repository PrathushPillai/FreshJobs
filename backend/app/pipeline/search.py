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
