"""Is a job an IT / tech role? From its title alone, into three groups (2026-11-19).

FreshJobs is an IT job feed (the user's decision, 2026-11-19): software, data, AI/ML, cloud and infrastructure,
security, IT support and the like, at any employer (a software engineer at a retailer's head office counts).

- "tech": clearly an IT role ("Senior Software Engineer", "Data Analyst", "IT Support Specialist").
- "edge": could be IT, or tech-adjacent: kept. Plain "Engineer" / "Senior Engineer" / "Analyst" / "Technical Lead"
  (software at a startup, something else at a manufacturer), product and project managers, business analysts, UX/UI,
  solutions and systems engineers, electrical and hardware engineers.
- "other": clearly not IT (store, field, clinical, trades, finance...): the only group that may be dropped.

The rule is lopsided on purpose: dropping a real IT job is worse than keeping a stray one (the user, 2026-11-19:
"please don't leave out real IT jobs"). So a title is "other" only when nothing in it suggests IT, or when the
IT-looking word is qualified away: "Mechanical Engineer", "Financial Analyst", "Security Officer", "Data Entry Clerk",
"CT Technologist". The first draft missed "Forward Deployed Engineer", "Member of Technical Staff", "Founding
Engineer", "Data Center Technician" and "USA_Developer" (an underscore defeated \\w boundaries), found by listing
the commonest "other" titles with tech-ish words.

Word boundaries are letters and digits only, (?<![a-z0-9]) / (?![a-z0-9]), so "_" and "-" separate words; never \\b
(tests/test_source_files.py: a \\b written through a shell became a backspace byte once).
"""

import re


def _words(*patterns: str) -> re.Pattern:
    return re.compile("|".join(f"(?<![a-z0-9])(?:{p})(?![a-z0-9])" for p in patterns), re.I)


# IT titles that contain a word _NOT_TECH would otherwise catch ("Information System Security Officer"): checked
# before it.
_TECH_FIRST = _words(r"e-?discovery", r"litigation support",  # "eDiscovery Litigation Technologist" is legal IT
                     r"information (?:systems? )?security", r"isso", r"issm", r"chief information security officer",
                     r"ciso")

# Look tech, aren't: checked next.
_NOT_TECH = _words(
    r"data entry", r"data collect(?:or|ion) (?:driver|specialist|associate|technician)", r"data collector",
    r"mapping data collection", r"field data collection", r"retail data", r"security (?:officer|guard|patrol|supervisor|"
    r"agent|screener|professional|shift|manager|associate|specialist)", r"armed|unarmed", r"network marketing",
    r"(?:qa|quality) (?:inspector|technician|auditor|control)", r"cable (?:technician|installer)",
    r"loss prevention", r"medical (?:coder|coding)", r"technologist", r"med tech", r"radiology tech",
    r"(?:product|event) demonstrat\w*", r"retail display installer",
)

