"""
The migrations on Postgres (migrations/dialect.py, migrations/postgres.py): an
empty database gets the same tables and columns as a new SQLite one, with the
Postgres versions of what SQLite does its own way (the chat index, the trigger,
the widened CHECKs), and upgrading again changes nothing. On the Postgres leg only.
"""

import os
import sqlite3

import pytest

import db

pytestmark = pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"),
                                reason="needs a Postgres server (the CI Postgres leg)")


def _sqlite_schema(tmp_path):
    path = str(tmp_path / "fresh.db")
    db.migrate(path)
    conn = sqlite3.connect(path)
    try:
        return db.tables(conn)
    finally:
        conn.close()


def _pg_schema():
    conn = db.connect()
    try:
        return db.tables(conn)
    finally:
        conn.close()


def test_postgres_gets_the_same_tables_and_columns_as_sqlite(tmp_path, fresh_db):
    lite, pg = _sqlite_schema(tmp_path), _pg_schema()
    pg = {t: cols for t, cols in pg.items() if not t.startswith("kb_")}   # pgvector's own
    assert set(lite) == set(pg)
    for table, columns in lite.items():
        extra = {"rowid"} if table == "messages" else {"tsv"} if \
            table == "conversation_search" else set()
        assert set(pg[table]) == set(columns) | extra, table


def test_what_sqlite_does_its_own_way_is_there(fresh_db):
    conn = db.connect()
    try:
        assert conn.execute("SELECT 1 FROM pg_trigger WHERE tgname = "
                            "'question_log_forget_message'").fetchone()
        checks = dict(conn.execute(
            "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint WHERE conname IN "
            "('question_log_surface_check', 'sso_events_outcome_check')").fetchall())
        assert "'email'" in checks["question_log_surface_check"]
        assert "'scim'" in checks["sso_events_outcome_check"]
        assert conn.execute("SELECT 1 FROM pg_collation WHERE collname = 'nocase'").fetchone()
        # Deleting a chat still clears its words from the question log.
        conn.execute("INSERT INTO conversations (id, workspace, owner, role_used, created_at, "
                     "updated_at) VALUES ('c1', 'w', 'u', 'public', 1, 1)")
        conn.execute("INSERT INTO messages (id, conversation_id, author, content, created_at) "
                     "VALUES ('m1', 'c1', 'user', 'q', 1)")
        conn.execute("INSERT INTO question_log (workspace, at, day, user, surface, message_id, "
                     "text_mode, question, outcome) VALUES ('w', 1, 'd', 'u', 'email', 'm1', "
                     "'full', 'my question', 'answered')")
        conn.execute("DELETE FROM conversations WHERE id = 'c1'")
        assert conn.execute("SELECT question, message_id FROM question_log").fetchone() == \
            (None, None)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO question_log (workspace, at, day, user, surface, "
                         "text_mode, outcome) VALUES ('w', 1, 'd', 'u', 'fax', 'full', 'answered')")
        conn.commit()
    finally:
        conn.close()


def test_upgrading_again_changes_nothing(fresh_db):
    before = _pg_schema()
    db.migrate()
    assert _pg_schema() == before
    conn = db.connect()
    try:
        assert [r[0] for r in conn.execute("SELECT version_num FROM alembic_version")] == \
            [__import__("tools.migrate_sqlite_to_pg", fromlist=["_head"])._head()]
    finally:
        conn.close()
