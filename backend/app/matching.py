"""How well a resume fits a job: a 0-100 score built from explainable parts, never a made-up number.

Parts and weights (WEIGHTS):
- skills (35%): of the skills the job asks for (SKILLS vocabulary found in its description), how many the
  resume has. The matched and missing ones are returned, so the user sees why.
- fit (30%): semantic similarity between the resume and the job (title + description), from a local
  embedding model (BAAI/bge-small-en-v1.5 via fastembed, CPU, no API cost; the resume never leaves the server).
- role (15%): the job's title is one of the user's target roles (1.0), a related role (0.6), or neither.
- experience (15%): years the job asks for (Job.min_years) against the user's years.
- education (5%): a degree the job says is *required* (PhD / Master's) against the user's.
Then the user's situation: a job that rules out sponsorship, for someone who needs it, is scaled down hard
(SPONSOR_PENALTY); a job welcoming OPT/CPT gets a small boost; one outside the preferred places is scaled down.
"""

import io
import math
import re
import threading
import zipfile
from datetime import date
from pathlib import Path

import numpy as np

from app.config import settings

WEIGHTS = {"skills": 0.35, "fit": 0.30, "role": 0.15, "experience": 0.15, "education": 0.05}
SPONSOR_PENALTY = 0.55  # a job that won't sponsor, for someone who needs sponsorship
OPT_WELCOME_BONUS = 4
OUTSIDE_LOCATIONS = 0.85
UNDESCRIBED_CAP = 70  # a job with no description is scored from its title, so at most "good"
# Cosine similarity of bge-small embeddings for (resume, job), measured on real jobs for a data-engineer resume:
# unrelated roles (nurse, sales, accountant) 0.55-0.59, neighbouring roles (analyst, SWE) ~0.66, the same role
# 0.70-0.75. Mapped linearly onto 0..1 between these.
FIT_LOW, FIT_HIGH = 0.57, 0.76

# ---------- skills ----------