_TECH = _words(
    # software
    r"software", r"developer", r"programmer", r"programming", r"coder", r"full[ -]?stack",
    r"(?:front|back)[ -]?end (?:engineer|developer|dev|software|web|ui)", r"(?:front|back)end", r"web (?:engineer|developer|designer)", r"ios", r"android", r"mobile (?:engineer|developer)",
    r"devops", r"devsecops", r"sre", r"site reliability", r"platform engineer(?:ing)?", r"sdet", r"sde", r"swe",
    r"test automation", r"automation engineer", r"tosca", r"selenium", r"cypress", r"playwright",
    r"service reliability", r"software quality",
    # second fresh sample (2026-11-19): middleware admins, in-car software, quant research
    r"was admin", r"websphere", r"weblogic", r"jboss", r"tomcat", r"middleware", r"infotainment",
    r"quantitative research(?:er)?",
    # enterprise software products (consultants, admins and developers on them are IT)
    r"tririga", r"maximo", r"guidewire", r"pega(?:systems)?", r"mulesoft", r"informatica", r"appian",
    r"outsystems", r"kofax", r"epic (?:analyst|consultant|application|certified)", r"cerner", r"murex",
    r"tibco", r"boomi", r"snowflake", r"databricks", r"splunk", r"okta", r"(?:software|qa) (?:test|tester|testing|qa)", r"qa engineer",
    r"quality assurance (?:engineer|analyst)", r"application(?:s)? (?:developer|engineer|architect|support|analyst)",
    r"api", r"java", r"python", r"\.net", r"c\+\+", r"c#", r"golang", r"rust (?:developer|engineer)",
    r"ruby (?:on rails|developer|engineer)", r"php", r"javascript", r"typescript", r"react", r"angular",
    r"node\.?js", r"scala", r"kotlin", r"swift developer", r"firmware", r"embedded",
    r"game (?:developer|engineer|programmer)", r"forward[ -]deployed", r"member of (?:the )?(?:technical|data) staff",
    r"mts", r"founding (?:engineer|developer|data)", r"integration(?:s)? (?:engineer|developer|specialist|analyst)",
    r"(?:product|customer|technical|application|production|it) support engineer", r"support engineer",
    r"tech(?:nical)? lead", r"engineering (?:manager|lead|director)", r"(?:head|director|vp|vice president) of "
    r"engineering", r"knowledge engineer",
    r"(?:cloud|it|software|data|platform|network|systems?|compute|ai|ml|core|production) infrastructure",
    r"infrastructure (?:engineer|engineering|software|architect|platform|automation|operations)",
    # data and AI
    r"data (?:scientist|science|engineer|engineering|analyst|analytics|architect|modeler|modeller|warehouse|"
    r"platform|governance|steward|quality|management|integration|specialist|visualization|operations|center|"
    r"centre|lead|manager|product|infrastructure|annotation|labeling|labelling|associate)",
    r"analytics", r"business intelligence", r"bi (?:developer|analyst|engineer)", r"power bi", r"tableau", r"etl",
    r"big data", r"machine learning", r"ml", r"mlops", r"deep learning", r"artificial intelligence", r"ai",
    r"llm", r"large language model", r"nlp", r"computer vision", r"generative ai", r"genai", r"prompt engineer",
    r"quantitative (?:developer|researcher|analyst)", r"statistician", r"research scientist", r"bioinformatic\w*",
    r"cheminformatic\w*",
    # infrastructure and cloud
    r"cloud", r"aws", r"azure", r"gcp", r"kubernetes", r"network (?:engineer|administrator|architect|analyst|"
    r"technician|specialist|operations)", r"(?:systems?|linux|windows|unix) administrator", r"sysadmin",
    r"system(?:s)? (?:analyst|integrator)", r"database", r"dba", r"sql", r"storage engineer", r"virtualization",
    r"vmware", r"mainframe", r"telecom(?:munications)? engineer", r"linux", r"unix",
    # security
    r"cyber ?security", r"cyber", r"information security", r"infosec", r"security (?:engineer|architect|analyst|"
    r"operations|researcher|consultant)", r"soc analyst", r"penetration tester", r"pen tester", r"appsec",
    r"identity (?:and|&) access", r"iam (?:engineer|analyst)", r"threat (?:intelligence|hunter)",
    # IT and enterprise systems
    r"information technology", r"help ?desk", r"service desk", r"desktop support", r"technical support",
    r"tech support", r"computer", r"technology (?:analyst|consultant|specialist|manager|lead)",
    r"salesforce", r"servicenow", r"sap", r"oracle (?:developer|dba|consultant|analyst)", r"workday (?:analyst|"
    r"consultant|integration|developer|hcm)", r"erp", r"crm (?:developer|administrator|analyst)",
    r"dynamics 365", r"sharepoint", r"blockchain", r"robotics software", r"(?:cloud|solutions?|technical|"
    r"enterprise|software|data|security|network|ai|ml) architect", r"chief (?:technology|information|data) officer",
    r"cto", r"cio",
)

