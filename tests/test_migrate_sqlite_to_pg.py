"""
python -m tools.migrate_sqlite_to_pg: an existing config/app.db (an old one from
tests/fixtures/legacy_dbs, upgraded, plus chats, audit events and a search
index) copied into an empty Postgres database, verified per table, and safe to
run again. Runs on the Postgres leg (TEST_DATABASE_URL); skipped on SQLite.
"""

import os
import pathlib
import sqlite3
import uuid

import pytest

import db

pytestmark = pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"),
                                reason="needs a Postgres server (the CI Postgres leg)")

FIXTURE = sorted((pathlib.Path(__file__).parent / "fixtures" / "legacy_dbs").glob("*.sql"))[-1]


@pytest.fixture
def target():
    """A new, empty Postgres database: its URL."""
    import psycopg
    server = os.environ["TEST_DATABASE_URL"]
    name = f"kb_copy_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(server, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    base, _, query = server.partition("?")
    yield base.rsplit("/", 1)[0] + "/" + name + (f"?{query}" if query else "")
    import db_pg
    db_pg.close_all()
    with psycopg.connect(server, autocommit=True) as admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture
def source(tmp_path):
    """An app.db at the newest migration, with rows in the old tables and the new."""
    path = str(tmp_path / "app.db")
    with sqlite3.connect(path) as conn:
        conn.executescript(FIXTURE.read_text())
    conn.close()
    db.migrate(path)
    conn = sqlite3.connect(path)
    ws = conn.execute("SELECT slug FROM workspaces LIMIT 1").fetchone()[0]
    conn.execute("INSERT INTO conversations (id, workspace, owner, title, role_used, "
                 "created_at, updated_at) VALUES ('c1', ?, 'pat', 'Refunds', 'public', 1, 2)",
                 (ws,))
    # Two messages of the same moment: their order is SQLite's rowid.
    for mid, author in (("m-b", "user"), ("m-a", "assistant")):
        conn.execute("INSERT INTO messages (id, conversation_id, author, content, created_at) "
                     "VALUES (?, 'c1', ?, 'how do refunds work?', 5.5)", (mid, author))
    conn.execute("INSERT INTO conversation_search (body, conversation_id, message_id, access) "
                 "VALUES ('how do refunds work?', 'c1', 'm-b', 'public')")
    conn.execute("INSERT INTO audit_events (workspace, at, actor_type, action, details, "
                 "prev_hash, hash) VALUES (?, 1.25, 'user', 'member.update', '{}', 'x', 'y')",
                 (ws,))
    conn.execute("INSERT INTO workspace_keys (workspace, version, wrapped_dek, created_at) "
                 "VALUES (?, 1, ?, 3)", (ws, b"\x00\x01binary"))
    # An id given out and deleted since: never handed out again.
    conn.execute("UPDATE sqlite_sequence SET seq = 41 WHERE name = 'audit_events'")
    conn.commit()
    conn.close()
    return path


def _copy(source, target, **kwargs):
    from tools import migrate_sqlite_to_pg as tool
    return tool.copy(source, target, out=lambda *a: None, **kwargs)


def test_every_table_is_copied_and_verified(source, target):
    import psycopg
    report = _copy(source, target)
    assert report and all(same for _, _, same in report.values())
    src = sqlite3.connect(source)
    for table, (n_src, n_dst, _) in report.items():
        assert n_src == n_dst == src.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
    with psycopg.connect(target) as pg:
        assert [r[0] for r in pg.execute(
            "SELECT id FROM messages ORDER BY created_at, rowid")] == ["m-b", "m-a"]
        assert pg.execute("SELECT wrapped_dek FROM workspace_keys").fetchone()[0] == \
            b"\x00\x01binary"
        # The chat index is Postgres's own now, and finds the copied row.
        assert pg.execute("SELECT message_id FROM conversation_search WHERE "
                          "tsv @@ to_tsquery('kb_search', 'refund:*')").fetchone()[0] == "m-b"
        # The next audit event's id is past every id SQLite gave out.
        new = pg.execute("INSERT INTO audit_events (workspace, at, actor_type, action, "
                         "prev_hash, hash) VALUES ('x', 1, 'user', 'a', 'p', 'h') "
                         "RETURNING id").fetchone()[0]
        assert new > 41


def test_running_it_again_changes_nothing(source, target):
    first = _copy(source, target)
    assert _copy(source, target) == first


def test_different_rows_in_postgres_are_refused_unless_overwritten(source, target):
    import psycopg
    _copy(source, target)
    with psycopg.connect(target) as pg:
        pg.execute("UPDATE messages SET content = 'changed' WHERE id = 'm-a'")
    assert not _copy(source, target, verify_only=True)["messages"][2]
    with pytest.raises(SystemExit, match="different rows"):
        _copy(source, target)
    report = _copy(source, target, overwrite=True)
    assert all(same for _, _, same in report.values())


def test_a_database_behind_the_newest_migration_is_refused(tmp_path, target):
    path = str(tmp_path / "old.db")
    with sqlite3.connect(path) as conn:
        conn.executescript(FIXTURE.read_text())
    conn.close()
    with pytest.raises(SystemExit, match="upgrade it first"):
        _copy(path, target)