# Canonical skill -> regex alternatives (matched case-insensitively on word boundaries unless noted).
SKILLS: dict[str, list[str]] = {
    # languages
    "Python": [r"python"], "Java": [r"java(?!\s*script)"], "JavaScript": [r"javascript", r"\bjs\b", r"ecmascript"],
    "TypeScript": [r"typescript"], "C++": [r"c\+\+", r"cpp"], "C#": [r"c#", r"\.net\s+c#"], "Go": [r"golang"],
    "Rust": [r"rust"], "Scala": [r"scala"], "Kotlin": [r"kotlin"], "Swift": [], "Ruby": [r"ruby"],
    "PHP": [r"php"], "SQL": [r"sql(?!\s*server)", r"t-sql", r"pl/sql"], "Bash": [r"bash", r"shell scripting"],
    "MATLAB": [r"matlab"], "SAS": [r"\bsas\b"], "Julia": [r"julia"],
    # data stores
    "PostgreSQL": [r"postgres(?:ql)?"], "MySQL": [r"mysql"], "SQL Server": [r"sql server", r"mssql"],
    "Oracle Database": [r"oracle (?:database|db|sql)"], "MongoDB": [r"mongo(?:db)?"], "Redis": [r"redis"],
    "Cassandra": [r"cassandra"], "DynamoDB": [r"dynamo ?db"], "Elasticsearch": [r"elastic ?search", r"opensearch"],
    "Snowflake": [r"snowflake"], "BigQuery": [r"big ?query"], "Redshift": [r"redshift"], "Databricks": [r"databricks"],
    "NoSQL": [r"no-?sql"], "Neo4j": [r"neo4j", r"graph database"],
    # data engineering
    "Spark": [r"spark", r"pyspark"], "Hadoop": [r"hadoop", r"hdfs"], "Hive": [r"\bhive\b"], "Kafka": [r"kafka"],
    "Airflow": [r"airflow"], "dbt": [r"\bdbt\b"], "ETL": [r"etl", r"elt", r"data pipelines?"], "Flink": [r"flink"],
    "Data Warehousing": [r"data warehous\w*"], "Data Modeling": [r"data model(?:l)?ing", r"dimensional model\w*"],
    "Data Lake": [r"data ?lake\w*", r"lakehouse"], "Delta Lake": [r"delta lake"], "Informatica": [r"informatica"],
    "SSIS": [r"ssis"], "Fivetran": [r"fivetran"], "Kinesis": [r"kinesis"],
    # analytics / BI
    "Tableau": [r"tableau"], "Power BI": [r"power ?bi"], "Looker": [r"looker"], "Excel": [r"excel"],
    "Statistics": [r"statistic(?:s|al)"], "A/B Testing": [r"a/b test\w*", r"experimentation"],
    "Data Visualization": [r"data visuali[sz]ation"], "Pandas": [r"pandas"], "NumPy": [r"numpy"],
    "R": [], "Google Analytics": [r"google analytics"], "Alteryx": [r"alteryx"], "SPSS": [r"spss"],
    # ML / AI
    "Machine Learning": [r"machine learning", r"\bml\b"], "Deep Learning": [r"deep learning", r"neural networks?"],
    "NLP": [r"\bnlp\b", r"natural language processing"], "Computer Vision": [r"computer vision"],
    "LLMs": [r"\bllms?\b", r"large language models?", r"generative ai", r"gen ?ai", r"prompt engineering"],
    "PyTorch": [r"pytorch", r"\btorch\b"], "TensorFlow": [r"tensorflow"], "Keras": [r"keras"],
    "scikit-learn": [r"scikit-?learn", r"sklearn"], "XGBoost": [r"xgboost", r"lightgbm"],
    "MLOps": [r"mlops", r"mlflow", r"kubeflow"], "Hugging Face": [r"hugging ?face", r"transformers library"],
    "RAG": [r"\brag\b", r"retrieval[- ]augmented"], "LangChain": [r"langchain", r"llamaindex"],
    "Forecasting": [r"forecasting", r"time[- ]series"],
    # cloud / devops
    "AWS": [r"\baws\b", r"amazon web services", r"\bs3\b", r"\bec2\b", r"lambda"], "Azure": [r"azure"],
    "GCP": [r"\bgcp\b", r"google cloud"], "Docker": [r"docker", r"containeri[sz]\w*"], "Kubernetes": [r"kubernetes", r"\bk8s\b"],
    "Terraform": [r"terraform", r"infrastructure as code", r"\biac\b"], "CI/CD": [r"ci/cd", r"continuous integration", r"github actions", r"jenkins", r"gitlab ci"],
    "Linux": [r"linux", r"unix"], "Git": [r"\bgit\b", r"github", r"gitlab", r"version control"],
    "Ansible": [r"ansible"], "Prometheus": [r"prometheus", r"grafana", r"datadog", r"observability"],
    # web / backend
    "React": [r"react(?:\.js|js)?(?!\w)", r"next\.js"], "Angular": [r"angular"], "Vue": [r"vue(?:\.js)?"],
    "Node.js": [r"node(?:\.js|js)", r"express\.js"], "Django": [r"django"], "Flask": [r"flask"], "FastAPI": [r"fastapi"],
    "Spring": [r"spring boot", r"\bspring\b"], ".NET": [r"\.net\b", r"asp\.net"], "HTML/CSS": [r"html5?", r"css3?", r"tailwind"],
    "REST APIs": [r"rest(?:ful)? apis?", r"\brest\b"], "GraphQL": [r"graphql"], "Microservices": [r"microservices?"],
    "Distributed Systems": [r"distributed systems?"], "System Design": [r"system design", r"scalable systems?"],
    "iOS": [r"\bios\b"], "Android": [r"android"], "Unity": [r"\bunity\b"],
    # practices / domain
    "Agile": [r"agile", r"scrum", r"kanban"], "Jira": [r"jira"], "Unit Testing": [r"unit test\w*", r"pytest", r"junit", r"test automation"],
    "Selenium": [r"selenium", r"cypress", r"playwright"], "Security": [r"cyber ?security", r"penetration testing", r"\bsiem\b", r"iam\b"],
    "Networking": [r"tcp/ip", r"networking", r"\bdns\b"], "SAP": [r"\bsap\b"], "Salesforce": [r"salesforce"],
    "Figma": [r"figma"], "Product Management": [r"product management", r"product roadmap"],
}
# Skills that need case or context: "R" alone is a letter, "Go" is a common word.
_CASE_SENSITIVE = {"Swift": re.compile(r"(?<![\w-])Swift(?:UI)?(?![\w-])(?!\s*(?:code|response|action|pace))"),
                   "R": re.compile(r"(?<![\w/])R(?![\w/&])(?:\s*(?:programming|language|studio)|(?=\s*[,/;)]))"),
                   "Go": re.compile(r"(?<![\w-])Go(?:lang)?(?=\s*[,/;)]|\s+(?:programming|language|developer))")}
