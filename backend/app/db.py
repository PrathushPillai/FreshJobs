from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

is_sqlite = settings.database_url.startswith("sqlite")
# 40 connections: three polling lanes, background refreshes and the API's worker threads all hold one at
# once. With the default 15, slow requests used them all up and the rest of the site waited for one.
engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False, "timeout": 120} if is_sqlite else {},
    **({"pool_size": 20, "max_overflow": 20, "pool_timeout": 60} if is_sqlite and ":memory:" not in settings.database_url and settings.database_url != "sqlite://" else {}),
)

if is_sqlite:

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):
        # WAL lets the API keep reading while the poller writes.
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        # The database is ~9 GB and searches were slow mostly on their first, cold read (6-40 s, then
        # 0.4-2 s once cached). SQLite's default cache is 2 MB per connection: give each 32 MB, and read
        # through a memory map so pages already in Windows' file cache are used without copying.
        cur.execute("PRAGMA cache_size=-32768")  # 32 MB each; the memory map below is shared
        cur.execute("PRAGMA mmap_size=4294967296")
        cur.execute("PRAGMA temp_store=MEMORY")
        cur.close()


SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    from app import models  # noqa: F401  (register models)

    Base.metadata.create_all(engine)
    _add_missing_columns()
    _rebuild_applications()
    with engine.begin() as conn:
        conn.execute(text("DROP INDEX IF EXISTS ix_jobs_feed"))  # superseded by ix_jobs_feed_v2
    if is_sqlite:
        _ensure_title_search()


def _rebuild_applications() -> None:
    """Before accounts, a job could be tracked once (UNIQUE job_id). Per user it's once per user, so the table is
    rebuilt once with the new constraint, keeping every row. SQLite can't drop a constraint in place."""
    if not is_sqlite:
        return
    with engine.begin() as conn:
        uniques = [r[1] for r in conn.exec_driver_sql("PRAGMA index_list(applications)") if r[2]]
        old = [i for i in uniques if [c[2] for c in conn.exec_driver_sql(f'PRAGMA index_info("{i}")')] == ["job_id"]]
        if not old:
            return
        cols = [r[1] for r in conn.exec_driver_sql("PRAGMA table_info(applications)")]
        conn.exec_driver_sql("ALTER TABLE applications RENAME TO applications_old")
        for (name,) in conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='applications_old' "
                                            "AND name NOT LIKE 'sqlite_autoindex%'").all():
            conn.exec_driver_sql(f'DROP INDEX "{name}"')
        Base.metadata.tables["applications"].create(conn)
        new_cols = {c.name for c in Base.metadata.tables["applications"].columns}
        keep = ", ".join(f'"{c}"' for c in cols if c in new_cols)
        conn.exec_driver_sql(f"INSERT INTO applications ({keep}) SELECT {keep} FROM applications_old")
        conn.exec_driver_sql("DROP TABLE applications_old")


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
        for name in _OBSOLETE_INDEXES:  # replaced by a _v2; each copy costs write time on every insert
            conn.execute(text(f"DROP INDEX IF EXISTS {name}"))


# Indexes replaced by new versions in models.py: dropped at startup once the new one exists.
_OBSOLETE_INDEXES = ["ix_jobs_by_found", "ix_jobs_by_listed"]


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
