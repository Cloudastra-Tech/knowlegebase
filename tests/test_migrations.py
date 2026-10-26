"""
db.py and migrations/: an empty database gets the baseline; a config/app.db
made before Alembic, at any age, is upgraded in place with every row kept and
ends up with the same schema as a new one.

The old databases are tests/fixtures/legacy_dbs/*.sql: each made by the stores
as they were at one commit, with rows in every table (make_fixtures.py there).
"""

import pathlib
import re
import sqlite3
import threading

import pytest

import db
import members_store
from migrations import legacy

FIXTURES = sorted((pathlib.Path(__file__).parent / "fixtures" / "legacy_dbs").glob("*.sql"))
INTERNAL = {"sqlite_sequence", "alembic_version"}
HEAD = "0008"                                    # the newest migration
# Made by migrations after the baseline.
NEWER_TABLES = {"jobs", "search_indexes", "conversations", "messages", "message_feedback",
                "audit_events", "invites", "sync_state", "source_items", "sync_runs",
                "scim_tokens", "scim_groups", "scim_group_members", "scim_group_rules",
                "question_log", "insights_daily", "source_stats", "insights_docs", "content_gaps",
                "eval_candidates", "chunk_contexts"}
# Empty columns the pre-Alembic data fixes fill in (migrations/legacy.py).
FILLED = {("members", "override_role"), ("members", "groups_updated_at"),
          ("workspaces", "role_source"), ("sessions", "username"), ("sessions", "workspace")}


def _load(path, sql):
    with sqlite3.connect(path) as conn:
        conn.executescript(sql)
    conn.close()


def _connect(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _checks(sql):
    """Every CHECK (...) in a CREATE TABLE, comments dropped and spacing evened out."""
    sql = re.sub(r"--[^\n]*", "", sql)
    found = []
    for start in (m.end() for m in re.finditer(r"CHECK\s*\(", sql)):
        depth, i = 1, start
        while depth:
            depth += {"(": 1, ")": -1}.get(sql[i], 0)
            i += 1
        body = re.sub(r"\s+", " ", sql[start:i - 1]).strip()
        found.append(re.sub(r"\(\s+|\s+\)", lambda m: m.group().strip(), body))
    return sorted(found)


def schema(path):
    """The schema as SQLite sees it, whatever text made it: per table its columns
    (in order), keys, CHECKs, foreign keys and indexes (with a partial index's
    WHERE)."""
    conn = _connect(path)
    out = {}
    for name, sql in conn.execute("SELECT name, sql FROM sqlite_master WHERE type = 'table'"):
        if name in INTERNAL:
            continue
        indexes = {}
        for ix in conn.execute(f"PRAGMA index_list({name})"):
            where = conn.execute("SELECT sql FROM sqlite_master WHERE name = ?",
                                 (ix["name"],)).fetchone()[0]
            if where:
                where = re.sub(r"\s+", " ", where.split(" WHERE ", 1)[1]) if ix["partial"] else ""
            key = ix["name"] if ix["origin"] == "c" else ix["origin"]
            indexes[key] = (ix["unique"], ix["partial"], where,
                            [c["name"] for c in conn.execute(f"PRAGMA index_info({ix['name']})")])
        out[name] = {
            "columns": [(c["name"], c["type"], c["notnull"], c["dflt_value"], c["pk"])
                        for c in conn.execute(f"PRAGMA table_xinfo({name})")],
            "foreign_keys": sorted((f["table"], f["from"], f["to"], f["on_update"], f["on_delete"])
                                   for f in conn.execute(f"PRAGMA foreign_key_list({name})")),
            "checks": _checks(sql),
            "autoincrement": "AUTOINCREMENT" in sql.upper(),
            "indexes": indexes,
        }
    conn.close()
    return out


def rows(path):
    conn = _connect(path)
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
              if r[0] not in INTERNAL]
    out = {t: {r["_rowid"]: {k: r[k] for k in r.keys() if k != "_rowid"}
               for r in conn.execute(f"SELECT rowid AS _rowid, * FROM {t}")} for t in tables}
    conn.close()
    return out


