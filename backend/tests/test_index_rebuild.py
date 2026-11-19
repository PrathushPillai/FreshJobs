"""db._add_missing_columns rebuilds an index whose columns changed (2026-11-19: it_role appended to the feed's sort
indexes, so hiding non-IT jobs stays index-only)."""

from sqlalchemy import text

import app.db as db


def test_an_index_with_old_columns_is_rebuilt(session):
    with db.engine.begin() as conn:
        conn.execute(text("DROP INDEX ix_jobs_by_sponsor"))
        conn.execute(text("CREATE INDEX ix_jobs_by_sponsor ON jobs (closed_at, is_repost, h1b_filings)"))
    db._add_missing_columns()
    with db.engine.connect() as conn:
        cols = [r[2] for r in conn.exec_driver_sql('PRAGMA index_info("ix_jobs_by_sponsor")')]
    assert cols[:3] == ["closed_at", "is_repost", "h1b_filings"] and cols[-1] == "it_role"
