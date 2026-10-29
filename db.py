"""
db.py  —  the app's one database: where it is, how it's opened, and bringing its
schema up to date.

Two backends (platform.md PL-04):

    SQLite     the default: config/app.db (APP_DB), for self-hosting and one server
    Postgres   when DATABASE_URL is a postgresql:// URL: several processes or
               servers, managed backups, row-level security (db_pg.py)

Every store gets its connections from connect(), and secret_box keeps its key
file beside DB_PATH. On SQLite each connection has:

    foreign_keys = ON    the ON DELETE CASCADEs in the schema apply
    busy timeout         a writer waits up to BUSY_TIMEOUT seconds for another
                         one instead of failing with "database is locked"
    sqlite3.Row rows     columns by name (row["role"]) or by position

and the file is in WAL mode (set once, it's kept in the file): readers don't
wait for a writer, and a writer doesn't wait for readers. On Postgres
connect() returns db_pg.Connection, which behaves the same for the stores (its
docstring says how, and which SQLite SQL it translates or refuses).

The schema is kept by Alembic migrations (migrations/versions/). The first
connect() in a process upgrades the database to the newest one, all or nothing,
before anything else uses it; a SQLite database from before Alembic is first
brought to the baseline and marked as at it (migrations/legacy.py). `alembic
upgrade head` does the same from the command line. How to add one:
ARCHITECTURE.md, "Database migrations".

TENANTS. A request or job runs inside tenant_scope(); once it knows the
workspace, set_tenant(ws). On Postgres every connection opened after that is
limited by row-level security to that workspace's rows (db_pg.py,
migrations/postgres.py). platform() is for code that must see across
workspaces. On SQLite these change nothing.
"""

import contextlib
import contextvars
import logging
import os
import sqlite3
import threading
import time

DB_PATH = os.getenv("APP_DB", os.path.join("config", "app.db"))
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
BUSY_TIMEOUT = 5                   # seconds
MIGRATIONS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "migrations")
# Row-level security for tenant connections on Postgres (DB_RLS=off: every
# connection runs as the login role, as on SQLite).
RLS = os.getenv("DB_RLS", "on").strip().lower() not in ("off", "0", "false", "no")

log = logging.getLogger(__name__)
_lock = threading.Lock()
_migrated = set()                  # databases brought up to date, this process
# Alembic keeps its `context` and `op` in module globals, so two Alembic commands at
# once in one process (a job's first connect() on its database while a test or a
# tool upgrades another) clobber each other's. Every one of them runs under this.
alembic_lock = threading.RLock()
# The current request's / job's tenant: a one-item list, shared by the threads
# its work runs in (a copied context keeps the same list), so a dependency that
# runs in a worker thread can still name the workspace for the endpoint.
_tenant = contextvars.ContextVar("kb_db_tenant", default=None)


def is_postgres():
    return bool(DATABASE_URL) and DATABASE_URL.split("://", 1)[0] in ("postgresql", "postgres")


def connect():
    """A connection to the app database, brought up to date first if this
    process hasn't yet."""
    if DATABASE_URL and not is_postgres():
        raise RuntimeError("DATABASE_URL must be a postgresql:// URL (unset it to use SQLite)")
    if is_postgres():
        url = DATABASE_URL
        if url not in _migrated:
            with _lock:
                if url not in _migrated:
                    migrate()
                    _migrated.add(url)
        import db_pg
        return db_pg.database(url).connect(tenant() if RLS else None)
    path = DB_PATH
    if path not in _migrated:
        with _lock:
            if path not in _migrated:
                migrate(path)
                _migrated.add(path)
    return _open(path)


# ---- Tenants ------------------------------------------------------------------------
@contextlib.contextmanager
def tenant_scope(workspace=None):
    """Run a request or a job as one workspace's (set_tenant names it later, for a
    request that learns it from the session)."""
    token = _tenant.set([workspace])
    try:
        yield
    finally:
        _tenant.reset(token)


def set_tenant(workspace):
    holder = _tenant.get()
    if holder is not None:
        holder[0] = workspace


def tenant():
    holder = _tenant.get()
    return holder[0] if holder else None


@contextlib.contextmanager
def platform():
    """Connections opened inside see every workspace's rows: for a check that is
    across workspaces by nature (is this domain verified by another one?)."""
    token = _tenant.set(None)
    try:
        yield
    finally:
        _tenant.reset(token)