def tables(path):
    conn = sqlite3.connect(path)
    try:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    finally:
        conn.close()


def version(path):
    conn = sqlite3.connect(path)
    try:
        return [r[0] for r in conn.execute("SELECT version_num FROM alembic_version")]
    finally:
        conn.close()


@pytest.fixture
def fresh(tmp_path):
    path = str(tmp_path / "fresh.db")
    db.migrate(path)
    return path


def test_an_empty_database_gets_every_table_at_the_newest_migration(fresh):
    assert version(fresh) == [HEAD]
    assert set(schema(fresh)) == legacy.APP_TABLES | NEWER_TABLES
    conn = sqlite3.connect(fresh)
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    conn.close()


def test_the_baseline_keeps_the_rules_the_stores_rely_on(fresh):
    s = schema(fresh)
    assert s["workspace_domains"]["indexes"]["one_verified_owner"] == (
        1, 1, "verified_at IS NOT NULL", ["domain"])
    assert ("workspaces", "workspace", "slug", "NO ACTION", "CASCADE") in s["members"]["foreign_keys"]
    assert s["sso_events"]["autoincrement"]
    allowed = ", ".join(f"'{o}'" for o in members_store.EVENT_OUTCOMES)
    assert f"outcome IN ({allowed})" in s["sso_events"]["checks"]


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda p: p.stem)
def test_an_old_database_is_upgraded_in_place_keeping_every_row(fixture, fresh, tmp_path):
    path = str(tmp_path / "old.db")
    _load(path, fixture.read_text())
    before = rows(path)
    assert before and not any(t == "alembic_version" for t in before)

    db.migrate(path)

    assert version(path) == [HEAD]
    assert schema(path) == schema(fresh)
    after = rows(path)
    for table, old in before.items():
        assert old.keys() <= after[table].keys(), table      # no row lost
        if table != "imports":                                # (which marks the fixes done)
            assert old.keys() == after[table].keys(), table
        for rowid, row in old.items():
            for column, value in row.items():
                # Every value kept, except empty ones the old data fixes exist to fill.
                if not (value is None and (table, column) in FILLED):
                    assert after[table][rowid][column] == value, (table, rowid, column)
    conn = sqlite3.connect(path)
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()

    db.migrate(path)                                 # again: nothing to do, nothing lost
    assert rows(path) == after and version(path) == [HEAD]


def _upgraded(fixture, tmp_path):
    path = str(tmp_path / "old.db")
    _load(path, (FIXTURES[0].parent / fixture).read_text())
    db.migrate(path)
    return _connect(path)


def test_roles_set_by_hand_before_group_roles_become_overrides(tmp_path):
    conn = _upgraded("2026-10-15-33ff439.sql", tmp_path)
    roles = {r["username"]: (r["role"], r["override_role"])
             for r in conn.execute("SELECT * FROM members")}
    assert roles == {"boss": ("hr", "hr"), "pat@acme.com": ("public", None),
                     "admin@acme.com": ("admin", "admin")}


def test_a_database_from_before_role_sources_gets_one(tmp_path):
    conn = _upgraded("2026-10-15-b64ccab.sql", tmp_path)
    # c2 has rules and signed someone in last; c1 has no rules.
    assert conn.execute("SELECT role_source FROM workspaces WHERE slug = 'acme'").fetchone()[0] == "c2"
    pat = conn.execute("SELECT * FROM members WHERE username = 'pat@acme.com'").fetchone()
    assert pat["groups_updated_at"] == pat["last_login_at"] == 50.0