# An engineer, analyst or designer of something that isn't IT: "other", even though the bare word would be kept.
_NON_IT_KIND = _words(
    r"(?:mechanical|civil|structural|manufacturing|process|industrial|chemical|geotechnical|environmental|"
    r"facilit(?:y|ies)|building|maintenance|safety|commissioning|construction|field service|service|stationary|"
    r"operating|plant|packaging|piping|hvac|petroleum|mining|nuclear|biomedical|clinical|supplier quality|"
    r"quality|validation|reliability|equipment|production|traffic|transportation|water|wastewater|"
    r"highway|bridge|survey|cost|estimating|marine|aerospace|propulsion|materials|metallurgical|welding|"
    r"tooling|mold|resident|project|continuous improvement|lean|sound|audio|broadcast|elevator|boiler|"
    r"locomotive|rail|fire protection|energy|solar|wind|power plant|utility) (?:engineer\w*|designer|drafter|"
    r"technician)",
    r"(?:financial|finance|fp&a|credit|tax|treasury|payroll|pricing|accounting|compensation|benefits|procurement|"
    r"purchasing|supply chain|logistics|inventory|audit|compliance|marketing|sales operations|sales|"
    r"behavior|behaviour|bcba|claims|underwriting|loan|investment|equity research|risk|fraud|kyc|aml|"
    r"client relationship|operations|merchandise|merchandising|planning|demand|budget|grants|policy|"
    r"intelligence|crime|research|hr|people|workforce|labor|real estate|leasing|lease|title|mortgage) analyst",
    r"(?:graphic|interior|fashion|floral|kitchen|landscape|instructional|brand|visual|apparel|textile|jewelry|"
    r"mechanical|electrical|lighting|exhibit|set|costume|print|packaging) designer",
    r"(?:project|landscape|licensed|registered|design) architect", r"technical (?:recruiter|sales|trainer)",
    r"engineering (?:technician|intern|aide|assistant|operations technician)", r"(?:mechanical|civil|"
    r"electrical|chemical|industrial) engineering", r"quantity surveyor",
)

# Could be IT, and dropping a real IT job is the worse mistake: kept as "edge".
_EDGE = _words(
    r"engineer\w*", r"analyst", r"architect", r"scientist", r"technical", r"technology", r"systems?",
    r"designer", r"digital", r"automation", r"robotics", r"product (?:manager|owner|management|lead|director|"
    r"analyst|specialist)", r"head of product", r"program manager", r"project manager", r"scrum master",
    r"agile coach", r"ux", r"ui", r"user experience", r"user interface", r"pre-?sales", r"asic", r"fpga",
    r"implementation (?:specialist|consultant|manager)", r"operations research", r"actuar(?:y|ial)", r"quantitative",
    r"research", r"r&d", r"informatics", r"platform", r"tech", r"cpu", r"silicon", r"semiconductor design",
    r"design verification", r"verification",
)

# A title that mentions data or AI is never dropped (only data entry and data collection, in _NOT_TECH, are): the
# LinkedIn list of startups hiring for data (2026-11-19) had "Safety Engineer (Data)", "Data Partnerships" and
# "Data Acquisition Lead", which the engineer/analyst rules alone would have dropped.
_DATA_OR_AI = _words(r"data", r"ai", r"ml", r"llm", r"models?", r"algorithms?", r"gpu", r"compute")

# "IT" only in capitals: a lowercase "it" is the pronoun ("Make it happen").
_IT = re.compile(r"(?<![A-Za-z0-9])IT(?![A-Za-z0-9])")


