"""The portal's health monitor: checks every part that can break, keeps the result, and emails the admin.

Run every 15 minutes on the server by a systemd timer (deploy/freshjobs-health.timer):

    python -m app.health             # check, save health.json next to the database, email on changes
    python -m app.health --print     # check and print only (no email, nothing saved)
    python -m app.health --portal    # also run scripts/check_portal.py now (otherwise once a day)

It runs as its own process, so it still reports when the backend is down. The admin dashboard shows the last
result (GET /api/admin/health), and anyone with SSH can read health.json.

Each check is "ok", "warn" or "fail". The admin gets an email when a check fails, when a warning has lasted
WARN_GRACE_MINUTES (a single slow poll or a platform's brief outage shouldn't wake anyone), again every
REMIND_HOURS while it lasts, and once more when everything is back to normal.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from sqlalchemy import text

from app.config import settings

WARN_GRACE_MINUTES = 60
# A failure must still be there on the next run (15 min later): a deploy or reboot restarts the backend for up to
# ~13 minutes, and that is no reason to email.
FAIL_GRACE_MINUTES = 10
REMIND_HOURS = 12
PORTAL_CHECK_HOURS = 24  # how often check_portal.py runs (it signs in and sends ~40 GET requests)

# A worker writes a heartbeat every 30 s even mid-poll; minutes without one means it died or hangs.
HEARTBEAT_FAIL_MINUTES = 5
# How long since a lane's last finished poll is too long (minutes): fast polls every 5, slow and bulk take longer.
POLL_AGE = {"fast": (25, 60), "slow": (120, 360), "bulk": (180, 480)}  # (warn, fail)
NEW_JOBS_HOURS = 3  # no new job found in this long means the pollers find nothing
PLATFORM_MIN_BOARDS = 20  # a platform with at least this many boards...
PLATFORM_FAILING_SHARE = 0.4  # ...failing this share at once is a platform-wide problem (outage, block, API change)
BACKUP_MAX_HOURS = 30
WAL_WARN_BYTES = 2 * 1024**3
MEMORY_WARN_MB = 800


@dataclass
class Check:
    name: str
    level: str  # ok | warn | fail | skip
    detail: str
    data: dict = field(default_factory=dict)


def data_dir() -> Path:
    from app.matching import _models_dir

    return Path(_models_dir()).parent


def health_file() -> Path:
    return data_dir() / "health.json"


def _db_path() -> Path | None:
    url = settings.database_url
    if url.startswith("sqlite:///") and ":memory:" not in url:
        return Path(url.removeprefix("sqlite:///"))
    return None


def _age_minutes(iso: str | None, now: datetime) -> float | None:
    if not iso:
        return None
    try:
        t = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return (now - t).total_seconds() / 60


def _fmt_minutes(m: float) -> str:
    if m < 90:
        return f"{m:.0f} min"
    if m < 48 * 60:
        return f"{m / 60:.1f} h"
    return f"{m / 1440:.1f} days"


# ---- the checks -------------------------------------------------------------------------------------------------

def check_api() -> Check:
    url = settings.health_api_url.rstrip("/") + "/api/status"
    try:
        t = time.monotonic()
        r = httpx.get(url, timeout=30)
        took = time.monotonic() - t
    except httpx.HTTPError as e:
        return Check("backend", "fail", f"the backend doesn't answer at {url}: {type(e).__name__}")
    if r.status_code != 200:
        return Check("backend", "fail", f"/api/status answered HTTP {r.status_code}")
    level = "warn" if took > 10 else "ok"
    return Check("backend", level, f"answers in {took:.1f} s", {"seconds": round(took, 2)})


def check_site() -> list[Check]:
    """The public address: Caddy, HTTPS, the built frontend, and the certificate's expiry."""
    base = settings.public_url.rstrip("/")
    if not base.startswith("https://"):
        return [Check("public site", "skip", f"PUBLIC_URL is {base} (not a public HTTPS address)")]
    out = []
    try:
        r = httpx.get(base + "/", timeout=30, follow_redirects=True)
        ok = r.status_code == 200 and "<div id=\"root\"" in r.text
        out.append(Check("public site", "ok" if ok else "fail",
                         "the page loads" if ok else f"the home page answered HTTP {r.status_code}"))
        r = httpx.get(base + "/api/status", timeout=30)
        if r.status_code != 200:
            out.append(Check("public API", "fail", f"{base}/api/status answered HTTP {r.status_code}"))
    except httpx.HTTPError as e:
        out.append(Check("public site", "fail", f"{base} doesn't load: {type(e).__name__}: {str(e)[:120]}"))
        return out
    host = base.split("//", 1)[1].split("/", 1)[0]
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((host, 443), timeout=15) as sock, ctx.wrap_socket(sock, server_hostname=host) as s:
            expires = datetime.fromtimestamp(ssl.cert_time_to_seconds(s.getpeercert()["notAfter"]), timezone.utc)
        days = (expires - datetime.now(timezone.utc)).days
        level = "fail" if days < 5 else "warn" if days < 14 else "ok"
        out.append(Check("HTTPS certificate", level, f"expires in {days} days (Caddy renews it by itself)",
                         {"days": days}))
    except (OSError, ssl.SSLError, KeyError, ValueError) as e:
        out.append(Check("HTTPS certificate", "warn", f"couldn't read it: {type(e).__name__}"))
    return out