# Skills most jobs and resumes mention: a match on them says little about fit.
GENERIC_SKILLS = {"Excel", "Agile", "Jira", "Git", "Linux", "Statistics", "Data Visualization", "Unit Testing", "Networking"}
GENERIC_WEIGHT = 0.3
_SKILL_RX = {name: re.compile(r"(?<![\w+#.])(?:" + "|".join(alts) + r")(?![\w+#])", re.I) for name, alts in SKILLS.items() if alts}


def extract_skills(text: str) -> list[str]:
    """Canonical skills mentioned in a text, in SKILLS order."""
    text = text or ""
    found = [name for name, rx in _SKILL_RX.items() if rx.search(text)]
    found += [name for name, rx in _CASE_SENSITIVE.items() if rx.search(text) and name not in found]
    return [name for name in SKILLS if name in found]


# ---------- resume reading ----------

# Normal English text is ~15% spaces. Some PDFs (fonts without character widths, as some resume builders
# export) come out with words run together: "DataEngineerwithhands-onexperience" (a real resume: 2.6%).
# Skills still match inside run-on text, but titles, dates and the fit embedding don't.
WELL_SPACED = 0.08


def spacing(text: str) -> float:
    """Share of spaces among the text's characters (line breaks not counted)."""
    chars = (text or "").replace("\n", "")
    return chars.count(" ") / len(chars) if chars else 0.0


# Putting spaces back. The word list (data/wordfreq.txt) is ranked by frequency in 40,000 job descriptions, so
# it knows tech words ("snowflake", "dbt", "kubernetes") as well as English. Each run of letters is split into
# the likeliest words by dynamic programming over Zipf costs (cost of the word ranked r = log(r * log N)), with
# an option to keep an unknown stretch whole (names, rare terms) so "Prathush" isn't cut into pieces.
_WORDFREQ = Path(__file__).resolve().parent.parent / "data" / "wordfreq.txt"
_COSTS: dict[str, float] | None = None
_MAX_WORD = 24
_UNKNOWN_BASE, _UNKNOWN_PER_CHAR = 18.0, 1.5


def _word_costs() -> dict[str, float]:
    global _COSTS
    if _COSTS is None:
        words = [w.strip() for w in _WORDFREQ.read_text(encoding="utf-8").splitlines() if w.strip() and not w.startswith("#")]
        log_n = math.log(len(words))
        costs = {w: math.log((i + 1) * log_n) for i, w in enumerate(words)}
        # Skill names and title words count as common, so they're never split ("databricks", "analyst").
        for name in list(SKILLS) + _TITLE_NOUNS.split("|"):
            for w in re.findall(r"[a-z]+", name.lower()):
                costs[w] = min(costs.get(w, 99.0), math.log(1000 * log_n))
        _COSTS = costs
    return _COSTS