def connect_platform():
    """connect(), seeing every workspace's rows (platform()), wherever it's called."""
    with platform():
        return connect()


# ---- The catalog, either backend ------------------------------------------------------
def tables(conn):
    """{table: [columns]} of the app's tables (not SQLite's own, nor FTS5's
    shadow tables, nor Alembic's)."""
    if isinstance(conn, sqlite3.Connection):
        names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
        names = [n for n in names if not n.startswith("sqlite_") and
                 not n.startswith("conversation_search_")]
        out = {n: [r[1] for r in conn.execute(f'PRAGMA table_info("{n}")')] for n in names}
    else:
        out = {}
        for table, column in conn.execute(
                "SELECT c.table_name, c.column_name FROM information_schema.columns c "
                "JOIN information_schema.tables t ON t.table_name = c.table_name "
                "AND t.table_schema = c.table_schema WHERE c.table_schema = current_schema() "
                "AND t.table_type = 'BASE TABLE' ORDER BY c.table_name, c.ordinal_position"):
            out.setdefault(table, []).append(column)
    out.pop("alembic_version", None)
    return out


# ---- SQLite ---------------------------------------------------------------------------
def _open(path, foreign_keys=True, rows=sqlite3.Row):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    conn = sqlite3.connect(path, timeout=BUSY_TIMEOUT)
    conn.row_factory = rows
    conn.execute(f"PRAGMA foreign_keys = {'ON' if foreign_keys else 'OFF'}")
    return conn


def migrate(path=None, url=None):
    """Upgrade the database to the newest migration: the SQLite file at `path`, or
    the Postgres database at `url`, else the app's database (Postgres when
    DATABASE_URL says so, else DB_PATH). Safe to run again, and from several
    processes at once: the first one takes the write lock (on Postgres an
    advisory lock) for the whole upgrade, the others wait and then find nothing
    to do."""
    from alembic import command
    from alembic.config import Config

    url = url or (DATABASE_URL if path is None and is_postgres() else None)
    if not url:
        path = path or DB_PATH
        use_wal(path)
    config = Config()
    config.set_main_option("script_location", MIGRATIONS)
    with alembic_lock, engine(path, url).begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    if url:
        import db_pg
        db_pg.database(url).catalog.forget()


def use_wal(path=None):
    """Put the database file in WAL mode (it stays so). Not inside a transaction.
    Switching needs the file to itself, and SQLite doesn't wait for that (no busy
    timeout): when another process is switching or migrating it right then, try
    again for up to BUSY_TIMEOUT seconds."""
    conn = _open(path or DB_PATH)
    try:
        deadline = time.monotonic() + BUSY_TIMEOUT
        while True:
            try:
                conn.execute("PRAGMA journal_mode = WAL")
                return
            except sqlite3.OperationalError as e:
                if "locked" not in str(e) or time.monotonic() > deadline:
                    raise
                time.sleep(0.05)
    finally:
        conn.close()


def engine(path=None, url=None):
    """A SQLAlchemy engine for migrations only (the stores use connect()).

    Postgres (`url`, or no `path` and DATABASE_URL set): psycopg 3; DDL is
    transactional, so a whole upgrade is one transaction there too.

    SQLite: its transactions begin with BEGIN IMMEDIATE, so a whole upgrade holds
    the write lock and is rolled back as a whole if any step fails (pysqlite would
    otherwise commit before each CREATE / ALTER). Foreign keys are OFF while
    migrating, as Alembic's batch mode needs: copying a table to change it must
    not cascade-delete the rows that point at it. env.py checks afterwards that
    no migration left a dangling reference."""
    import sqlalchemy
    from sqlalchemy.pool import NullPool

    url = url or (DATABASE_URL if path is None and is_postgres() else None)
    if url:
        import db_pg
        return sqlalchemy.create_engine(db_pg.sqlalchemy_url(url), poolclass=NullPool)

    path = path or DB_PATH
    eng = sqlalchemy.create_engine("sqlite://", poolclass=NullPool,
                                   creator=lambda: _open(path, foreign_keys=False, rows=None))

    @sqlalchemy.event.listens_for(eng, "connect")
    def _no_implicit_transactions(dbapi_conn, record):
        dbapi_conn.isolation_level = None

    @sqlalchemy.event.listens_for(eng, "begin")
    def _begin_immediate(conn):
        conn.exec_driver_sql("BEGIN IMMEDIATE")

    return eng
