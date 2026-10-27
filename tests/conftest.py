"""
Point every test at throwaway files BEFORE any app module is imported (the
stores read these paths once, at import), so no test can touch config/app.db
or the real accounts, whichever test file pytest collects first.

The developer's .env is never loaded either, so tests run with the same
settings here as in CI (which has no .env).
"""

import atexit
import os
import shutil
import tempfile

import dotenv
import pytest

dotenv.load_dotenv = lambda *args, **kwargs: False

_tmp = tempfile.mkdtemp(prefix="kb-tests-")
atexit.register(shutil.rmtree, _tmp, ignore_errors=True)
os.environ.setdefault("OPENAI_API_KEY", "sk-test")
os.environ["RATE_LIMITS"] = "off"         # every test signs in from the same address
# Starting the app doesn't rebuild indexes other tests left with other settings.
os.environ["REINDEX_ON_SETTINGS_CHANGE"] = "off"
# No model calls for a chat's title or its follow-ups (kb/services/suggestions.py):
# the question's own words, and follow-ups from the cited passages' headings.
os.environ.update(CHAT_AUTO_TITLE="off", CHAT_FOLLOWUPS="headings")
os.environ.update(APP_DB=os.path.join(_tmp, "app.db"),
                  USERS_FILE=os.path.join(_tmp, "users.json"),
                  SSO_CONFIG=os.path.join(_tmp, "sso.json"))

# THE POSTGRES LEG (db.py, platform.md PL-04). TEST_DATABASE_URL names a Postgres
# server the tests may make databases on (CI's service container): the run gets a
# new, empty database of its own there, dropped at the end, as DATABASE_URL. A
# DATABASE_URL already set (a real one, from a shell) is never used by tests.
os.environ.pop("DATABASE_URL", None)
POSTGRES = bool(os.getenv("TEST_DATABASE_URL"))
if POSTGRES:
    import uuid

    import psycopg

    _server = os.environ["TEST_DATABASE_URL"]
    _name = f"kb_test_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(_server, autocommit=True) as _admin:
        _admin.execute(f'CREATE DATABASE "{_name}"')
    _base, _, _query = _server.partition("?")
    os.environ["DATABASE_URL"] = (_base.rsplit("/", 1)[0] + "/" + _name
                                  + (f"?{_query}" if _query else ""))

    def _drop_database():
        import db_pg
        db_pg.close_all()
        with psycopg.connect(_server, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{_name}" WITH (FORCE)')

    atexit.register(_drop_database)


_template = None


def fresh_database(monkeypatch, tmp_path):
    """Point the app at an empty database of the test's own: a new SQLite file, or
    on the Postgres leg a new database (copied from an empty, migrated template),
    dropped afterwards. Returns a function that drops it (the fixture's teardown)."""
    import db
    monkeypatch.setattr(db, "_migrated", set())
    if not POSTGRES:
        monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "app.db"))
        return lambda: None
    import psycopg

    import db_pg
    global _template
    base = os.environ["DATABASE_URL"].rsplit("/", 1)[0]
    if _template is None:
        _template = f"{_name}_template"
        with psycopg.connect(_server, autocommit=True) as admin:
            admin.execute(f'CREATE DATABASE "{_template}"')
        db.migrate(url=f"{base}/{_template}")
        db_pg.database(f"{base}/{_template}").close()
        atexit.register(_drop, _template)
    name = f"{_name}_{len(os.listdir(tmp_path))}_{os.urandom(3).hex()}"
    with psycopg.connect(_server, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}" TEMPLATE "{_template}"')
    url = f"{base}/{name}"
    monkeypatch.setattr(db, "DATABASE_URL", url)

    def drop():
        db_pg.database(url).close()
        _drop(name)
    return drop


@pytest.fixture
def fresh_db(monkeypatch, tmp_path):
    """An empty database of the test's own, on either backend (fresh_database)."""
    drop = fresh_database(monkeypatch, tmp_path)
    yield
    drop()


def _drop(name):
    import psycopg
    with psycopg.connect(_server, autocommit=True) as admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "sqlite_only(reason): about the SQLite file itself (its migrations from "
                   "pre-Alembic copies, WAL, PRAGMAs, the backup tool); skipped on the "
                   "Postgres leg")


def pytest_collection_modifyitems(config, items):
    if not POSTGRES:
        return
    for item in items:
        marker = item.get_closest_marker("sqlite_only")
        if marker:
            reason = marker.kwargs.get("reason") or (marker.args[0] if marker.args else "")
            item.add_marker(pytest.mark.skip(reason=f"SQLite only: {reason}"))


@pytest.fixture
def jobs_paused(monkeypatch):
    """No job runner in this process: jobs stay queued until the test runs them
    (kb.jobs.Runner(...).run_once()). The next test that queues one starts it again."""
    from kb import jobs
    monkeypatch.setenv("JOBS_IN_PROCESS", "off")
    jobs.stop_in_process()