def check_lanes(now: datetime) -> list[Check]:
    from app.lanes import LANES, lane_status

    if not settings.scheduler_enabled:
        return [Check("polling", "skip", "SCHEDULER_ENABLED is false here (no polling on this copy)")]
    out = []
    for lane in (*LANES, "discovery"):
        s = lane_status(lane)
        beat = _age_minutes(s.get("heartbeat"), now)
        if beat is None or beat > HEARTBEAT_FAIL_MINUTES:
            out.append(Check(f"{lane} worker", "fail",
                             "no status file" if beat is None else f"no heartbeat for {_fmt_minutes(beat)}: "
                             "the worker died or hangs"))
            continue
        if lane == "discovery":
            out.append(Check("discovery worker", "ok", "alive"))
            continue
        last = s.get("last") or {}
        done = _age_minutes(last.get("finished_at"), now)
        warn, fail = POLL_AGE[lane]
        if done is None:
            started = _age_minutes(s.get("started_at"), now) or 0
            level = "fail" if started > fail else "warn" if started > warn else "ok"
            out.append(Check(f"{lane} polls", level, f"no poll finished since the worker started "
                             f"{_fmt_minutes(started)} ago"))
            continue
        level = "fail" if done > fail else "warn" if done > warn else "ok"
        sources = last.get("sources") or 0
        cut = last.get("cut_short") or 0
        errors = last.get("errors") or 0
        detail = (f"last poll finished {_fmt_minutes(done)} ago: {sources:,} boards, {errors:,} errors, "
                  f"{last.get('new_jobs', 0):,} new jobs")
        if cut:
            detail += f", {cut:,} cut short"
        if level == "ok" and sources and cut / sources > 0.3:
            level, detail = "warn", detail + " (the lane can't keep up)"
        if level == "ok" and sources >= 100 and errors / sources > 0.2:
            level, detail = "warn", detail + " (many boards failing)"
        out.append(Check(f"{lane} polls", level, detail, {k: last.get(k) for k in
                                                          ("sources", "errors", "new_jobs", "cut_short")}))
    return out