def _segment(run: str) -> list[str]:
    """The likeliest words in a run of letters, keeping its capitals: "DataEngineerwith" -> Data, Engineer, with."""
    costs, low, n = _word_costs(), run.lower(), len(run)
    best = [0.0] + [math.inf] * n
    back = [0] * (n + 1)
    for end in range(1, n + 1):
        for start in range(max(0, end - _MAX_WORD), end):
            piece = low[start:end]
            c = costs.get(piece)
            if c is None:
                c = _UNKNOWN_BASE + _UNKNOWN_PER_CHAR * len(piece)
            if best[start] + c < best[end]:
                best[end], back[end] = best[start] + c, start
    words, end = [], n
    while end > 0:
        words.append(run[back[end]:end])
        end = back[end]
    return words[::-1]


def respace(text: str) -> str:
    """Put spaces back into run-together text (see WELL_SPACED). Text that's normally spaced is returned as is."""
    if not text or spacing(text) >= WELL_SPACED:
        return text
    costs = _word_costs()
    out_lines = []
    for line in text.splitlines():
        line = re.sub(r"(?<=[A-Za-z])(?=(?:19|20)\d\d\b)", " ", line)  # "Jan2024" -> "Jan 2024"
        line = re.sub(r"([,;:|])(?=[A-Za-z0-9(])", r"\1 ", line)  # "Python,SQL" -> "Python, SQL"
        line = re.sub(r"(?<=[a-z])\((?=[A-Za-z])", " (", line)  # "pipeline(AWS" -> "pipeline (AWS"
        pieces = []
        for part in re.split(r"([A-Za-z]+)", line):
            if len(part) >= 4 and part.isalpha() and part.lower() not in costs:
                pieces.append(" ".join(_segment(part)))
            else:
                pieces.append(part)
        out_lines.append(re.sub(r" {2,}", " ", "".join(pieces)).strip())
    return "\n".join(out_lines)


def spacing_warning(text: str) -> str | None:
    """A note for the profile page when the resume's text is still run together after respace(): no action asked,
    just where to look."""
    if text and spacing(text) < WELL_SPACED:
        # Only text respace() couldn't fix reaches here (respace runs on every upload and on saved resumes).
        return ("Part of this file couldn't be read cleanly, so your scores rely more on the skills, years and roles "
                "below than on the resume's wording. They're worth a quick look: anything you correct updates your scores.")
    return None


def _pdf_text(data: bytes) -> str:
    """A PDF's text, from pypdf's plain reading or, when that runs words together, its layout reading
    (which places words by position): whichever keeps more of the spacing."""
    from pypdf import PdfReader

    pages = PdfReader(io.BytesIO(data)).pages
    plain = "\n".join(page.extract_text() or "" for page in pages).strip()
    if spacing(plain) >= WELL_SPACED:
        return plain
    try:
        layout = "\n".join(page.extract_text(extraction_mode="layout") or "" for page in pages)
    except Exception:  # layout mode is newer and stricter about odd PDFs
        return plain
    layout = re.sub(r"[ \t]{2,}", " ", layout)  # it pads columns with runs of spaces
    layout = "\n".join(line.strip() for line in layout.splitlines() if line.strip())
    return layout if spacing(layout) > spacing(plain) else plain


