"""The AI visa reader: Claude reads what a posting says about work authorization and returns a structured verdict.

The rules in pipeline/sponsorship.py catch the common wordings, and miss the unusual ones ("must be able to work
for any employer without restriction", a clearance hidden in a benefits list). Here a model reads only the sentences
about work authorization (`passages`) of new jobs that match someone's roles, through the Message Batches API (half
price, results within the hour), and answers in a fixed JSON shape with the sentence it relied on as evidence.

How a verdict is used (`apply_verdict`, also run by poller.apply_flags):
- it can add a "rules out sponsorship" flag (`no_sponsor`) or a "says it sponsors" flag (`sponsors_text`), and an
  OPT/CPT verdict (`student_visa`) where the rules found none;
- it never clears a flag the rules set: a rule's match is a quoted phrase, and the eval measures disagreements first.

Hourly with discovery (`main._upkeep` -> `run_once`): collect finished batches, then send the next one.
    python -m app.pipeline.visa_reader --status | --submit | --collect
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import settings

log = logging.getLogger(__name__)

PROMPT_VERSION = 2
MAX_PASSAGE_CHARS = 3000
MAX_TOKENS = 2048  # thinking counts toward it; the JSON answer itself is ~150 tokens

# Sentences worth reading: what they say about who may legally take the job. Look-alikes found while labelling the
# eval set (2026-11-15) are left out: "opt out of texts" (OPT counts only in capitals), "company-sponsored 401(k)",
# "sponsors of all levels", "citizen developers", "citizenship status" in equal-opportunity notices.
_AUTH = re.compile(
    r"(?i:(?<!company[- ])(?<!employer[- ])\bsponsor(s|ship|ships|ing)?\b(?!\s+(of|events?|training|certifications?|"
    r"team|plans?|benefits?|retirement)))|(?i:\bvisas?\b)|(?i:h-?1-?b)|\bOPT\b|\bCPT\b|(?i:stem opt|\bf-?1\b)|"
    r"(?i:green ?card|permanent resident|(u\.?s\.?|united states|us) citizen|citizenship(?! status)(?!, )|"
    r"citizens? only|authori[sz]ed to (work|be employed)|work authori[sz]ation|employment authori[sz]ation|"
    r"eligib(le|ility) to work|legally (eligible|able|authori[sz]ed)|without restriction|immigration|"
    r"security clearance|clearance:|(secret|ts/sci|top secret|dod|public trust) clearance|polygraph|\bitar\b|"
    r"export (control|administration|regulation)|u\.?s\.? persons?|international (students?|candidates?))"
)
_ABBREV = re.compile(r"(?<![A-Za-z])(?:U\.S\.A\.|U\.S\.|e\.g\.|i\.e\.|etc\.)", re.I)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+|\s*[•·▪◦]\s*")


def passages(description: str, limit: int = MAX_PASSAGE_CHARS) -> str:
    """The sentences of a posting about work authorization, each with the sentence before it (a list heading such as
    "This includes:" gives the next line its meaning), in their original order. Empty when there are none: nothing
    to read, no call made."""
    # "U.S.", "e.g." and "i.e." lose their dots first, or "Must be a U.S. citizen" splits in two.
    text = _ABBREV.sub(lambda m: m.group(0).replace(".", ""), description or "")
    sentences = [s.strip() for s in _SENTENCE_SPLIT.split(text) if s and s.strip()]
    keep: list[int] = []
    for i, s in enumerate(sentences):
        if _AUTH.search(s):
            for j in (i - 1, i):
                if j >= 0 and j not in keep:
                    keep.append(j)
    out, size = [], 0
    for i in sorted(keep):
        s = sentences[i]
        if size + len(s) > limit:
            break
        out.append(s)
        size += len(s) + 1
    return "\n".join(out)


def passage_hash(passage: str) -> str:
    return hashlib.sha1(f"{PROMPT_VERSION}|{passage}".encode()).hexdigest()


# ---- the request ------------------------------------------------------------------------------------------------

SYSTEM = """You read job postings for international students in the US (F-1 students on OPT or STEM OPT, and people \
who will need H-1B sponsorship). You are given the sentences of one posting that talk about work authorization. \
Decide what they say, strictly from the text.