def test_an_old_event_log_takes_the_new_outcomes(tmp_path):
    conn = _upgraded("2026-10-15-b64ccab.sql", tmp_path)
    with conn:
        conn.execute("INSERT INTO sso_events (workspace, outcome, at) VALUES ('acme', 'attention', 1)")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO sso_events (workspace, outcome, at) VALUES ('acme', 'other', 1)")
    assert [r[0] for r in conn.execute("SELECT id FROM sso_events ORDER BY id")] == [1, 2, 3]


def test_sessions_from_before_the_whose_columns_say_whose_they_are(tmp_path):
    conn = _upgraded("2026-10-14-89a4ad7.sql", tmp_path)
    assert sorted(tuple(r) for r in conn.execute("SELECT username, workspace FROM sessions")) == [
        ("boss", "default"), ("pat@acme.com", "acme")]


def test_the_stores_read_an_old_database_after_it_is_upgraded(tmp_path, monkeypatch):
    """Through the stores, as the app would on its first request after a deploy.
    Before the SSO epic, accounts were in config/users.json: they're copied in."""
    import integrations_store
    import sessions_store
    path = str(tmp_path / "app.db")
    _load(path, FIXTURES[0].read_text())                   # from before the SSO epic
    (tmp_path / "users.json").write_text(
        '{"boss": {"name": "Boss", "password": "pbkdf2$1$aa$bb", "role": "hr"}}')
    monkeypatch.setattr(db, "DB_PATH", path)
    monkeypatch.setattr(db, "_migrated", set())
    monkeypatch.setattr(members_store, "_ready", False)
    monkeypatch.setattr(members_store, "USERS_FILE", str(tmp_path / "users.json"))
    monkeypatch.setattr(members_store, "SSO_FILE", str(tmp_path / "sso.json"))
    assert sessions_store.delete_user("pat@acme.com", "acme") == 1
    assert integrations_store.get("acme", "google_drive")["doc_count"] == 3
    boss = members_store.get("boss")
    assert (boss["role"], boss["override_role"], boss["workspace"]) == ("hr", "hr", "default")


def test_a_failed_upgrade_changes_nothing(tmp_path, monkeypatch):
    path = str(tmp_path / "old.db")
    _load(path, FIXTURES[0].read_text())
    before = (schema(path), rows(path))

    def broken(conn):
        raise RuntimeError("boom")
    monkeypatch.setattr(legacy, "_sessions_say_whose", broken)   # after the tables are made
    with pytest.raises(RuntimeError, match="boom"):
        db.migrate(path)
    assert (schema(path), rows(path)) == before
    assert "alembic_version" not in tables(path)


def test_workers_starting_together_upgrade_it_once(tmp_path):
    path = str(tmp_path / "old.db")
    _load(path, FIXTURES[-1].read_text())
    errors = []

    def start():
        try:
            db.migrate(path)
        except Exception as e:                        # pragma: no cover - reported below
            errors.append(e)
    workers = [threading.Thread(target=start) for _ in range(4)]
    for w in workers:
        w.start()
    for w in workers:
        w.join()
    assert errors == [] and version(path) == [HEAD]


def test_connections_have_the_settings_the_stores_rely_on(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "sub" / "app.db"))
    monkeypatch.setattr(db, "_migrated", set())
    conn = db.connect()
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == db.BUSY_TIMEOUT * 1000
    row = conn.execute("SELECT 'x' AS name").fetchone()
    assert row["name"] == row[0] == "x"


def test_the_alembic_command_line_upgrades_the_same_way(tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config
    path = str(tmp_path / "old.db")
    _load(path, FIXTURES[0].read_text())
    monkeypatch.setattr(db, "DB_PATH", path)
    config = Config(str(pathlib.Path(db.__file__).with_name("alembic.ini")))
    command.current(config)                      # looking doesn't adopt it
    assert tables(path) == {"sessions", "integrations"}
    command.upgrade(config, "head")
    assert version(path) == [HEAD] and "members" in tables(path)
    assert rows(path)["sessions"][1]["workspace"] == "acme"
