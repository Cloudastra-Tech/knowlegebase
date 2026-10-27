"""
Per-workspace keys (secret_box.py, kb/services/keys.py; platform.md PL-05): on a
database of its own holding real secrets sealed the old way (by the server key)
through each store that keeps them (integrations, SSO providers, the Slack app,
Outline's token), the re-seal job moves every one under its workspace's data
key, resumably, with each still readable at every step; rotation and a new
server key keep them readable; shredding a workspace's keys makes its old
ciphertexts unreadable; hosted mode never makes a key file.
"""

import pytest
from cryptography.fernet import Fernet

import db
import secret_box

WORKSPACES = ("acme", "globex")


@pytest.fixture
def fresh(tmp_path, monkeypatch, fresh_db):
    """A database and server key of their own."""
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "app.db"))
    monkeypatch.setattr(db, "_migrated", set())
    monkeypatch.setattr(secret_box, "KEY_PATH", str(tmp_path / "integrations.key"))
    monkeypatch.setenv("INTEGRATIONS_KEY", Fernet.generate_key().decode())
    for name in ("APP_ENV", "HOSTED", "KEK_PROVIDER"):
        monkeypatch.delenv(name, raising=False)
    secret_box.forget()
    yield tmp_path
    secret_box.forget()


def stored(ws):
    """Every secret of a workspace, read back through its store."""
    import integrations_store
    import members_store
    import slack_store
    (conn,) = members_store.list_connections(ws)
    return {"notion": integrations_store.secret(ws, "notion"),
            "outline": integrations_store.secret(ws, "outline"),
            "sso": members_store.connection_keys(conn["id"]),
            "slack_app": slack_store.app_secrets(ws), "slack_bot": slack_store.bot_token(ws)}


def expected(ws):
    return {"notion": {"access_token": f"notion-{ws}"}, "outline": {"token": f"outline-{ws}"},
            "sso": {"client_secret": f"oidc-{ws}"},
            "slack_app": {"client_secret": f"sc-{ws}", "signing_secret": f"ss-{ws}"},
            "slack_bot": f"xoxb-{ws}"}


def blobs():
    conn = db.connect()
    try:
        out = []
        for table, column, _ in secret_box.SEALED:
            out += [(table, column, r[0], bytes(r[1])) for r in conn.execute(
                f"SELECT workspace, {column} FROM {table} WHERE {column} IS NOT NULL")]
        return out
    finally:
        conn.close()


@pytest.fixture
def old_secrets(fresh, monkeypatch):
    """Each store's secrets, sealed the way they were before data keys."""
    import integrations_store
    import members_store
    import slack_store
    seal = secret_box.seal
    monkeypatch.setattr(secret_box, "seal", lambda value, workspace=None: seal(value))
    for ws in WORKSPACES:
        e = expected(ws)
        integrations_store.connect(ws, "notion", "Notion", e["notion"], "admin")
        integrations_store.connect(ws, "outline", "Outline", e["outline"], "admin")
        members_store.create_connection(ws, "oidc", "Okta", "https://idp.example", "kb",
                                        e["sso"], {}, "admin")
        slack_store.set_custom_app(ws, "1.2", e["slack_app"]["client_secret"],
                                   e["slack_app"]["signing_secret"], "admin")
        slack_store.installed(ws, f"T{ws}", "Slack", "A", "U", e["slack_bot"], "chat:write",
                              "admin")
    monkeypatch.setattr(secret_box, "seal", seal)
    assert all(secret_box.version_of(b) is None for *_, b in blobs())
    assert len(blobs()) == 5 * len(WORKSPACES)
    return blobs()


def test_reseal_moves_every_secret_under_its_workspaces_key(old_secrets):
    seen = []

    def check(done):                       # after each row: everything still opens
        seen.append(dict(done))
        for ws in WORKSPACES:
            assert stored(ws) == expected(ws)
    result = secret_box.reseal(progress=check)
    assert result == {"resealed": 10, "kept": 0, "failed": 0} and len(seen) == 10
    assert all(secret_box.version_of(b) == 1 for *_, b in blobs())
    for ws in WORKSPACES:
        assert stored(ws) == expected(ws)
    # Each under its own workspace's key: another's can't open it.
    (_, _, ws, blob), *_ = [b for b in blobs() if b[2] == "acme"]
    with pytest.raises(Exception):
        secret_box.unseal(blob, "globex")
    assert secret_box.reseal() == {"resealed": 0, "kept": 10, "failed": 0}   # idempotent
    assert secret_box.old_format_count() == 0


