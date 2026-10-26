"""
db.py  —  the app's one SQLite database (config/app.db): where it is, how it's opened,
and bringing its schema up to date.

Every store (members_store, sessions_store, integrations_store) gets its
connections from connect(), and secret_box keeps its key file beside DB_PATH.
Each connection:

    foreign_keys = ON    the ON DELETE CASCADEs in the schema apply
    busy timeout         a writer waits up to BUSY_TIMEOUT seconds for another
                         one instead of failing with "database is locked"
    sqlite3.Row rows     columns by name (row["role"]) or by position

The file is in WAL mode (set once, it's kept in the file): readers don't wait
for a writer, and a writer doesn't wait for readers.

The schema is kept by Alembic migrations (migrations/versions/). The first
connect() in a process upgrades the database to the newest one, all or nothing,
before anything else uses it; a database from before Alembic is first brought
to the baseline and marked as at it (migrations/legacy.py). `alembic upgrade
head` does the same from the command line. How to add one: ARCHITECTURE.md,
"Database migrations".
"""

import logging
import os
import sqlite3
import threading

DB_PATH = os.getenv("APP_DB", os.path.join("config", "app.db"))
BUSY_TIMEOUT = 5                   # seconds
MIGRATIONS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "migrations")

log = logging.getLogger(__name__)
_lock = threading.Lock()
_migrated = set()                  # database files brought up to date, this process


def connect():
    """A connection to the app database, brought up to date first if this
    process hasn't yet."""
    path = DB_PATH
    if path not in _migrated:
        with _lock:
            if path not in _migrated:
                migrate(path)
                _migrated.add(path)
    return _open(path)


def _open(path, foreign_keys=True, rows=sqlite3.Row):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    conn = sqlite3.connect(path, timeout=BUSY_TIMEOUT)
    conn.row_factory = rows
    conn.execute(f"PRAGMA foreign_keys = {'ON' if foreign_keys else 'OFF'}")
    return conn


def migrate(path=None):
    """Upgrade the database at `path` (default DB_PATH) to the newest migration.
    Safe to run again, and from several processes at once: the first one takes
    the write lock for the whole upgrade, the others wait and then find nothing
    to do."""
    from alembic import command
    from alembic.config import Config

    path = path or DB_PATH
    use_wal(path)
    config = Config()
    config.set_main_option("script_location", MIGRATIONS)
    with engine(path).begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")


def use_wal(path=None):
    """Put the database file in WAL mode (it stays so). Not inside a transaction."""
    conn = _open(path or DB_PATH)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.close()


def engine(path=None):
    """A SQLAlchemy engine for migrations only (the stores use plain sqlite3).

    Its transactions begin with BEGIN IMMEDIATE, so a whole upgrade holds the
    write lock and is rolled back as a whole if any step fails (pysqlite would
    otherwise commit before each CREATE / ALTER). Foreign keys are OFF while
    migrating, as Alembic's batch mode needs: copying a table to change it must
    not cascade-delete the rows that point at it. env.py checks afterwards that
    no migration left a dangling reference."""
    import sqlalchemy
    from sqlalchemy.pool import NullPool

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