sponsorship:
- "no": the employer will not sponsor a visa now or in the future, or only US citizens / permanent residents / \
  US persons / people with an active or obtainable US security clearance may apply, or candidates must be \
  authorized to work without sponsorship now and in the future.
- "yes": the posting says it sponsors visas or supports H-1B / immigration (and nothing rules it out).
- "unclear": it discusses work authorization but leaves sponsorship open (e.g. "must be authorized to work in the \
  US" with nothing about sponsorship, or conflicting statements).
- "not_mentioned": nothing about sponsorship or who may legally work.
A standard equal-opportunity notice that lists "citizenship status" or "national origin" as protected says nothing \
about sponsorship. "Security clearance not required" is not a restriction.

opt_cpt: "welcome" if F-1 OPT/STEM OPT/CPT candidates are explicitly invited, "excluded" if they are explicitly \
ruled out (including lists of visas that will not be sponsored or accepted), else "not_mentioned".

us_persons_only: true only when the posting restricts the job to US citizens, permanent residents, US persons \
(ITAR/EAR) or clearance holders.

clearance_required: true only when a US government security clearance (or eligibility for one) is required.

evidence: copy the one sentence (at most 300 characters) that most decides your answers, word for word from the \
text (the restriction, if there is one). Empty string only when the sentences say nothing about who may work."""

SCHEMA = {
    "type": "object",
    "properties": {
        "sponsorship": {"type": "string", "enum": ["yes", "no", "unclear", "not_mentioned"]},
        "opt_cpt": {"type": "string", "enum": ["welcome", "excluded", "not_mentioned"]},
        "us_persons_only": {"type": "boolean"},
        "clearance_required": {"type": "boolean"},
        "evidence": {"type": "string"},
    },
    "required": ["sponsorship", "opt_cpt", "us_persons_only", "clearance_required", "evidence"],
    "additionalProperties": False,
}


def request_params(title: str, company: str, passage: str, model: str | None = None) -> dict:
    """Messages API parameters for one posting (the same for a batch request and a direct call)."""
    return {
        "model": model or settings.visa_reader_model,
        "max_tokens": MAX_TOKENS,
        "system": [{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
        "output_config": {"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
        "messages": [{"role": "user", "content": f"Job title: {title}\nEmployer: {company}\n\nSentences:\n{passage}"}],
    }


def parse_message(message) -> dict | None:
    """The verdict from a response, or None (refused, cut off, or not the expected JSON)."""
    if getattr(message, "stop_reason", None) in ("refusal", "max_tokens"):
        return None
    raw = next((b.text for b in message.content if getattr(b, "type", "") == "text"), "")
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("sponsorship") not in SCHEMA["properties"]["sponsorship"]["enum"]:
        return None
    data["evidence"] = str(data.get("evidence") or "")[:300]
    return data


def client():
    import anthropic

    return anthropic.Anthropic(api_key=settings.anthropic_api_key or None)


def read_now(title: str, company: str, passage: str, model: str | None = None) -> dict | None:
    """One posting, read at once by Claude (the eval, and checking a single job by hand)."""
    message = client().messages.create(**request_params(title, company, passage, model))
    return parse_message(message)


# ---- Groq (free tier: open models, docs console.groq.com/docs) ----------------------------------------------------
# The free plan allows 30 requests and 8,000 tokens a minute and 1,000 requests and 200,000 tokens a day per model
# (console.groq.com/docs/rate-limits, 2026-11-15). There is no batch endpoint, so postings are read one at a time,
# GROQ_SPACING_SECONDS apart (under the per-minute token limit at ~1,000 tokens each), and the day stops at
# visa_reader_groq_daily_tokens, below the free 200,000. Structured outputs in strict mode (constrained decoding)
# are supported on gpt-oss-120b/20b and Qwen3.8 27B (console.groq.com/docs/structured-outputs).

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_SPACING_SECONDS = 8.0


def groq_payload(title: str, company: str, passage: str, model: str | None = None) -> dict:
    model = model or settings.visa_reader_groq_model
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"Job title: {title}\nEmployer: {company}\n\nSentences:\n{passage}"},
        ],
        "response_format": {"type": "json_schema",
                            "json_schema": {"name": "visa_verdict", "strict": True, "schema": SCHEMA}},
        "max_completion_tokens": MAX_TOKENS,
    }
    # Reasoning, kept short: it is most of the tokens, and the free plan counts tokens.
    if "gpt-oss" in model:
        payload.update(reasoning_effort="low", include_reasoning=False)
    return payload


def groq_read(http, title: str, company: str, passage: str, model: str | None = None) -> tuple[dict | None, int]:
    """One posting read by a Groq model: (verdict or None, tokens used). Raises httpx.HTTPStatusError on an error
    answer (429 included, with Retry-After) so the caller can stop for the day."""
    for attempt in range(3):
        resp = http.post(GROQ_URL, json=groq_payload(title, company, passage, model),
                         headers={"Authorization": f"Bearer {settings.groq_api_key}"}, timeout=60)
        # gpt-oss now and then answers a structured-output request with a tool call ("Tool choice is none, but model
        # called a tool", 400 tool_use_failed, 1 in 25 in the eval): the same request usually works again.
        if resp.status_code == 400 and "tool_use_failed" in resp.text and attempt < 2:
            continue
        break
    resp.raise_for_status()
    data = resp.json()
    tokens = int((data.get("usage") or {}).get("total_tokens") or 0)
    choice = (data.get("choices") or [{}])[0]
    if choice.get("finish_reason") not in (None, "stop"):
        return None, tokens
    try:
        verdict = json.loads((choice.get("message") or {}).get("content") or "")
    except ValueError:
        return None, tokens
    if not isinstance(verdict, dict) or verdict.get("sponsorship") not in SCHEMA["properties"]["sponsorship"]["enum"]:
        return None, tokens
    verdict["evidence"] = str(verdict.get("evidence") or "")[:300]
    return verdict, tokens


def read_due_with_groq(session: Session, max_jobs: int | None = None, sleep=None) -> dict:
    """Read due jobs one at a time with Groq, within today's token budget. Run hourly; each run reads at most
    `visa_reader_groq_per_run` jobs (about 8 seconds each)."""
    import time

    import httpx

    from app.models import Job, VisaRead

    sleep = sleep or time.sleep
    state = _state()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    used = state.get("groq_tokens", {}).get(today, 0)
    budget = settings.visa_reader_groq_daily_tokens - used
    if budget <= 0:
        return {"read": 0, "reason": "today's Groq token budget is used up"}
    jobs = due_jobs(session, max_jobs or settings.visa_reader_groq_per_run)
    read = failed = changed = 0
    stop = ""
    with httpx.Client() as http:
        for i, (jid, title, company, p) in enumerate(jobs):
            if used >= settings.visa_reader_groq_daily_tokens:
                stop = "daily token budget reached"
                break
            if i:
                sleep(GROQ_SPACING_SECONDS)
            try:
                verdict, tokens = groq_read(http, title, company, p)
            except httpx.HTTPStatusError as e:
                stop = f"Groq answered HTTP {e.response.status_code}"
                break
            except httpx.HTTPError as e:
                stop = f"Groq unreachable: {type(e).__name__}"
                break
            used += tokens
            if verdict is None:
                failed += 1
                continue
            row = VisaRead(job_id=jid, passage_hash=passage_hash(p), model=f"groq:{settings.visa_reader_groq_model}",
                           verdict=verdict, read_at=datetime.now(timezone.utc))
            job = session.get(Job, jid)
            if job is not None:
                before = (job.no_sponsor, job.sponsors_text, job.student_visa)
                apply_verdict(job, verdict)
                row.changed = (job.no_sponsor, job.sponsors_text, job.student_visa) != before
                changed += row.changed
            session.merge(row)
            session.commit()
            read += 1
    state.setdefault("groq_tokens", {})[today] = used
    week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
    state["groq_tokens"] = {d: t for d, t in state["groq_tokens"].items() if d >= week_ago}
    state["last_groq_run"] = {"at": datetime.now(timezone.utc).isoformat(), "read": read, "failed": failed,
                              "changed": changed, "stopped": stop}
    _save_state(state)
    return {"read": read, "failed": failed, "changed": changed, "tokens_today": used, "stopped": stop}


# ---- what a verdict changes ---------------------------------------------------------------------------------------

# A "no" counts only when the sentence the model quotes rules something out. On the eval set gpt-oss-120b also said
# "no" to postings that are only unclear ("Applicants must be currently authorized to work in the United States on a
# full-time basis", BASF): their quotes have none of these words, so they get no badge.
_RULES_OUT = re.compile(
    r"\b(not|no|unable|cannot|can ?not|can'?t|won'?t|will\s+not|does\s+not|do\s+not|ineligible|without|only|"
    r"exclusively|restricted|limited\s+to)\b|citizen|green\s*card|permanent\s+resident|clearance|polygraph|"
    r"u\.?s\.?\s+persons?|itar|export", re.I)


def flags_from(verdict: dict | None) -> dict:
    """The job fields a verdict can set. Only additions: see the module docstring."""
    if not verdict:
        return {}
    out = {}
    restricted = verdict.get("us_persons_only") or verdict.get("clearance_required")  # specific, no guard needed
    if restricted or (verdict.get("sponsorship") == "no" and _RULES_OUT.search(verdict.get("evidence") or "")):
        out["no_sponsor"] = True
    elif verdict.get("sponsorship") == "yes":
        out["sponsors_text"] = True
    if verdict.get("opt_cpt") in ("welcome", "excluded"):
        out["student_visa"] = verdict["opt_cpt"]
    return out


def overlay(values: dict, verdict: dict | None) -> dict:
    """Rule-based visa fields (no_sponsor, sponsors_text, student_visa) with a verdict's additions on top."""
    f = flags_from(verdict)
    out = dict(values)
    if f.get("student_visa") and not out.get("student_visa"):
        out["student_visa"] = f["student_visa"]
        if f["student_visa"] == "excluded":
            f["no_sponsor"] = True
    if f.get("no_sponsor"):
        out["no_sponsor"], out["sponsors_text"] = True, False
    elif f.get("sponsors_text") and not out.get("no_sponsor"):
        out["sponsors_text"] = True
    return out