def test_reseal_stopped_midway_picks_up_where_it_was(old_secrets, monkeypatch):
    real, calls = secret_box.seal, []

    def flaky(value, workspace=None):
        calls.append(1)
        if len(calls) == 4:
            raise RuntimeError("the process died")
        return real(value, workspace)
    monkeypatch.setattr(secret_box, "seal", flaky)
    first = secret_box.reseal()
    assert first["failed"] == 1 and first["resealed"] == 9
    for ws in WORKSPACES:                   # a mix of old and new: all open
        assert stored(ws) == expected(ws)
    monkeypatch.setattr(secret_box, "seal", real)
    assert secret_box.reseal() == {"resealed": 1, "kept": 9, "failed": 0}


def test_a_change_made_meanwhile_isnt_overwritten(old_secrets, monkeypatch):
    import integrations_store
    real = secret_box.unseal

    def meanwhile(blob, workspace=None):       # someone reconnects while it re-seals
        value = real(blob, workspace)
        if value == {"access_token": "notion-acme"}:
            integrations_store.update("acme", "notion", secret={"access_token": "new"})
        return value
    monkeypatch.setattr(secret_box, "unseal", meanwhile)
    secret_box.reseal()
    monkeypatch.setattr(secret_box, "unseal", real)
    assert integrations_store.secret("acme", "notion") == {"access_token": "new"}


def test_the_reseal_job_is_queued_at_start_while_old_secrets_remain(old_secrets,
                                                                    jobs_paused):
    from kb import jobs
    from kb.services import keys
    job_id = keys.schedule_reseal()
    assert jobs.get(job_id)["kind"] == "secrets_reseal"
    jobs.Runner(handlers={"secrets_reseal": jobs.HANDLERS["secrets_reseal"]},
                threads=0).run_once()
    assert jobs.get(job_id)["status"] == "done"
    assert keys.schedule_reseal() is None


def test_new_secrets_are_sealed_under_the_workspace_key(fresh):
    import integrations_store
    integrations_store.connect("acme", "notion", "Notion", {"t": "x"}, "admin")
    ((_, _, _, blob),) = blobs()
    assert blob.startswith(b"kbw1:1:")
    assert integrations_store.secret("acme", "notion") == {"t": "x"}


def test_rotation_keeps_everything_readable(old_secrets):
    secret_box.reseal()
    before = {b[3] for b in blobs() if b[2] == "acme"}
    result = secret_box.rotate("acme")
    assert result["version"] == 2 and result["resealed"] == 5
    acme = [b for b in blobs() if b[2] == "acme"]
    assert all(secret_box.version_of(b[3]) == 2 for b in acme)
    assert all(secret_box.version_of(b[3]) == 1 for b in blobs() if b[2] == "globex")
    for ws in WORKSPACES:
        assert stored(ws) == expected(ws)
    # A copy sealed under the retired version still opens (the version is kept).
    assert {secret_box.unseal(b, "acme") is not None for b in before} == {True}
    conn = db.connect()
    try:
        rows = conn.execute("SELECT version, retired_at FROM workspace_keys WHERE workspace = "
                            "'acme' ORDER BY version").fetchall()
    finally:
        conn.close()
    assert [(r[0], r[1] is not None) for r in rows] == [(1, True), (2, False)]


def test_a_new_server_key_takes_over(old_secrets, monkeypatch):
    import os
    secret_box.reseal()
    old = os.environ["INTEGRATIONS_KEY"]
    new = Fernet.generate_key().decode()
    monkeypatch.setenv("INTEGRATIONS_KEY", f"{new},{old}")
    result = secret_box.rewrap()
    assert result["rewrapped"] == len(WORKSPACES)
    monkeypatch.setenv("INTEGRATIONS_KEY", new)            # the old one is dropped
    secret_box.forget()
    for ws in WORKSPACES:
        assert stored(ws) == expected(ws)


