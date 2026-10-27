"""
GET /api/health (the process is up) and GET /api/health/ready (it can serve):
a real SQLite file, a real Chroma folder and a real Fernet key, each broken in
turn.
"""

import os
import sqlite3

import pytest
from cryptography.fernet import Fernet

# Each test breaks a real SQLite file or Chroma folder; Postgres's readiness is
# checked in tests/test_postgres_migrations.py.
pytestmark = pytest.mark.sqlite_only("breaks the SQLite file and the Chroma folder themselves")


@pytest.fixture
def server(tmp_path, monkeypatch):
    """A healthy server: a database holding one encrypted secret, a search index,
    and the key that encrypted it."""
    from fastapi.testclient import TestClient
    import api
    import db as app_db
    import ingest
    import secret_box

    key = Fernet.generate_key().decode()
    monkeypatch.setenv("INTEGRATIONS_KEY", key)
    db = tmp_path / "app.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE integrations (workspace TEXT, provider TEXT, secret BLOB)")
        conn.execute("INSERT INTO integrations VALUES ('acme', 'notion', ?)",
                     (secret_box.seal({"token": "t"}),))
    conn.close()
    index = tmp_path / "chroma_db"
    import chromadb
    chromadb.PersistentClient(path=str(index)).get_or_create_collection("langchain")

    monkeypatch.setattr(app_db, "DB_PATH", str(db))
    monkeypatch.setattr(ingest, "DB_FOLDER", str(index))
    monkeypatch.setattr(secret_box, "KEY_PATH", str(tmp_path / "integrations.key"))
    client = TestClient(api.app)
    client.paths = {"db": db, "index": index, "key": tmp_path / "integrations.key"}
    return client


def ready(client):
    r = client.get("/api/health/ready")
    return r.status_code, r.json()


def test_a_healthy_server_is_ready(server):
    status, body = ready(server)
    assert (status, body["ok"]) == (200, True)
    assert body["checks"] == {"database": "ok", "search_index": "ok", "integrations_key": "ok"}


def test_liveness_stays_200_when_not_ready(server, monkeypatch):
    import db as app_db
    monkeypatch.setattr(app_db, "DB_PATH", str(server.paths["db"]) + ".missing")
    assert ready(server)[0] == 503
    r = server.get("/api/health")
    assert (r.status_code, r.json()["ok"]) == (200, True)


def test_a_missing_database_is_not_ready(server):
    server.paths["db"].unlink()
    status, body = ready(server)
    assert status == 503 and body["ok"] is False
    assert body["checks"]["database"] == "no database file"
    assert body["checks"]["search_index"] == "ok"
    assert not server.paths["db"].exists()          # reported, not created


def test_a_corrupt_database_is_not_ready(server):
    server.paths["db"].write_bytes(b"not a database" * 100)
    status, body = ready(server)
    assert status == 503
    assert body["checks"]["database"] == "failed (DatabaseError)"


def test_a_missing_search_index_is_not_ready(server, monkeypatch):
    import ingest
    monkeypatch.setattr(ingest, "DB_FOLDER", str(server.paths["index"]) + "-gone")
    status, body = ready(server)
    assert status == 503
    assert body["checks"] == {"database": "ok", "integrations_key": "ok",
                              "search_index": "no search index folder (run ingest.py)"}


def test_a_search_index_that_wont_open_is_not_ready(server, monkeypatch, tmp_path):
    import ingest
    broken = tmp_path / "broken_index"
    broken.mkdir()
    (broken / "chroma.sqlite3").write_bytes(b"garbage" * 100)
    monkeypatch.setattr(ingest, "DB_FOLDER", str(broken))
    status, body = ready(server)
    assert status == 503
    assert body["checks"]["search_index"].startswith("failed (")


def test_a_changed_key_is_not_ready(server, monkeypatch):
    monkeypatch.setenv("INTEGRATIONS_KEY", Fernet.generate_key().decode())
    status, body = ready(server)
    assert status == 503
    assert body["checks"]["integrations_key"] == \
        "the encryption key doesn't decrypt the stored secrets"


def test_a_malformed_key_is_not_ready(server, monkeypatch):
    monkeypatch.setenv("INTEGRATIONS_KEY", "not-a-fernet-key")
    status, body = ready(server)
    assert status == 503
    assert body["checks"]["integrations_key"] == "the encryption key is not a valid key"
    assert "not-a-fernet-key" not in str(body)


def test_stored_secrets_without_a_key_is_not_ready(server, monkeypatch):
    monkeypatch.delenv("INTEGRATIONS_KEY")
    status, body = ready(server)
    assert status == 503
    assert body["checks"]["integrations_key"] == \
        "secrets are stored but no encryption key is set"
    assert not server.paths["key"].exists()         # the check never makes a key


def test_the_key_file_is_used_without_the_env_var(server, monkeypatch):
    server.paths["key"].write_text(os.environ["INTEGRATIONS_KEY"])
    monkeypatch.delenv("INTEGRATIONS_KEY")
    assert ready(server)[0] == 200


def test_a_fresh_install_without_secrets_or_key_is_ready(server, monkeypatch):
    monkeypatch.delenv("INTEGRATIONS_KEY")
    with sqlite3.connect(server.paths["db"]) as conn:
        conn.execute("DELETE FROM integrations")
    conn.close()
    assert ready(server)[0] == 200
    assert not server.paths["key"].exists()


@pytest.mark.parametrize("active, required, status, check", [
    (False, False, 200, None),       # reported under "guards", not a failed check
    (False, True, 503, "REQUIRE_TOXICITY_GUARD is set but the toxicity guard is not installed"),
    (True, True, 200, "ok"),
])
def test_readiness_reports_the_toxicity_guard(server, monkeypatch, active, required, status, check):
    import safety
    monkeypatch.setattr(safety, "toxicity_guard_active", active)
    if required:
        monkeypatch.setenv("REQUIRE_TOXICITY_GUARD", "1")
    else:
        monkeypatch.delenv("REQUIRE_TOXICITY_GUARD", raising=False)
    got, body = ready(server)
    assert got == status
    assert body["checks"].get("toxicity_guard") == check
    assert body["guards"] == {"toxicity": "active" if active else "missing",
                              "toxicity_required": required}