def apply_verdict(job, verdict: dict | None) -> None:
    """Add a verdict's flags to a job whose rule-based fields are already set (poller.apply_flags)."""
    if not verdict:
        return
    keys = ("no_sponsor", "sponsors_text", "student_visa")
    for k, v in overlay({k: getattr(job, k) for k in keys}, verdict).items():
        setattr(job, k, v)


def verdict_for(session: Session, job_id: int | None) -> dict | None:
    if job_id is None:
        return None
    from app.models import VisaRead

    row = session.get(VisaRead, job_id)
    return row.verdict if row and row.verdict else None


# ---- batches ------------------------------------------------------------------------------------------------------

def _state_file() -> Path:
    from app.matching import _models_dir

    return Path(_models_dir()).parent / "visa_reader.json"


def _state() -> dict:
    try:
        return json.loads(_state_file().read_text())
    except (OSError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    path = _state_file()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1, default=str))
    tmp.replace(path)


def provider() -> str:
    """"groq" (free tier) or "anthropic" (Claude, paid): VISA_READER_PROVIDER, else whichever key is set (Groq first)."""
    chosen = (settings.visa_reader_provider or "").lower()
    if chosen in ("groq", "anthropic"):
        return chosen
    return "groq" if settings.groq_api_key else "anthropic"


