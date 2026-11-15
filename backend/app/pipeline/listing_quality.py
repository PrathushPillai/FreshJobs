"""Listings that aren't real jobs, or are a risk: a red badge, and an opt-in filter (More -> Hide possible scams).

Big job sites refuse these; we show them with a warning (shown, never hidden unasked). Sources, 2026-11-15:
- FTC: honest employers never ask you to pay upfront or to pass money on from a check they sent
  (consumer.ftc.gov/consumer-alerts/2023/11/searching-job-work-remotely-avoid-scams-identity-theft);
- Indeed's posting rules ban pay-to-apply, MLM and "business opportunity" posts, and mystery shopping
  (indeed.com/hire/resources/howtohub/indeeds-job-posting-rules-and-policies-for-employers).

"scam": a single strong sign is enough (pay to start, deposit our check, reship packages, interview on Telegram).
"mlm": commission-only sales recruiting dressed as a job; two signs needed, since one ("commission") is common in
honest sales jobs.

    python -m app.pipeline.listing_quality --days 30   # re-read open jobs found in the last 30 days
"""

from __future__ import annotations

import argparse
import re
import time

_SCAM = [re.compile(p, re.I) for p in (
    r"(pay|purchase|buy)\s+(?:for\s+)?(?:your\s+|a\s+)?(?:own\s+)?(?:training|starter kit|background check|equipment|"
    r"software license)\s+(?:fee|kit|upfront|before)",
    r"(?:registration|application|training|onboarding|processing)\s+fee\s+(?:of|is|required)",
    r"we\s+will\s+(?:send|mail)\s+you\s+a\s+(?:check|cheque)\s+(?:to|for)\s+(?:purchase|buy)",
    r"(?:deposit|cash)\s+(?:the|a)\s+(?:check|cheque)\s+and\s+(?:send|transfer|wire)",
    r"\b(?:reship(?:ping)?|package\s+forwarding|parcel\s+forwarding)\b",
    r"payment\s+processing\s+(?:agent|assistant)",
    r"(?:pay|paid)\s+(?:in|with|via)\s+(?:gift\s+cards?|bitcoin|crypto(?:currency)?)",
    r"(?:interview|contact\s+(?:us|me|the\s+hiring\s+manager)|text|message)\s+(?:\w+\s+){0,3}(?:on|via|through)\s+"
    r"(?:telegram|whats\s?app|signal|wickr|google\s+hangouts)",
    r"\b(?:telegram|whats\s?app)\s*[:@]\s*\+?\d",
    r"\binterviews?\s+(?:are|is|will\s+be)\s+(?:held|conducted|done)\s+(?:on|via|over|through)\s+(?:telegram|whats\s?app|"
    r"signal|wickr|google\s+hangouts)",
)]
_MLM = [re.compile(p, re.I) for p in (
    r"\b(?:multi[-\s]?level|network)\s+marketing\b",
    r"\b(?:100%|straight|commission)[-\s]+(?:commission|only)\b",
    r"\bcommission[-\s]only\b",
    r"\bbe\s+your\s+own\s+boss\b",
    r"\bunlimited\s+(?:earning|income)\s+potential\b",
    r"\b(?:residual|passive)\s+income\b",
    r"\bfinancial\s+freedom\b",
    r"\bno\s+(?:experience|license)\s+(?:necessary|needed|required)\b.*\b(?:life|health)\s+insurance\b",
    r"\b(?:get|become)\s+(?:your\s+)?(?:life\s+|health\s+)?insurance\s+licen[cs]e(?:d)?\b.*\bcommission",
    r"\bsix[-\s]figure\s+(?:income|earning)s?\b.*\b(?:first\s+year|part[-\s]time|from\s+home)\b",
)]


def listing_flag(title: str, description: str) -> str | None:
    """"scam", "mlm" or None."""
    text = f"{title or ''}\n{description or ''}"
    if any(p.search(text) for p in _SCAM):
        return "scam"
    if sum(1 for p in _MLM if p.search(text)) >= 2:
        return "mlm"
    return None


def backfill(days: int, batch: int = 2000) -> dict:
    """Set listing_flag on open jobs found in the last `days` days. Short transactions, retried while locked."""
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import text as sql
    from sqlalchemy.exc import OperationalError

    from app.db import SessionLocal

    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    with SessionLocal() as s:
        ids = [i for (i,) in s.execute(sql("SELECT id FROM jobs INDEXED BY ix_jobs_by_found_v2 WHERE closed_at IS NULL "
                                           "AND is_repost = 0 AND first_seen_at >= :s"), {"s": since})]
    counts = {"read": 0, "scam": 0, "mlm": 0}
    for start in range(0, len(ids), batch):
        chunk = ids[start:start + batch]
        for attempt in range(10):
            try:
                with SessionLocal() as s:
                    rows = s.execute(sql(f"SELECT id, title, description, listing_flag FROM jobs WHERE id IN "
                                         f"({','.join(map(str, chunk))})")).all()
                    for jid, title, desc, old in rows:
                        new = listing_flag(title, desc)
                        if new:
                            counts[new] += 1
                        if new != old:
                            s.execute(sql("UPDATE jobs SET listing_flag = :f WHERE id = :i"), {"f": new, "i": jid})
                    s.commit()
                break
            except OperationalError as e:
                if "locked" not in str(e) or attempt == 9:
                    raise
                time.sleep(2 * (attempt + 1))
        counts["read"] += len(chunk)
        print(counts, flush=True)
    return counts


def main() -> None:
    ap = argparse.ArgumentParser(description="Flag possible scams and MLM recruiting among open jobs.")
    ap.add_argument("--days", type=int, default=30)
    print(backfill(ap.parse_args().days))


if __name__ == "__main__":
    main()