def resume_text(filename: str, data: bytes) -> str:
    """Plain text of an uploaded resume: PDF, Word (.docx) or plain text."""
    name = (filename or "").lower()
    if name.endswith(".pdf") or data[:4] == b"%PDF":
        return _pdf_text(data)
    if name.endswith(".docx") or data[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            xml = z.read("word/document.xml").decode("utf-8", "ignore")
        xml = re.sub(r"</w:p>|<w:br/>|<w:tab/>", "\n", xml)
        text = re.sub(r"<[^>]+>", "", xml)
        return re.sub(r"[ \t]+", " ", re.sub(r"&amp;", "&", re.sub(r"&lt;", "<", re.sub(r"&gt;", ">", text)))).strip()
    return data.decode("utf-8", "ignore").strip()


_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_DATE = r"(?:(?P<{p}m>jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+|(?P<{p}n>\d{{1,2}})/)?(?P<{p}y>(?:19|20)\d\d)"
_RANGE = re.compile(_DATE.format(p="a") + r"\s*(?:-|–|—|to)\s*(?:" + _DATE.format(p="b") + r"|(?P<now>present|current|now|today))", re.I)
_EDUCATION_LINE = re.compile(r"universit|college|institute|school|bachelor|master|b\.?s\.?\b|m\.?s\.?\b|ph\.?d|degree|gpa|coursework|\bbsn\b|\bmsn\b|\bmba\b|diploma|\bb\.?a\.?\b|\bb\.?tech\b|\bm\.?tech\b", re.I)
_YEARS_STATED = re.compile(r"(\d{1,2})\+?\s*(?:years?|yrs?)\s+(?:of\s+)?(?:professional\s+|industry\s+|work\s+)?experience", re.I)


def _month(m: str | None, n: str | None) -> int:
    if m:
        return _MONTHS.get(m[:3].lower(), 1)
    return int(n) if n and 1 <= int(n) <= 12 else 1


def resume_years(text: str, today: date | None = None) -> float | None:
    """Years of work experience: the union of the date ranges outside education lines, or a stated
    "N years of experience". An estimate the user can correct on the profile page."""
    today = today or date.today()
    spans = []
    for line in (text or "").splitlines():
        if _EDUCATION_LINE.search(line):
            continue
        for m in _RANGE.finditer(line):
            start = int(m["ay"]) * 12 + _month(m["am"], m["an"]) - 1
            if m["now"]:
                end = today.year * 12 + today.month - 1
            else:
                end = int(m["by"]) * 12 + _month(m["bm"], m["bn"]) - 1
            if 0 <= end - start <= 12 * 40:
                spans.append((start, end + 1))
    months = 0
    for start, end in sorted(spans):  # union of overlapping roles
        if months and start < last_end:
            if end > last_end:
                months += end - last_end
                last_end = end
            continue
        months += end - start
        last_end = end
    from_dates = round(months / 12, 1) if months else None
    stated = [int(n) for n in _YEARS_STATED.findall(text or "")]
    if stated and (from_dates is None or max(stated) > from_dates):
        return float(max(stated))
    return from_dates


def resume_degree(text: str) -> str:
    t = text or ""
    if re.search(r"\bph\.?\s?d\b|doctor of philosophy|doctorate", t, re.I):
        return "phd"
    if re.search(r"\bmaster'?s?\b|\bm\.?\s?s\.?\s+(?:in|of)\b|\bmba\b|\bm\.?eng\b|\bm\.?tech\b|\bms\b", t, re.I):
        return "masters"
    if re.search(r"\bbachelor'?s?\b|\bb\.?\s?s\.?\s+(?:in|of)\b|\bb\.?tech\b|\bb\.?e\.?\b|\bb\.?a\.?\s+(?:in|of)\b|\bbs\b", t, re.I):
        return "bachelors"
    return ""


def required_degree(description: str) -> str:
    """A degree the job says is required (not just preferred)."""
    t = description or ""
    if re.search(r"ph\.?\s?d\.?[^.\n]{0,60}\brequired|required[^.\n]{0,40}ph\.?\s?d", t, re.I):
        return "phd"
    if re.search(r"master'?s[^.\n]{0,60}\brequired|required[^.\n]{0,40}master'?s", t, re.I):
        return "masters"
    return ""


# ---------- embeddings ----------

_model = None
_model_lock = threading.Lock()


def _models_dir() -> str:
    if settings.models_dir:
        return settings.models_dir
    url = settings.database_url
    if url.startswith("sqlite:///") and ":memory:" not in url:
        return str(Path(url.removeprefix("sqlite:///")).parent / "models")
    return str(Path(__file__).resolve().parent.parent / "data" / "models")


def model():
    """The embedding model, loaded once per process (about 3 s; 65 MB on disk)."""
    global _model
    with _model_lock:
        if _model is None:
            from fastembed import TextEmbedding

            _model = TextEmbedding("BAAI/bge-small-en-v1.5", cache_dir=_models_dir(), threads=settings.embed_threads)
        return _model


def embed(texts: list[str]) -> np.ndarray:
    """Normalized float32 vectors, one row per text."""
    vectors = np.array(list(model().embed(texts, batch_size=32)), dtype=np.float32)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return vectors / np.where(norms == 0, 1, norms)


def job_text(title: str, description: str) -> str:
    # Title and the description's opening (what the role is, the main asks). 2,000 characters embedded at
    # ~1.6 jobs/s on this laptop under load; 1,000 is about 3x faster and keeps the ranking.
    return f"{title}. {(description or '')[:1000]}"


def resume_for_embedding(text: str) -> str:
    # bge-small reads ~512 tokens: the start of a resume (summary, latest roles, skills) carries the most signal.
    return (text or "")[:2500]


# ---------- the score ----------

def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def score(profile: dict, job: dict, fit_cosine: float | None) -> dict:
    """The match of one job. `profile`: skills, years, degree, target_roles, needs_sponsorship, locations.
    `job`: title, description, min_years, no_sponsor, student_visa, places (list of (state, city)), remote,
    role_family_terms (titles related to the target roles). Returns score 0-100 and its explanation."""
    from app.pipeline.filters import role_matches

    my_skills = set(profile.get("skills") or [])
    job_skills = extract_skills(job.get("description") or "") or extract_skills(job.get("title") or "")
    matched = [s for s in job_skills if s in my_skills]
    missing = [s for s in job_skills if s not in my_skills]
    notes = []

    fit_part = _clamp((fit_cosine - FIT_LOW) / (FIT_HIGH - FIT_LOW)) if fit_cosine is not None else 0.5
    # Skills: weighted coverage of what the job asks for. Generic skills (Excel, Agile…) count 0.3. A job naming
    # little the vocabulary knows (a nurse posting that mentions Excel) leans on the overall fit instead:
    # one generic match out of one scored a nurse job 55/100 on skills for a data resume.
    total_w = sum(GENERIC_WEIGHT if s in GENERIC_SKILLS else 1.0 for s in job_skills)
    match_w = sum(GENERIC_WEIGHT if s in GENERIC_SKILLS else 1.0 for s in matched)
    coverage = match_w / total_w if total_w else 0.0
    confidence = min(1.0, total_w / 3)
    skills_part = confidence * coverage + (1 - confidence) * fit_part

    title = job.get("title") or ""
    targets = profile.get("target_roles") or []
    if targets and role_matches(title, targets, []):
        role_part = 1.0
    elif job.get("related_role"):
        role_part = 0.6
    else:
        role_part = 0.2 if targets else 0.5

    need, have = job.get("min_years"), profile.get("years")
    if need is None or have is None:
        exp_part = 0.75
    elif have >= need:
        exp_part = 1.0 if have - need <= 8 else 0.85  # far over-qualified: still fine, slightly lower
    else:
        exp_part = _clamp(1 - 0.3 * (need - have))
        notes.append(f"asks {need}+ years; you have {have:g}")

    req = required_degree(job.get("description") or "")
    rank = {"": 0, "bachelors": 1, "masters": 2, "phd": 3}
    if req and rank.get(profile.get("degree") or "", 0) < rank[req]:
        edu_part = 0.2
        notes.append(f"requires a {'PhD' if req == 'phd' else 'Master’s'}")
    else:
        edu_part = 1.0

    parts = {"skills": skills_part, "fit": fit_part, "role": role_part, "experience": exp_part, "education": edu_part}
    total = sum(WEIGHTS[k] * v for k, v in parts.items()) * 100

    if profile.get("needs_sponsorship", True) and job.get("no_sponsor"):
        total *= SPONSOR_PENALTY
        notes.append("rules out sponsorship")
    elif job.get("student_visa") == "welcome":
        total += OPT_WELCOME_BONUS
        notes.append("welcomes OPT/CPT")
    wanted = [w.lower() for w in profile.get("locations") or []]
    if wanted and not job.get("remote"):
        here = [p.lower() for place in job.get("places") or [] for p in place if p]
        if here and not any(w in here for w in wanted):
            total *= OUTSIDE_LOCATIONS
            notes.append("outside your preferred locations")

    if len((job.get("description") or "").strip()) < 100:
        # Only a title to go on: no skills, years or degree to check, so never a "strong" match.
        total = min(total, UNDESCRIBED_CAP)
        notes.append("no description yet: scored from the title only")

    return {
        "score": int(round(_clamp(total / 100) * 100)),
        "parts": {k: round(v * 100) for k, v in parts.items()},
        "matched": matched,
        "missing": missing,
        "skills_asked": len(job_skills),
        "notes": notes,
    }


# A job title as resumes write it: up to three words before a title noun ("Senior Data Engineer Intern",
# "Registered Nurse", "Machine Learning Engineer"), at the start of a line or after a separator, as
# resumes write titles; "worked with the product manager" isn't a role held. Lowercased text.
_TITLE_NOUNS = ("engineer|developer|analyst|scientist|nurse|designer|accountant|consultant|architect|administrator|"
                "manager|specialist|technician|researcher|programmer|therapist|pharmacist|teacher|recruiter|"
                "coordinator|associate|representative|executive|strategist|economist|statistician|physician")
_TITLE_RE = re.compile(r"(?:^|[,|:;()•–—-])[ \t]*((?:[a-z][a-z+#./&-]*[ \t]+){0,3})(" + _TITLE_NOUNS + r")s?(?![a-z])", re.M)
_TITLE_DROP = {  # leading words that aren't part of the title
    "the", "a", "an", "as", "and", "or", "with", "for", "of", "to", "our", "my", "by", "at", "in", "on", "worked",
    "senior", "sr", "junior", "jr", "lead", "staff", "principal", "intern", "internship", "i", "ii", "iii",
    "experience", "experienced", "skilled", "certified", "licensed", "graduate", "entry", "level", "former",
}


def resume_roles(text: str, limit: int = 4) -> list[str]:
    """The job titles the resume names (headline and roles held), the most mentioned first, earliest first on a
    tie (the headline): "Frontend Developer", "Registered Nurse". Seniority words and "intern" are dropped;
    skill names alone ("power bi") are never a role. Offered as target roles."""
    lowered = (text or "").lower()
    skill_names = {k.lower() for k in SKILLS}
    counts: dict[str, int] = {}
    first: dict[str, int] = {}
    for m in _TITLE_RE.finditer(lowered):
        words = m.group(1).split()
        # Keep the words right before the noun, up to the first one that isn't part of a title.
        kept: list[str] = []
        for w in reversed(words):
            w = w.strip(".,;:()/&-")
            if not w or w in _TITLE_DROP or not re.fullmatch(r"[a-z][a-z+#.-]*", w):
                break
            kept.insert(0, w)
        if not kept:
            continue  # a bare "engineer" or "manager" says nothing about the field
        title = " ".join(kept + [m.group(2)])
        if title in skill_names:
            continue
        counts[title] = counts.get(title, 0) + 1
        first.setdefault(title, m.start())
    ranked = sorted(counts, key=lambda t: (-counts[t], first[t]))
    picked: list[str] = []
    for t in ranked:
        if not any(t in p or p in t for p in picked):  # "data engineer" and "big data engineer": keep one
            picked.append(t)
    return picked[:limit]
