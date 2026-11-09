"""Which website a job's employer has, so the UI can show its logo (the site's icon).

No logo data is stored: the frontend asks Google's public favicon service for the domain this returns,
and falls back to the coloured initials when there's no real icon. A wrong logo is worse than none, so a
domain is only returned when it's clearly the employer's:
- the apply link is on the employer's own site (careers.mayoclinic.org -> mayoclinic.org), or
- the job board's name for the employer matches the employer's name (greenhouse.io/stripe for "Stripe",
  nvidia.wd5.myworkdayjobs.com for "NVIDIA" -> nvidia.com), or
- it's a well-known employer in KNOWN_DOMAINS.
"""

import re
from functools import lru_cache
from urllib.parse import urlparse

# Hosts of job boards, ATSs and aggregators: their icon isn't the employer's.
_BOARD_HOSTS = {
    "myworkdayjobs.com", "myworkdaysite.com", "workday.com", "applytojob.com", "jazz.co", "jazzhr.com", "workable.com",
    "smartrecruiters.com", "oraclecloud.com", "greenhouse.io", "icims.com", "ashbyhq.com", "themuse.com", "lever.co",
    "rippling.com", "rippling-ats.com", "eightfold.ai", "gem.com", "ziprecruiter.com", "remoteok.com", "remoteok.io",
    "remotive.com", "paylocity.com", "polymer.co", "bamboohr.com", "recruitee.com", "breezy.hr", "pinpointhq.com",
    "successfactors.com", "successfactors.eu", "sapsf.com", "sapsf.eu", "jobvite.com", "taleo.net", "ultipro.com",
    "ukg.com", "adp.com", "dayforcehcm.com", "ycombinator.com", "workatastartup.com", "linkedin.com", "indeed.com",
    "glassdoor.com", "adzuna.com", "simplify.jobs", "github.com", "google.com", "jobs.net", "careerbuilder.com",
    "monster.com", "dice.com", "builtin.com", "wellfound.com", "angel.co", "teamtailor.com", "personio.de",
    "personio.com", "comeet.com", "comeet.co", "hirebridge.com", "applicantpro.com", "paycomonline.net",
    "paycor.com", "hrmdirect.com", "hire.trakstar.com", "trakstar.com", "freshteam.com", "zohorecruit.com",
    "dover.com", "jobs.lever.co", "brassring.com", "avature.net", "phenompeople.com", "clearcompany.com",
}

# Two-part public suffixes, so careers.example.co.uk keeps "example.co.uk".
_TWO_PART_TLDS = {"co.uk", "org.uk", "ac.uk", "com.au", "co.in", "co.jp", "com.br", "co.nz", "com.sg", "com.mx", "co.za"}

# Employers whose board name doesn't match their site.
KNOWN_DOMAINS = {
    "amazon": "amazon.com", "amazon web services": "aws.amazon.com", "aws": "aws.amazon.com", "apple": "apple.com",
    "google": "google.com", "alphabet": "abc.xyz", "microsoft": "microsoft.com", "meta": "meta.com",
    "facebook": "meta.com", "netflix": "netflix.com", "nvidia": "nvidia.com", "jpmorgan chase": "jpmorganchase.com",
    "jpmorgan": "jpmorganchase.com", "jp morgan": "jpmorganchase.com", "goldman sachs": "goldmansachs.com",
    "morgan stanley": "morganstanley.com", "bank of america": "bankofamerica.com", "wells fargo": "wellsfargo.com",
    "capital one": "capitalone.com", "citi": "citi.com", "citigroup": "citi.com", "deloitte": "deloitte.com",
    "pwc": "pwc.com", "ey": "ey.com", "ernst young": "ey.com", "kpmg": "kpmg.com", "accenture": "accenture.com",
    "ibm": "ibm.com", "oracle": "oracle.com", "salesforce": "salesforce.com", "adobe": "adobe.com",
    "intel": "intel.com", "amd": "amd.com", "qualcomm": "qualcomm.com", "cisco": "cisco.com", "tesla": "tesla.com",
    "spacex": "spacex.com", "uber": "uber.com", "lyft": "lyft.com", "airbnb": "airbnb.com", "stripe": "stripe.com",
    "openai": "openai.com", "anthropic": "anthropic.com", "databricks": "databricks.com", "snowflake": "snowflake.com",
    "linkedin": "linkedin.com", "walmart": "walmart.com", "target": "target.com", "costco": "costco.com",
    "starbucks": "starbucks.com", "disney": "disney.com", "the walt disney company": "disney.com",
    "mayo clinic": "mayoclinic.org", "kaiser permanente": "kaiserpermanente.org", "cvs health": "cvshealth.com",
    "unitedhealth group": "unitedhealthgroup.com", "optum": "optum.com", "pfizer": "pfizer.com",
    "johnson johnson": "jnj.com", "lockheed martin": "lockheedmartin.com", "boeing": "boeing.com",
    "general motors": "gm.com", "ford": "ford.com", "dollar tree": "dollartree.com", "infosys": "infosys.com",
    "tata consultancy services": "tcs.com", "tcs": "tcs.com", "cognizant": "cognizant.com", "wipro": "wipro.com",
    "hcltech": "hcltech.com", "capgemini": "capgemini.com", "paypal": "paypal.com", "visa": "visa.com",
    "mastercard": "mastercard.com", "american express": "americanexpress.com", "intuit": "intuit.com",
    "servicenow": "servicenow.com", "workday": "workday.com", "vmware": "vmware.com", "dell": "dell.com",
    "dell technologies": "dell.com", "hp": "hp.com", "hpe": "hpe.com", "samsung": "samsung.com", "sony": "sony.com",
    "tiktok": "tiktok.com", "bytedance": "bytedance.com", "pinterest": "pinterest.com", "snap": "snap.com",
    "reddit": "reddit.com", "spotify": "spotify.com", "coinbase": "coinbase.com", "robinhood": "robinhood.com",
    "doordash": "doordash.com", "instacart": "instacart.com", "shopify": "shopify.com", "atlassian": "atlassian.com",
    "github": "github.com",
}