def classify(title: str) -> str:
    """'tech', 'edge' or 'other' for a job title."""
    t = (title or "").strip()
    if not t:
        return "other"
    if _TECH_FIRST.search(t):
        return "tech"
    if _NOT_TECH.search(t):
        return "other"
    if _TECH.search(t) or _IT.search(t):
        return "tech"
    if _DATA_OR_AI.search(t):
        return "edge"
    if _NON_IT_KIND.search(t):
        return "other"
    if _EDGE.search(t):
        return "edge"
    return "other"


def keep(title: str) -> bool:
    """Whether an IT-only FreshJobs keeps this job: everything but clearly non-IT titles."""
    return classify(title) != "other"


# Tech signals in a description: tools and practices only IT work names. One alone isn't enough ("SQL a plus" in
# a finance job, "Python" in a lab job); two different ones are.
_DESC_SIGNALS = {
    "python": r"python", "java": r"java(?!\s*(?:cafe|coffee|house))", "javascript": r"javascript|typescript",
    "c-family": r"c\+\+|c#|golang|rust programming|scala|kotlin|swift(?:ui)?(?= development)",
    "sql": r"sql|postgres(?:ql)?|mysql|snowflake|bigquery|redshift|databricks", "web": r"react|angular|vue\.?js|"
    r"node\.?js|django|flask|spring boot|\.net|graphql|rest(?:ful)? apis?|api (?:design|development|integrations?)",
    "cloud": r"aws|azure|gcp|google cloud|cloud (?:platform|infrastructure|computing|services)",
    "containers": r"kubernetes|docker|terraform|ansible|helm", "ci": r"ci/cd|continuous integration|jenkins|github actions", "git": r"github|gitlab|bitbucket|git (?:workflows?|repositor\w*)|version control", "linux": r"linux|unix|bash|shell scripting",
    "software": r"software (?:development|engineering|design|architecture|lifecycle)|sdlc|code reviews?|"
    r"writing code|source code|codebase", "ml": r"machine learning|deep learning|pytorch|tensorflow|llms?|"
    r"large language models?|neural networks?|nlp|data science", "data": r"data (?:pipelines?|engineering|"
    r"warehous\w*|modeling|lake\w*)|etl|airflow|spark|kafka|dbt", "bi": r"tableau|power bi|looker",
    "security": r"cyber ?security|siem|penetration testing|vulnerability (?:management|assessment)|soc 2|"
    r"incident response|firewalls?", "it": r"active directory|help ?desk|ticketing|servicenow|jira|"
    r"network(?:ing)? (?:infrastructure|administration|protocols)|tcp/ip|vmware|office 365 administration",
    "erp": r"salesforce|sap|workday (?:hcm|integrations?)|oracle (?:ebs|fusion|cloud)|netsuite",
}
_DESC_PATTERNS = {k: re.compile(f"(?<![a-z0-9])(?:{p})(?![a-z0-9])", re.I) for k, p in _DESC_SIGNALS.items()}
DESC_SIGNALS_NEEDED = 2
# Signals that alone are enough: code in these is written for an IT job ("Automotive Infotainment", whose
# description named only Python, was dropped in the second hand-labelled sample, 2026-11-19).
_STRONG = {"python", "java", "javascript", "c-family", "web", "containers", "software"}


def description_signals(description: str) -> set[str]:
    """Which kinds of tech signal a description names (first 6,000 characters)."""
    text = (description or "")[:6000]
    return {k for k, p in _DESC_PATTERNS.items() if p.search(text)}


def classify_job(title: str, description: str = "") -> str:
    """classify() on the title, then a second look at the description for titles labelled "other": two different
    tech signals make it "edge" (kept). Vague titles ("Consultant", "Associate", "Rotational Program") hide IT jobs
    that only the description shows (2026-11-19, before any job is dropped)."""
    label = classify(title)
    if label == "other" and not _NOT_TECH.search((title or "").strip()):
        found = description_signals(description)
        if len(found) >= DESC_SIGNALS_NEEDED or found & _STRONG:
            return "edge"
    return label
