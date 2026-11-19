"""Hourly work beside discovery: real names for boards still named after their slug, closing jobs of switched-off
sources that nobody can check any more, the AI visa reader, and the next batch of Phenom careers sites
(`phenom_rollout`, only once the last batch loaded cleanly). Each part fails on its own.

Run by the discovery worker after each discovery run (lanes.run_discovery_once) and by main.run_discovery in the
old thread mode. Until 2026-11-16 it was called only from main.run_discovery, which the worker mode never uses, so
none of it ran on the server for a day: no names, no stale closing, no AI visa reads after the first three.
"""

import logging
from datetime import datetime, timezone

from app.db import SessionLocal

log = logging.getLogger(__name__)


def run() -> dict:
    from app.pipeline import phenom_rollout, source_names, visa_reader
    from app.pipeline.poller import close_stale_of_disabled

    out: dict = {}
    # The AI visa reader first (about 6 minutes at most), naming last: a naming run asks each Workday board for
    # its name (up to 5 requests), which took the whole first manual run (2026-11-16) and adds to Workday's rate
    # limits, so it does 100 boards an hour.
    for key, fn in (("visa_reader", lambda s: visa_reader.run_once(s)),
                    ("closed_from_disabled", lambda s: close_stale_of_disabled(s, datetime.now(timezone.utc))),
                    ("phenom_rollout", lambda s: phenom_rollout.step(s)),
                    ("radancy_rollout", lambda s: phenom_rollout.step(s, platform="radancy")),
                    ("named", lambda s: source_names.name_some(s, limit=100))):
        try:
            with SessionLocal() as session:
                out[key] = fn(session)
        except Exception:
            log.exception("upkeep step %s failed", key)
            out[key] = {"error": "failed, see the log"}
    return out