_LEGAL = {"inc", "incorporated", "llc", "ltd", "limited", "corp", "corporation", "co", "company", "the", "lp", "llp",
          "plc", "group", "holdings", "and", "us", "usa", "na"}
_SLUG_NOISE = re.compile(r"(careers?|jobs?|hiring|external|us|usa|global|inc|llc|\d+)$")


def _name_key(name: str) -> str:
    words = [w for w in re.findall(r"[a-z0-9]+", (name or "").lower().replace("&", " ")) if w not in _LEGAL]
    return " ".join(words)


def _squash(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _registrable(host: str) -> str:
    parts = host.lower().strip(".").split(".")
    if len(parts) >= 3 and ".".join(parts[-2:]) in _TWO_PART_TLDS:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def _slug_matches(slug: str, name: str) -> bool:
    """A job board's name for the employer ("stripe", "dollartree", "mayoclinic") is the employer's."""
    s = _squash(slug)
    for _ in range(2):  # "nvidiacareers" -> "nvidia", "acme2" -> "acme"
        s = _SLUG_NOISE.sub("", s) or s
    words = _name_key(name).split()
    if len(s) < 3 or not words:
        return False
    # The whole name, or its first two or more words ("dollartree" for "Dollar Tree Stores"). Not just the first
    # word of a longer name: "acme" for "Acme Robotics" would be acme.com, someone else's site.
    # The full name with its legal words counts too: "PilotCompany" is pilotcompany.com (pilot.com isn't them).
    return any(s == "".join(words[:k]) for k in range(2, len(words) + 1)) or s == "".join(words) or s == _squash(name)


@lru_cache(maxsize=20_000)
def logo_domain(url: str, ats: str, slug: str, company: str) -> str | None:
    known = KNOWN_DOMAINS.get(_name_key(company))
    if known:
        return known
    host = (urlparse(url or "").hostname or "").lower()
    if host:
        site = _registrable(host)
        if site not in _BOARD_HOSTS and not any(host.endswith("." + b) for b in _BOARD_HOSTS):
            return site  # the apply link is on the employer's own site
    board_name = (slug or "").split("|")[0]  # Workday slugs are "tenant|wdN|site"
    if ats == "oracle_hcm" and "|" in (slug or ""):
        board_name = slug.split("|")[1]  # "host|StaplesInc": the career site's name
    if ats == "workday" and host.endswith("myworkdayjobs.com"):
        board_name = host.split(".")[0]
    if ats in ("greenhouse", "lever", "ashby", "workday", "smartrecruiters", "workable", "recruitee", "breezy",
               "pinpoint", "rippling", "bamboohr", "gem", "polymer", "icims", "eightfold", "oracle_hcm", "successfactors"):
        board_name = re.sub(r"^(careers?|jobs?)-", "", board_name)  # iCIMS "careers-acme"
        if _slug_matches(board_name, company):
            return _squash(_SLUG_NOISE.sub("", _squash(board_name)) or board_name) + ".com"
    return None