def test_shredding_makes_the_old_ciphertexts_unreadable(old_secrets):
    secret_box.reseal()
    copied = [b[3] for b in blobs() if b[2] == "acme"]       # e.g. in a backup
    assert secret_box.shred("acme") == 1
    for blob in copied:
        with pytest.raises(secret_box.Shredded):
            secret_box.unseal(blob, "acme")
    assert stored("globex") == expected("globex")


def test_hosted_mode_never_makes_a_key_file(fresh, monkeypatch):
    import os
    monkeypatch.delenv("INTEGRATIONS_KEY")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("HOSTED", "1")
    with pytest.raises(secret_box.KeyMissing):
        secret_box.require_key()
    with pytest.raises(secret_box.KeyMissing):
        secret_box.seal({"t": 1}, "acme")
    assert not os.path.exists(secret_box.KEY_PATH)
    monkeypatch.setenv("INTEGRATIONS_KEY", Fernet.generate_key().decode())
    secret_box.require_key()
    assert secret_box.unseal(secret_box.seal({"t": 1}, "acme"), "acme") == {"t": 1}
    monkeypatch.delenv("HOSTED")                    # self-hosted: a key file is fine
    monkeypatch.delenv("INTEGRATIONS_KEY")
    secret_box.require_key()


def test_hosted_mode_refuses_to_start_without_a_key(fresh, monkeypatch):
    from fastapi.testclient import TestClient
    from kb.app import create_app
    monkeypatch.delenv("INTEGRATIONS_KEY")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("HOSTED", "1")
    with pytest.raises(secret_box.KeyMissing):
        with TestClient(create_app()):
            pass


def test_a_kms_can_wrap_the_data_keys(fresh):
    class FakeKms:
        id = "fake-kms"
        key = Fernet(Fernet.generate_key())

        def wrap(self, dek):
            return b"kms:" + self.key.encrypt(dek)

        def unwrap(self, wrapped):
            return self.key.decrypt(wrapped[4:])

        def rewrap(self, wrapped):
            return wrapped
    secret_box.use_kek(FakeKms())
    try:
        blob = secret_box.seal({"t": 1}, "acme")
        conn = db.connect()
        try:
            wrapped = conn.execute("SELECT wrapped_dek FROM workspace_keys").fetchone()[0]
        finally:
            conn.close()
        assert bytes(wrapped).startswith(b"kms:")
        assert secret_box.unseal(blob, "acme") == {"t": 1}
    finally:
        secret_box.use_kek(None)


def test_readiness_checks_the_key_against_a_data_key(fresh, monkeypatch):
    import health
    import integrations_store
    integrations_store.connect("acme", "notion", "Notion", {"t": "x"}, "admin")
    health.check_integrations_key(db.DB_PATH)
    monkeypatch.setenv("INTEGRATIONS_KEY", Fernet.generate_key().decode())
    with pytest.raises(health.NotReady):
        health.check_integrations_key(db.DB_PATH)


@pytest.mark.sqlite_only("scripts/sqlite_tool.py, the restore tool, reads the SQLite file")
def test_restore_checks_the_key_against_a_data_key(fresh, tmp_path):
    """scripts/restore.sh's check-key: the key file opens the wrapped data keys."""
    import importlib.util
    import os
    import integrations_store
    spec = importlib.util.spec_from_file_location(
        "sqlite_tool", os.path.join(os.path.dirname(__file__), "..", "scripts", "sqlite_tool.py"))
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    integrations_store.connect("acme", "notion", "Notion", {"t": "x"}, "admin")
    right, wrong = tmp_path / "right.key", tmp_path / "wrong.key"
    right.write_text(os.environ["INTEGRATIONS_KEY"])
    wrong.write_text(Fernet.generate_key().decode())
    assert tool.check_key(db.DB_PATH, str(right)) == 0
    assert tool.check_key(db.DB_PATH, str(wrong)) == 1
