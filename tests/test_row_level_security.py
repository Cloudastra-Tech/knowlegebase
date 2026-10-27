"""
Row-level security on Postgres (platform.md PL-04 step 3; migrations/postgres.py,
db_pg.py): a tenant's connections see and write only their workspace's rows,
in every tenant table; a connection without app.workspace sees nothing; the
platform (the login role) sees everything. Runs on the Postgres leg only.
"""

import os
import sqlite3
import uuid

import pytest

import db

pytestmark = pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"),
                                reason="row-level security is Postgres's (the CI Postgres leg)")

PASSWORD = "correct-horse"


def two_workspaces():
    import auth
    import conversations_store
    import members_store
    a, b = (f"rls-{x}-{uuid.uuid4().hex[:6]}" for x in "ab")
    for ws in (a, b):
        members_store.set_password(f"admin@{ws}.example", "Admin", auth.hash_password(PASSWORD),
                                   ws, "admin")
        c = conversations_store.create(ws, f"admin@{ws}.example", "public", title=f"chat of {ws}")
        conversations_store.add_message(c["id"], "user", f"a question in {ws}")
    return a, b


def test_a_query_without_the_setting_returns_nothing():
    import psycopg

    import db_pg
    two_workspaces()
    with psycopg.connect(db.DATABASE_URL, autocommit=True) as raw:
        everyone = raw.execute("SELECT COUNT(*) FROM members").fetchone()[0]
        assert everyone >= 2                                    # the platform sees them all
        raw.execute(f'SET ROLE "{db_pg.TENANT_ROLE}"')          # a tenant, no app.workspace
        for table in ("members", "workspaces", "conversations", "messages", "audit_events",
                      "question_log", "sessions"):
            assert raw.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0, table


def test_every_tenant_table_has_a_policy():
    import psycopg
    with psycopg.connect(db.DATABASE_URL) as raw:
        with_workspace = {r[0] for r in raw.execute(
            "SELECT table_name FROM information_schema.columns WHERE table_schema = "
            "current_schema() AND column_name = 'workspace'")}
        secured = {r[0] for r in raw.execute(
            "SELECT tablename FROM pg_policies WHERE schemaname = current_schema()")}
    assert with_workspace and with_workspace <= secured
    assert {"workspaces", "messages", "conversation_search", "scim_group_members"} <= secured


def test_a_tenant_sees_and_writes_only_its_own_workspace():
    a, b = two_workspaces()
    with db.tenant_scope(a):
        conn = db.connect()
        try:
            assert {r[0] for r in conn.execute("SELECT DISTINCT workspace FROM members")} == {a}
            assert {r[0] for r in conn.execute("SELECT slug FROM workspaces")} == {a}
            # Messages have no workspace column: theirs are hidden through their chat.
            assert {r[0] for r in conn.execute(
                "SELECT c.workspace FROM messages m JOIN conversations c "
                "ON c.id = m.conversation_id")} == {a}
            assert conn.execute("SELECT COUNT(*) FROM messages m WHERE NOT EXISTS (SELECT 1 "
                                "FROM conversations c WHERE c.id = m.conversation_id)"
                                ).fetchone()[0] == 0
            assert conn.execute("UPDATE members SET name = 'x' WHERE workspace = ?",
                                (b,)).rowcount == 0
            conn.commit()
            with pytest.raises(sqlite3.OperationalError, match="row-level security"):
                with conn:
                    conn.execute("INSERT INTO audit_events (workspace, at, actor_type, action, "
                                 "prev_hash, hash) VALUES (?, 1, 'user', 'x', 'p', 'h')", (b,))
        finally:
            conn.close()
    with db.platform():                                         # the platform: everyone
        conn = db.connect()
        try:
            assert {a, b} <= {r[0] for r in conn.execute("SELECT DISTINCT workspace FROM members")}
        finally:
            conn.close()


def test_a_signed_in_request_runs_as_its_workspace(monkeypatch):
    from fastapi.testclient import TestClient

    import api
    import db_pg
    a, _ = two_workspaces()
    seen = []
    checkout = db_pg.Database.checkout

    def spy(self, workspace):
        seen.append(workspace)
        return checkout(self, workspace)

    monkeypatch.setattr(db_pg.Database, "checkout", spy)
    client = TestClient(api.app, follow_redirects=False)
    token = client.post("/api/auth/login", json={"username": f"admin@{a}.example",
                                                 "password": PASSWORD}).json()["token"]
    seen.clear()
    r = client.get("/api/conversations", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    assert [c["title"] for c in r.json()["conversations"]] == [f"chat of {a}"]
    # Signing in looked up the session as the platform; the rest ran as the tenant.
    assert None in seen and a in seen
    assert set(seen) <= {None, a}


def test_an_api_key_request_runs_as_its_workspace(monkeypatch):
    from fastapi.testclient import TestClient

    import api
    import api_keys_store
    import db_pg
    a, _ = two_workspaces()
    _, key = api_keys_store.create(a, f"admin@{a}.example", "probe", ["docs:read"], "admin")
    seen = []
    checkout = db_pg.Database.checkout

    def spy(self, workspace):
        seen.append(workspace)
        return checkout(self, workspace)

    monkeypatch.setattr(db_pg.Database, "checkout", spy)
    r = TestClient(api.app).get("/api/v1/documents", headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 200, r.text
    # The key was looked up as the platform; the rest ran as the tenant.
    assert None in seen and a in seen and set(seen) <= {None, a}


def test_a_workspaces_job_runs_as_that_tenant(jobs_paused, monkeypatch):
    import db_pg
    from kb import jobs
    a, _ = two_workspaces()
    seen = {}

    @jobs.handler("rls_probe")
    def probe(job):
        conn = db.connect()
        try:
            seen["tenant"] = conn.workspace
            seen["workspaces"] = {r[0] for r in conn.execute("SELECT slug FROM workspaces")}
        finally:
            conn.close()

    try:
        jobs.enqueue("rls_probe", a)
        assert jobs.Runner({"rls_probe": jobs.HANDLERS["rls_probe"]}, threads=1).run_once()
    finally:
        jobs.HANDLERS.pop("rls_probe", None)
    assert seen == {"tenant": a, "workspaces": {a}}
    assert db_pg.TENANT_ROLE