def check_new_jobs(session, now: datetime) -> Check:
    if not settings.scheduler_enabled:
        return Check("new jobs", "skip", "no polling on this copy")
    since = (now - timedelta(hours=NEW_JOBS_HOURS)).strftime("%Y-%m-%d %H:%M:%S")
    n = session.execute(text("SELECT count(*) FROM jobs WHERE first_seen_at >= :s AND baseline = 0"),
                        {"s": since}).scalar() or 0
    day = session.execute(text("SELECT count(*) FROM jobs WHERE first_seen_at >= :s AND baseline = 0"),
                          {"s": (now - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")}).scalar() or 0
    level = "fail" if n == 0 else "ok"
    return Check("new jobs", level, f"{n:,} found in the last {NEW_JOBS_HOURS} h, {day:,} in 24 h",
                 {"last_3h": n, "last_24h": day})


def check_platforms(session) -> list[Check]:
    rows = session.execute(text(
        "SELECT ats, count(*), sum(CASE WHEN last_status LIKE 'error%' THEN 1 ELSE 0 END) "
        "FROM companies WHERE enabled = 1 GROUP BY ats")).all()
    total = sum(r[1] for r in rows)
    failing = sum(r[2] or 0 for r in rows)
    out = []
    bad = sorted(((ats, n, f or 0) for ats, n, f in rows
                  if n >= PLATFORM_MIN_BOARDS and (f or 0) / n >= PLATFORM_FAILING_SHARE),
                 key=lambda r: -r[2])
    if bad:
        out.append(Check("job platforms", "warn", "most boards failing on: " + ", ".join(
            f"{ats} ({f:,} of {n:,})" for ats, n, f in bad), {"platforms": [list(b) for b in bad]}))
    else:
        out.append(Check("job platforms", "ok", f"{failing:,} of {total:,} boards failing (none platform-wide)",
                         {"failing": failing, "boards": total}))
    return out


FRESHNESS_WARN_HOURS = 6  # a platform's typical board not checked successfully in this long
VOLUME_WEEKS = 4  # baseline: the same hours of the same weekday in the previous weeks
VOLUME_HOURS = 6


def check_platform_freshness(session, now: datetime) -> Check:
    """Per platform, how long ago its median board was last checked successfully (data observability's freshness):
    a platform whose boards all stopped answering, or a lane that skips them, shows here first."""
    rows = session.execute(text(
        "SELECT ats, last_ok_at FROM companies WHERE enabled = 1 AND last_ok_at IS NOT NULL")).all()
    ages: dict[str, list[float]] = {}
    for ats, ok in rows:
        if (age := _age_minutes(str(ok).replace(" ", "T"), now)) is not None:
            ages.setdefault(ats, []).append(age / 60)
    stale = []
    for ats, values in ages.items():
        values.sort()
        median = values[len(values) // 2]
        # Rate-limited and slow platforms check less often by design.
        limit = FRESHNESS_WARN_HOURS * (2 if ats in ("workable", "jazzhr", "yc", "jsearch", "adzuna") else 1)
        if len(values) >= 5 and median > limit:
            stale.append(f"{ats} ({median:.0f} h)")
    if stale:
        return Check("platform freshness", "warn", "boards not checked for hours on: " + ", ".join(stale))
    return Check("platform freshness", "ok", f"every platform's boards checked within {FRESHNESS_WARN_HOURS} h")


def check_volume(session, now: datetime) -> Check:
    """New jobs in the last VOLUME_HOURS against the same hours on the same weekday of the previous weeks: a
    platform-wide outage or a broken adapter shows as a drop long before anyone misses the jobs."""
    def count(end: datetime) -> int:
        start = end - timedelta(hours=VOLUME_HOURS)
        return session.execute(text("SELECT count(*) FROM jobs WHERE first_seen_at >= :a AND first_seen_at < :b "
                                    "AND baseline = 0"), {"a": start.strftime("%Y-%m-%d %H:%M:%S"),
                                                          "b": end.strftime("%Y-%m-%d %H:%M:%S")}).scalar() or 0
    now_n = count(now)
    past = [count(now - timedelta(weeks=w)) for w in range(1, VOLUME_WEEKS + 1)]
    past = [p for p in past if p > 0]
    if len(past) < 2:
        return Check("new-job volume", "ok", f"{now_n:,} new jobs in {VOLUME_HOURS} h (not enough history to compare)")
    typical = sorted(past)[len(past) // 2]
    detail = f"{now_n:,} new jobs in the last {VOLUME_HOURS} h; usually {typical:,} at this hour on this weekday"
    level = "warn" if typical >= 100 and now_n < typical * 0.4 else "ok"
    return Check("new-job volume", level, detail + (" (a big drop)" if level == "warn" else ""),
                 {"now": now_n, "typical": typical})


def check_disk(now: datetime) -> list[Check]:
    out = []
    db = _db_path()
    if not db or not db.exists():
        return [Check("disk", "skip", "not a SQLite file database")]
    usage = shutil.disk_usage(db.parent)
    free_gb = usage.free / 1024**3
    db_gb = db.stat().st_size / 1024**3
    # The nightly backup writes an uncompressed copy first, then a gzip of about a quarter of it.
    need_gb = db_gb * 1.3 + 3
    level = "fail" if free_gb < 5 else "warn" if free_gb < need_gb else "ok"
    detail = f"{free_gb:.0f} GB free; the database is {db_gb:.1f} GB"
    if level != "ok":
        detail += f" (a nightly backup needs about {need_gb:.0f} GB free)"
    out.append(Check("disk", level, detail, {"free_gb": round(free_gb, 1), "db_gb": round(db_gb, 1)}))
    wal = db.with_name(db.name + "-wal")
    if wal.exists() and wal.stat().st_size > WAL_WARN_BYTES:
        out.append(Check("database log (WAL)", "warn", f"{wal.stat().st_size / 1024**3:.1f} GB: checkpoints "
                         "aren't keeping up, which slows every read"))
    backups = sorted((db.parent / "backups").glob("jobs-*.db.gz"), key=lambda p: p.stat().st_mtime)
    if (db.parent / "backups").exists():
        if not backups:
            out.append(Check("backups", "fail", "no backup found"))
        else:
            age_h = (now.timestamp() - backups[-1].stat().st_mtime) / 3600
            level = "fail" if age_h > BACKUP_MAX_HOURS else "ok"
            out.append(Check("backups", level, f"newest {backups[-1].name}, {age_h:.0f} h old "
                             f"({backups[-1].stat().st_size / 1024**3:.1f} GB); {len(backups)} kept"))
    return out


def check_memory() -> Check:
    try:
        info = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
    except OSError:
        return Check("memory", "skip", "no /proc/meminfo here")
    avail = int(info["MemAvailable"].split()[0]) // 1024
    total = int(info["MemTotal"].split()[0]) // 1024
    level = "warn" if avail < MEMORY_WARN_MB else "ok"
    return Check("memory", level, f"{avail:,} MB of {total:,} MB available", {"available_mb": avail})


def check_email(session, now: datetime) -> Check:
    from app.notify.email import email_mode

    mode = email_mode()
    if mode != "on":
        level = "warn" if settings.public_url.startswith("https://") else "skip"
        return Check("email", level, f"email is {mode} (nothing reaches a real inbox)")
    since = (now - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    rows = dict(session.execute(text(
        "SELECT kind, count(*) FROM activity WHERE kind IN ('email_sent', 'email_failed') AND at >= :s GROUP BY kind"),
        {"s": since}).all())
    sent, failed = rows.get("email_sent", 0), rows.get("email_failed", 0)
    limit = settings.email_daily_limit
    level = "ok"
    detail = f"{sent} sent and {failed} failed in 24 h"
    if failed >= 3 and failed > sent * 0.2:
        level = "warn"
        last = session.execute(text("SELECT detail FROM activity WHERE kind = 'email_failed' ORDER BY id DESC LIMIT 1")
                               ).scalar()
        detail += f"; last failure: {(last or '')[:160]}"
    if limit and sent >= limit * 0.8:
        level = "warn"
        detail += f" (the daily limit is {limit})"
    return Check("email", level, detail, {"sent": sent, "failed": failed})


_JUNK_TITLE = re.compile(r"&[a-z]{2,8};|&#\d+;|<[a-z/][^>]*>|\{\{|\bundefined\b|\bnull\b|^\W*$", re.I)


FEED_SAMPLE = 2000  # the newest visible jobs the content checks read
FLOOD_SHARE = 0.08  # one source with more than this share of the newest jobs is flooding the feed
FOREIGN_ALLOWED = 3  # jobs abroad in the US feed (the location rules missed a format)
DUPLICATES_ALLOWED = 5  # one posting shown twice (from two sources)
SLUG_NAME_SHARE = 0.15  # employers shown by a board's code name ("Tjx", "Acadiahealthcare")


def _feed_sample(session) -> list:
    """The newest jobs the US feed shows, as people see them."""
    return session.execute(text(
        "SELECT j.id, j.title, j.url, j.location, j.remote, j.company_key, j.company_name, j.company_id, "
        "c.name, c.slug, c.ats FROM jobs j INDEXED BY ix_jobs_by_found_v2 JOIN companies c ON c.id = j.company_id "
        "WHERE j.closed_at IS NULL AND j.is_repost = 0 AND j.us_eligible = 1 "
        "ORDER BY j.first_seen_at DESC LIMIT :n"), {"n": FEED_SAMPLE}).all()


def check_feed_content(session) -> list[Check]:
    """What people actually see, read the way a person would: the newest jobs in the US feed.

    Code tests use made-up jobs and the portal check only proves the pages answer; these catch bad data:
    junk titles, places abroad in the US feed, one posting shown twice, one source flooding the feed, and
    employers shown by a board's code name.
    """
    from app.pipeline.dedupe import same_posting
    from app.pipeline.filters import is_us_location
    from app.pipeline.harvest import name_from_slug
    from app.pipeline.normalize import normalize_title

    rows = _feed_sample(session)
    _MULTI_EMPLOYER = _multi_employer()
    if not rows:
        return [Check("feed content", "warn", "the US feed is empty")]
    n = len(rows)
    out = []

    junk = [(r.id, r.title) for r in rows if not r.title or _JUNK_TITLE.search(r.title) or not (r.url or "").startswith("http")]
    out.append(Check("feed titles", "warn" if len(junk) > 5 else "ok",
                     f"{len(junk)} of the newest {n} jobs have junk titles or no link"
                     + (f", e.g. {junk[0][1]!r} (job {junk[0][0]})" if junk else ""), {"examples": junk[:10]}))

    abroad = [(r.id, r.location) for r in rows if r.location and not is_us_location(r.location, bool(r.remote))]
    out.append(Check("US-only feed", "warn" if len(abroad) > FOREIGN_ALLOWED else "ok",
                     f"{len(abroad)} of the newest {n} US jobs are abroad by today's rules"
                     + (f", e.g. {abroad[0][1]!r} (job {abroad[0][0]})" if abroad else ""), {"examples": abroad[:10]}))

    groups: dict[tuple, list] = {}
    for r in rows:
        if r.company_key:
            groups.setdefault((r.company_key, normalize_title(r.title or "")), []).append(r)
    dups = []
    for group in groups.values():
        for i, a in enumerate(group):
            if any(b.company_id != a.company_id and same_posting(a.url, a.location, b.url, b.location) for b in group[:i]):
                dups.append((a.id, a.title, a.company_name))
    out.append(Check("duplicates", "warn" if len(dups) > DUPLICATES_ALLOWED else "ok",
                     f"{len(dups)} of the newest {n} jobs are shown twice (from two sources)"
                     + (f", e.g. {dups[0][1]!r} at {dups[0][2]} (job {dups[0][0]})" if dups else ""),
                     {"examples": dups[:10]}))

    per_source: dict[int, int] = {}
    for r in rows:
        per_source[r.company_id] = per_source.get(r.company_id, 0) + 1
    top_id, top = max(per_source.items(), key=lambda kv: kv[1])
    top_name = next(r.company_name or r.name for r in rows if r.company_id == top_id)
    multi = next(r.ats for r in rows if r.company_id == top_id) in _MULTI_EMPLOYER
    flood = n >= 200 and top / n > FLOOD_SHARE and not multi  # shares mean little in a small feed
    out.append(Check("one source flooding", "warn" if flood else "ok",
                     f"the busiest source, {top_name}, has {top} of the newest {n} jobs ({top / n:.0%})", {"source": top_id}))

    slug_named = [r for r in rows if r.ats not in _MULTI_EMPLOYER and r.company_name == r.name == name_from_slug(r.slug)
                  and r.name.replace(" ", "").isalpha() and r.name.lower().replace(" ", "") == r.slug.split("|")[0].split(".")[0].replace("-", "").lower()]
    share = len(slug_named) / n
    out.append(Check("employer names", "warn" if n >= 200 and share > SLUG_NAME_SHARE else "ok",
                     f"{len(slug_named)} of the newest {n} jobs show the employer by a board's code name ({share:.0%})"
                     + (f", e.g. {slug_named[0].name!r}" if slug_named else "")))
    return out


def _multi_employer() -> set[str]:
    """Sources listing many employers: their jobs are spread across employers, not one source's flood."""
    from app.sources import ADAPTERS

    return {k for k, a in ADAPTERS.items() if getattr(a, "aggregator", False)} | {"yc"}


def check_log_errors() -> Check:
    if not shutil.which("journalctl"):
        return Check("backend errors", "skip", "no journalctl here")
    try:
        out = subprocess.run(["journalctl", "-u", "freshjobs-backend", "--since", "-20min", "--no-pager", "-o", "cat"],
                             capture_output=True, text=True, timeout=60).stdout
    except (OSError, subprocess.SubprocessError) as e:
        return Check("backend errors", "skip", f"couldn't read the log: {e}")
    lines = out.splitlines()
    tracebacks = sum(1 for line in lines if line.startswith("Traceback"))
    errors = [line for line in lines if " ERROR " in line or " CRITICAL " in line]
    server_errors = [line for line in lines if re.search(r'" 5\d\d ', line)]
    if not (tracebacks or errors or server_errors):
        return Check("backend errors", "ok", "no errors in the last 20 minutes")
    sample = (errors or server_errors or [""])[0][:200]
    return Check("backend errors", "warn", f"last 20 min: {tracebacks} tracebacks, {len(errors)} errors, "
                 f"{len(server_errors)} HTTP 5xx answers. {sample}",
                 {"tracebacks": tracebacks, "errors": len(errors), "http_5xx": len(server_errors)})


def check_portal(previous: dict, now: datetime, force: bool) -> Check | None:
    """scripts/check_portal.py against the public site: every screen's API, sorts, filters and searches."""
    last = previous.get("portal") or {}
    if not force and (_age_minutes(last.get("at"), now) or 1e9) < PORTAL_CHECK_HOURS * 60:
        return Check("portal check", last.get("level", "ok"), last.get("detail", ""), {"at": last.get("at")})
    if not settings.public_url.startswith("https://") and not force:
        return None
    script = Path(__file__).resolve().parent.parent / "scripts" / "check_portal.py"
    base = settings.public_url.rstrip("/") if settings.public_url.startswith("https://") else settings.health_api_url
    cmd = [sys.executable, str(script), "--base", base]
    if settings.public_url.startswith("https://"):
        cmd += ["--frontend", base]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=1200)
        out = p.stdout + p.stderr
    except subprocess.TimeoutExpired:
        out = "timed out after 20 minutes"
    summary = next((line for line in reversed(out.splitlines()) if "checks:" in line), out.strip()[-200:])
    failed = [line.strip() for line in out.splitlines() if line.startswith(("FAIL", "fail "))]
    m = re.search(r"(\d+) failed", summary)
    n_failed = int(m.group(1)) if m else (len(failed) or 1)
    level = "fail" if n_failed else "ok"
    detail = summary + ("; " + "; ".join(failed[:3]) if failed else "")
    return Check("portal check", level, detail[:400], {"at": now.isoformat()})


# ---- running, saving and alerting ---------------------------------------------------------------------------------

def run_checks(previous: dict | None = None, force_portal: bool = False) -> list[Check]:
    from app.db import SessionLocal

    previous = previous or {}
    now = datetime.now(timezone.utc)
    checks: list[Check] = [check_api(), *check_site()]
    checks += check_lanes(now)
    try:
        with SessionLocal() as session:
            checks.append(check_new_jobs(session, now))
            checks += check_platforms(session)
            checks.append(check_platform_freshness(session, now))
            checks.append(check_volume(session, now))
            checks.append(check_email(session, now))
            checks += check_feed_content(session)
    except Exception as e:  # the database itself: locked for minutes, damaged, missing
        checks.append(Check("database", "fail", f"{type(e).__name__}: {str(e)[:200]}"))
    checks += check_disk(now)
    checks.append(check_memory())
    checks.append(check_log_errors())
    portal = check_portal(previous, now, force_portal)
    if portal:
        checks.append(portal)
    return [c for c in checks if c.level != "skip"]


def _problems(checks: list[Check], since: dict, now: datetime) -> list[Check]:
    """Checks worth an email now: failures once they've lasted FAIL_GRACE_MINUTES, warnings WARN_GRACE_MINUTES."""
    out = []
    for c in checks:
        age = _age_minutes(since.get(c.name), now) or 0
        if c.level == "fail" and age >= FAIL_GRACE_MINUTES:
            out.append(c)
        elif c.level == "warn" and (_age_minutes(since.get(c.name), now) or 0) >= WARN_GRACE_MINUTES:
            out.append(c)
    return out


def _email(subject: str, lines: list[str], footer: str) -> bool:
    from app.notify.email import _layout, send_email

    to = settings.admin_email
    if not to:
        return False
    body = "".join(f"<p style=\"margin:0 0 10px\">{line}</p>" for line in lines)
    html = _layout(subject, subject, body + f"<p style=\"margin:16px 0 0;color:#666\">{footer}</p>")
    sent, _ = send_email(to, subject, "\n".join(lines) + "\n\n" + footer, html)
    return sent


def _line(c: Check) -> str:
    return f"{'FAIL' if c.level == 'fail' else 'Warning'}: {c.name}: {c.detail}"


def run(force_portal: bool = False, send: bool = True, save: bool = True) -> dict:
    path = health_file()
    try:
        previous = json.loads(path.read_text())
    except (OSError, ValueError):
        previous = {}
    now = datetime.now(timezone.utc)
    checks = run_checks(previous, force_portal)
    # When each check first left "ok", so warnings get their grace period.
    since = {c.name: (previous.get("since", {}).get(c.name) or now.isoformat()) for c in checks if c.level != "ok"}
    problems = _problems(checks, since, now)
    alert = previous.get("alert") or {}
    emailed = set(alert.get("names", []))
    names = {c.name for c in problems}
    last_email = _age_minutes(alert.get("at"), now)
    if send and problems and (names - emailed or last_email is None or last_email >= REMIND_HOURS * 60):
        n = len(problems)
        subject = f"FreshJobs: {n} problem{'s' if n > 1 else ''} ({', '.join(c.name for c in problems[:3])})"
        footer = (f"Checked {now:%Y-%m-%d %H:%M} UTC. You'll get a reminder every {REMIND_HOURS} h while this "
                  "lasts and an email when it's fixed. The Admin tab shows every check.")
        if _email(subject, [_line(c) for c in problems], footer):
            alert = {"at": now.isoformat(), "names": sorted(names)}
    elif send and not problems and emailed:
        if _email("FreshJobs: back to normal", ["Every check passes again: " + ", ".join(sorted(emailed)) + "."],
                  f"Checked {now:%Y-%m-%d %H:%M} UTC."):
            alert = {}
    elif problems:
        alert = {**alert, "names": sorted(names & emailed | set(alert.get("names", [])) & names)} if alert else alert
    portal = next((c for c in checks if c.name == "portal check"), None)
    result = {
        "checked_at": now.isoformat(),
        "level": "fail" if any(c.level == "fail" for c in checks) else
                 "warn" if any(c.level == "warn" for c in checks) else "ok",
        "checks": [asdict(c) for c in checks],
        "since": since,
        "alert": alert,
        "portal": ({"at": portal.data.get("at"), "level": portal.level, "detail": portal.detail}
                   if portal else previous.get("portal")),
    }
    if save:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(result, indent=1, default=str))
        os.replace(tmp, path)
    return result


def last_result() -> dict:
    """health.json for the admin dashboard, with `stale` when the monitor hasn't run for a while."""
    try:
        data = json.loads(health_file().read_text())
    except (OSError, ValueError):
        return {"checked_at": None, "level": "unknown", "checks": [], "stale": True}
    age = _age_minutes(data.get("checked_at"), datetime.now(timezone.utc))
    data["stale"] = age is None or age > 45
    return data


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--print", action="store_true", help="check and print only: no email, nothing saved")
    ap.add_argument("--portal", action="store_true", help="run scripts/check_portal.py now")
    args = ap.parse_args()
    result = run(force_portal=args.portal, send=not args.print, save=not args.print)
    for c in result["checks"]:
        print(f"{c['level']:5} {c['name']:22} {c['detail']}")
    print(f"\noverall: {result['level']}")


if __name__ == "__main__":
    main()