def enabled() -> bool:
    key = settings.groq_api_key if provider() == "groq" else settings.anthropic_api_key
    return bool(settings.visa_reader_enabled and key)


def due_jobs(session: Session, limit: int) -> list:
    """New, open US jobs in someone's For you feed, found in the last few days, not read yet, with something about
    work authorization to read. Newest first."""
    since = (datetime.now(timezone.utc) - timedelta(days=settings.visa_reader_days)).strftime("%Y-%m-%d %H:%M:%S")
    rows = session.execute(text(
        "SELECT j.id, j.title, j.company_name, j.description FROM jobs j INDEXED BY ix_jobs_by_found_v2 "
        "WHERE j.closed_at IS NULL AND j.is_repost = 0 AND j.first_seen_at >= :since AND j.us_eligible = 1 "
        "AND j.role_match = 1 AND NOT EXISTS (SELECT 1 FROM visa_reads v WHERE v.job_id = j.id) "
        # Only jobs the rules didn't settle: when they flag a job they are right 97-98% of the time (eval set).
        "AND j.no_sponsor = 0 AND j.sponsors_text = 0 "
        "ORDER BY j.first_seen_at DESC LIMIT :n"), {"since": since, "n": limit * 4}).all()
    out = []
    for jid, title, company, description in rows:
        p = passages(description or "")
        if p:
            out.append((jid, title or "", company or "", p))
        if len(out) >= limit:
            break
    return out


