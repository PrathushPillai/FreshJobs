from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

is_sqlite = settings.database_url.startswith("sqlite")
engine = create_engine(settings.database_url, connect_args={"check_same_thread": False, "timeout": 120} if is_sqlite else {})

if is_sqlite:

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):
        # WAL lets the API keep reading while the poller writes.
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()


SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    from app import models  # noqa: F401  (register models)

    Base.metadata.create_all(engine)
    _add_missing_columns()
    with engine.begin() as conn:
        conn.execute(text("DROP INDEX IF EXISTS ix_jobs_feed"))  # superseded by ix_jobs_feed_v2
    if is_sqlite:
        _ensure_title_search()


def _add_missing_columns() -> None:
    """Tiny auto-migration: add model columns that an existing database doesn't have yet.

    Only handles added columns (new columns must be nullable or have a scalar default).
    Renames, type changes and drops need a real migration tool.
    """
    insp = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            existing = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in existing:
                    continue
                ddl = f'ALTER TABLE {table.name} ADD COLUMN "{col.name}" {col.type.compile(engine.dialect)}'
                default = col.default.arg if col.default is not None and col.default.is_scalar else None
                if default is not None:
                    ddl += f" DEFAULT {int(default) if isinstance(default, bool) else repr(default)}"
                conn.execute(text(ddl))
            for index in table.indexes:
                if index.name not in {i["name"] for i in insp.get_indexes(table.name)}:
                    index.create(conn, checkfirst=True)


# Full-text index over job titles and employer names, kept in sync with `jobs` by triggers.
# Title search MATCHes it instead of LIKE-scanning every row (ms instead of seconds; see main._title_search).
_FTS_DDL = [
    "CREATE VIRTUAL TABLE jobs_fts USING fts5(title, company_name, content='jobs', content_rowid='id')",
    """CREATE TRIGGER jobs_fts_ai AFTER INSERT ON jobs BEGIN
         INSERT INTO jobs_fts(rowid, title, company_name) VALUES (new.id, new.title, new.company_name); END""",
    """CREATE TRIGGER jobs_fts_ad AFTER DELETE ON jobs BEGIN
         INSERT INTO jobs_fts(jobs_fts, rowid, title, company_name) VALUES ('delete', old.id, old.title, old.company_name); END""",
    """CREATE TRIGGER jobs_fts_au AFTER UPDATE OF title, company_name ON jobs BEGIN
         INSERT INTO jobs_fts(jobs_fts, rowid, title, company_name) VALUES ('delete', old.id, old.title, old.company_name);
         INSERT INTO jobs_fts(rowid, title, company_name) VALUES (new.id, new.title, new.company_name); END""",
]


def _ensure_title_search() -> None:
    with engine.begin() as conn:
        # The triggers go when `jobs` is dropped (tests do), leaving a stale index: then start over.
        if conn.execute(text("SELECT 1 FROM sqlite_master WHERE type = 'trigger' AND name = 'jobs_fts_ai'")).first():
            return
        conn.execute(text("DROP TABLE IF EXISTS jobs_fts"))
        for ddl in _FTS_DDL:
            conn.execute(text(ddl))
        conn.execute(text("INSERT INTO jobs_fts(jobs_fts) VALUES ('rebuild')"))  # index existing jobs


def get_session():
    with SessionLocal() as session:
        yield session