def submit(session: Session, limit: int | None = None) -> dict:
    """Send one batch of due jobs, within today's cap. Returns what was sent."""
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    state = _state()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    sent_today = state.get("sent", {}).get(today, 0)
    room = max(0, settings.visa_reader_daily_jobs - sent_today)
    n = min(limit or settings.visa_reader_per_batch, room)
    if n <= 0:
        return {"sent": 0, "reason": f"daily cap of {settings.visa_reader_daily_jobs} reached"}
    jobs = due_jobs(session, n)
    if not jobs:
        return {"sent": 0, "reason": "nothing new to read"}
    batch = client().messages.batches.create(requests=[
        Request(custom_id=str(jid), params=MessageCreateParamsNonStreaming(**request_params(title, company, p)))
        for jid, title, company, p in jobs])
    from app.models import VisaRead

    for jid, _, _, p in jobs:  # placeholders: the job isn't picked again while its batch runs
        session.merge(VisaRead(job_id=jid, passage_hash=passage_hash(p), model=settings.visa_reader_model,
                               batch_id=batch.id, verdict=None))
    session.commit()
    state.setdefault("batches", []).append({"id": batch.id, "jobs": len(jobs), "at": datetime.now(timezone.utc).isoformat()})
    state.setdefault("sent", {})[today] = sent_today + len(jobs)
    state["sent"] = {d: c for d, c in state["sent"].items() if d >= (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")}
    _save_state(state)
    return {"sent": len(jobs), "batch": batch.id}


def collect(session: Session) -> dict:
    """Store the verdicts of every finished batch and apply them to their jobs."""
    from app.models import Job, VisaRead

    state = _state()
    pending = state.get("batches", [])
    still, stored, failed = [], 0, 0
    api = client()
    for b in pending:
        batch = api.messages.batches.retrieve(b["id"])
        if batch.processing_status != "ended":
            still.append(b)
            continue
        for result in api.messages.batches.results(b["id"]):
            row = session.get(VisaRead, int(result.custom_id))
            if row is None:
                continue
            verdict = parse_message(result.result.message) if result.result.type == "succeeded" else None
            row.read_at = datetime.now(timezone.utc)
            if verdict is None:
                failed += 1
                session.delete(row)  # read again in a later batch
                continue
            row.verdict = verdict
            stored += 1
            job = session.get(Job, row.job_id)
            if job is not None:
                before = (job.no_sponsor, job.sponsors_text, job.student_visa)
                apply_verdict(job, verdict)
                if (job.no_sponsor, job.sponsors_text, job.student_visa) != before:
                    row.changed = True
        session.commit()
    state["batches"] = still
    state["last_collect"] = {"at": datetime.now(timezone.utc).isoformat(), "stored": stored, "failed": failed}
    _save_state(state)
    return {"stored": stored, "failed": failed, "pending_batches": len(still)}


def run_once(session: Session) -> dict:
    if not enabled():
        return {"skipped": "no API key for the visa reader, or VISA_READER_ENABLED=false"}
    if provider() == "groq":
        return read_due_with_groq(session)
    out = collect(session)
    out.update(submit(session))
    return out


def status() -> dict:
    s = _state()
    model = settings.visa_reader_groq_model if provider() == "groq" else settings.visa_reader_model
    return {"enabled": enabled(), "provider": provider(), "model": model, "pending_batches": len(s.get("batches", [])),
            "sent": s.get("sent", {}), "last_collect": s.get("last_collect"), "groq_tokens": s.get("groq_tokens", {}),
            "last_groq_run": s.get("last_groq_run")}


def main() -> None:
    from app.db import SessionLocal

    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--submit", type=int, metavar="N", help="send a batch of up to N jobs now")
    ap.add_argument("--collect", action="store_true", help="store finished batches' verdicts now")
    args = ap.parse_args()
    with SessionLocal() as session:
        if args.collect:
            print(collect(session))
        if args.submit:
            print(submit(session, args.submit))
    print(status())


if __name__ == "__main__":
    main()
