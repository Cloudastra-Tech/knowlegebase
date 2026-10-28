"""
Several chat models per workspace, and each chat's pick (model_providers_store's
chat_models, kb/services/model_choice.py, migration 0026). Every provider is a
fake (tests/test_bedrock.py, tests/anthropic_fakes.py): nothing reaches a network.

    the store       credentials for several providers at once, the enabled models
                    and the default; each enabled provider needs its credentials;
                    a removed key takes its models; keys never shown (last 4 only)
    a chat          answers with the model picked, keeps it, and is refused a
                    model that isn't enabled (any more); the guards stay on the
                    workspace's fast model whatever is picked
    isolation       a workspace can only pick its own enabled models, with its own
                    keys: another's credentials are never reachable
    the cache, log  the answer cache's key and the question log name the model
    migration 0026  single-provider rows become one enabled model; downgrade works
"""

import json
import pathlib
import sqlite3

import pytest

import db
import model_providers_store as store
import models
from kb.services import model_choice
from tests.anthropic_fakes import FakeAnthropic
from tests.test_bedrock import (KEY_ID, REGION, SECRET, client, fake_aws, indexed,  # noqa: F401
                                login, new_workspace)
from tests.test_model_providers import OPENAI_KEY, recorder  # noqa: F401

ANTHROPIC_KEY = "sk-ant-api03-workspace000000000000000000own"
AWS = {"region": REGION, "auth": "keys", "access_key_id": KEY_ID, "secret_access_key": SECRET}
OPUS = {"provider": "anthropic", "model": "claude-opus-5-5"}
NOVA = {"provider": "bedrock", "model": models.BEDROCK_CHAT}


@pytest.fixture
def fake(monkeypatch):
    import anthropic
    made = FakeAnthropic()
    monkeypatch.setattr(anthropic, "Client", made)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return made


def two_providers(w=None, **extra):
    """Bedrock answers by default (its own AWS), Claude Opus may be picked (its own key)."""
    w = w or new_workspace()
    store.save(w, {"chat_provider": "bedrock", "embed_provider": "default", **AWS,
                   "api_keys": {"anthropic": ANTHROPIC_KEY},
                   "chat_models": [NOVA, OPUS], **extra}, "admin")
    return w


# ---- The store ---------------------------------------------------------------------------------
def test_several_providers_with_one_default_and_keys_never_shown(client, fake_aws):
    w = new_workspace()
    h = login(client, w)
    r = client.put("/api/admin/assistant/models", headers=h, json={
        "chat_provider": "bedrock", **AWS, "api_keys": {"anthropic": ANTHROPIC_KEY,
                                                        "openai": OPENAI_KEY},
        "chat_models": [NOVA, OPUS, {"provider": "openai", "model": "gpt-5"}],
        "default_model": OPUS})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["default_model"] == OPUS and body["chat_provider"] == "anthropic"
    assert [(m["provider"], m["model"], m["default"]) for m in body["chat_models"]] == [
        ("anthropic", "claude-opus-5-5", True), ("bedrock", models.BEDROCK_CHAT, False),
        ("openai", "gpt-5", False)]
    assert body["chat_models"][0]["tier"] == "best"
    assert body["chat_models"][0]["label"] == "Claude Opus 5.5"
    assert body["api_keys_saved"] == {"anthropic": True, "openai": True}
    assert body["api_key_hints"] == {"anthropic": "0own", "openai": "0own"}
    assert ANTHROPIC_KEY not in r.text and OPENAI_KEY not in r.text and SECRET not in r.text
    assert {m["id"] for m in body["catalogue"]["anthropic"]} >= {"claude-sonnet-5-5"}
    # The fast model (the guards, titles) stays the default provider's.
    on = models.for_workspace(w)
    assert (on.provider, on.answer_name, on.fast_name) == (
        "anthropic", "claude-opus-5-5", "claude-haiku-5-5")
    # Members see the picker's list, never a key; they can't change it.
    staff = login(client, w, "staff")
    picker = client.get("/api/models", headers=staff).json()
    assert [m["model"] for m in picker["models"]] == ["claude-opus-5-5", models.BEDROCK_CHAT,
                                                      "gpt-5"]
    assert picker["default"] == OPUS and picker["last"] is None
    assert picker["models"][1]["provider_label"] == "AWS Bedrock"
    assert client.put("/api/admin/assistant/models", headers=staff,
                      json={"chat_models": [NOVA]}).status_code == 403


def test_each_enabled_provider_needs_its_own_credentials(fake_aws):
    w = new_workspace()
    with pytest.raises(store.Invalid, match="Anthropic API key"):
        store.save(w, {"chat_provider": "bedrock", **AWS, "chat_models": [NOVA, OPUS]}, "admin")
    with pytest.raises(store.Invalid, match="AWS region"):
        store.save(w, {"chat_provider": "anthropic", "api_keys": {"anthropic": ANTHROPIC_KEY},
                       "chat_models": [OPUS, NOVA]}, "admin")
    with pytest.raises(store.Invalid, match="model id"):
        store.save(w, {"chat_models": [{"provider": "openai", "model": "no spaces allowed"}]},
                   "admin")
    with pytest.raises(store.Invalid, match="chat provider"):
        store.save(w, {"chat_models": [{"provider": "local", "model": "x1"}]}, "admin")
    with pytest.raises(store.Invalid, match="at most"):
        store.save(w, {"chat_models": [{"provider": "openai", "model": f"m-{i}"}
                                       for i in range(store.MAX_MODELS + 1)]}, "admin")
    # The server's chat has its one model: no choice to offer.
    with pytest.raises(store.Invalid, match="provider of your own"):
        store.save(w, {"chat_models": [NOVA], **AWS}, "admin")


def test_a_removed_key_takes_its_models_and_the_server_default_has_none(fake_aws):
    w = two_providers()
    assert store.enabled(store.get(w)) == [("bedrock", models.BEDROCK_CHAT),
                                           ("anthropic", "claude-opus-5-5")]
    store.save(w, {"api_keys": {"anthropic": None}}, "admin")
    assert store.enabled(store.get(w)) == [("bedrock", models.BEDROCK_CHAT)]
    assert store.view(w)["api_keys_saved"] == {}
    # Back on the server's chat: no enabled models, no keys kept for them.
    two_providers(w)
    store.save(w, {"chat_provider": "default"}, "admin")
    assert store.get(w)["chat_models"] is None and store.get(w)["api_keys"] is None
    assert model_choice.available(w)["models"][0]["provider"] == "openai"   # the server's


def test_a_row_without_a_list_offers_its_one_model(fake_aws):
    from tests.test_bedrock import on_bedrock
    w = new_workspace()
    on_bedrock(w, chat_model="amazon.nova-micro-v1:0")
    conn = db.connect()
    with conn:
        conn.execute("UPDATE model_providers SET chat_models = NULL WHERE workspace = ?", (w,))
    conn.close()
    assert store.enabled(store.get(w)) == [("bedrock", "amazon.nova-micro-v1:0")]


def test_a_migrated_default_follows_the_servers_model_after_a_save(monkeypatch):
    """A row 0026 gave {model: null} keeps meaning "the provider's default (.env)":
    a later save doesn't freeze it, so changing ANSWER_MODEL can't leave two models."""
    w = new_workspace()
    store.save(w, {"chat_provider": "openai", "api_keys": {"openai": OPENAI_KEY}}, "admin")
    conn = db.connect()
    with conn:
        conn.execute("UPDATE model_providers SET chat_models = ? WHERE workspace = ?",
                     (json.dumps([{"provider": "openai", "model": None}]), w))
    conn.close()
    store.save(w, {"api_keys": {"openai": OPENAI_KEY}}, "admin")
    monkeypatch.setenv("ANSWER_MODEL", "gpt-5")
    assert store.enabled(store.get(w)) == [("openai", "gpt-5")]


# ---- A chat's pick -------------------------------------------------------------------------------
def _stream(client, h, **body):
    r = client.post("/api/chat/stream", headers=h, json={"question": "How long do refunds take?",
                                                         **body})
    return r, [json.loads(c[len("data: "):]) for c in r.text.split("\n\n") if c.startswith("data")]


def test_a_chat_answers_with_its_pick_and_the_guards_stay_on_the_fast_model(
        client, indexed, fake, fake_aws, recorder):
    w = two_providers(indexed.ws)
    h = login(client, w, "staff")
    cid = client.post("/api/conversations", headers=h, json={}).json()["id"]
    r, events = _stream(client, h, conversation_id=cid, model=OPUS)
    assert r.status_code == 200, r.text
    assert events[-1]["type"] == "done", events[-1]
    assert "30 days" in events[-1]["answer"]
    # Claude Opus wrote the answer, with the workspace's own key; nothing else ran on it.
    assert fake.calls and {c["model"] for c in fake.calls} == {"claude-opus-5-5"}
    assert set(fake.keys) == {ANTHROPIC_KEY}
    # The guards, the title: the workspace's FAST model (Bedrock's), never the pick.
    used = {c[1] for c in fake_aws.runtime.calls if c[0].startswith("converse")}
    assert used == {models.BEDROCK_FAST}
    assert not [m for m in recorder if str(m.get("model", "")).startswith("gpt")]
    # The chat keeps it, and the asker's next new chat starts on it.
    assert client.get(f"/api/conversations/{cid}", headers=h).json()["model"] == OPUS
    assert client.get("/api/models", headers=h).json()["last"] == OPUS
    # The question log names the provider and model that answered.
    conn = db.connect()
    try:
        row = conn.execute("SELECT provider, model FROM question_log WHERE workspace = ? "
                           "ORDER BY id DESC LIMIT 1", (w,)).fetchone()
    finally:
        conn.close()
    assert tuple(row) == ("anthropic", "claude-opus-5-5")
    # Without a pick, the chat goes on with its own.
    fake.calls.clear()
    r, events = _stream(client, h, conversation_id=cid)
    assert events[-1]["type"] == "done" and {c["model"] for c in fake.calls} == {
        "claude-opus-5-5"}


def test_a_model_that_isnt_enabled_is_refused(client, indexed, fake, fake_aws):
    w = two_providers(indexed.ws)
    h = login(client, w, "staff")
    cid = client.post("/api/conversations", headers=h, json={}).json()["id"]
    assert _stream(client, h, conversation_id=cid, model=OPUS)[0].status_code == 200
    r, _ = _stream(client, h, conversation_id=cid,
                   model={"provider": "openai", "model": "gpt-5"})
    assert r.status_code == 400 and "isn't available" in r.json()["detail"]
    # The admin turns Opus off: the chat that used it is refused it, with the reason.
    admin = login(client, w)
    assert client.put("/api/admin/assistant/models", headers=admin,
                      json={"chat_models": [NOVA]}).status_code == 200
    fake.calls.clear()
    for body in ({"model": OPUS}, {}):
        r, _ = _stream(client, h, conversation_id=cid, **body)
        assert r.status_code == 400, r.text
        assert "isn't available" in r.json()["detail"]
    assert fake.calls == []                     # nothing reached Claude
    r, events = _stream(client, h, conversation_id=cid, model=NOVA)
    assert r.status_code == 200 and events[-1]["type"] == "done"
    assert client.get("/api/models", headers=h).json()["last"] == NOVA


def test_one_workspace_can_never_use_anothers_models_or_keys(client, fake, fake_aws):
    a = two_providers()
    b = new_workspace()
    store.save(b, {"chat_provider": "openai", "api_keys": {"openai": OPENAI_KEY}}, "admin")
    staff_b = login(client, b, "staff")
    picker = client.get("/api/models", headers=staff_b).json()
    assert [(m["provider"], m["model"]) for m in picker["models"]] == [
        ("openai", models.answer_model())]
    assert ANTHROPIC_KEY not in json.dumps(picker)
    r, _ = _stream(client, staff_b, model=OPUS)
    assert r.status_code == 400 and "isn't available" in r.json()["detail"]
    with pytest.raises(model_choice.Refused):
        model_choice.resolve(b, OPUS)
    assert fake.calls == []
    # Each workspace's models hold its own keys only.
    keys_b = dict(models.for_workspace(b).spec.api_keys)
    assert keys_b == {"openai": OPENAI_KEY}
    assert dict(models.for_workspace(a).spec.api_keys) == {"anthropic": ANTHROPIC_KEY}
    # A's chat can't be read or answered in by B's member.
    staff_a = login(client, a, "staff")
    cid = client.post("/api/conversations", headers=staff_a, json={}).json()["id"]
    r, _ = _stream(client, staff_b, conversation_id=cid, model={
        "provider": "openai", "model": models.answer_model()})
    assert r.status_code == 404


def test_the_answer_cache_and_release_key_on_the_answer_model(fake_aws):
    w = two_providers()
    on = models.for_workspace(w)
    picked = on.with_answer("anthropic", "claude-opus-5-5")
    assert on.with_answer("bedrock", models.BEDROCK_CHAT) is on       # the default: no change
    assert on.release() == f"bedrock|{models.BEDROCK_CHAT}|{models.BEDROCK_FAST}"   # as before
    assert picked.release() != on.release() and "claude-opus-5-5" in picked.release()
    assert (picked.provider, picked.answer_provider) == ("bedrock", "anthropic")
    assert picked.fast_name == models.BEDROCK_FAST
    from kb.services import answer_cache
    with models.scope(on):
        default_key = answer_cache._release()
    with models.scope(picked):
        assert answer_cache._release() != default_key


# ---- Migration 0026 --------------------------------------------------------------------------------
@pytest.mark.sqlite_only("the SQLite file's own migrations (Postgres: test_postgres_migrations)")
def test_0026_turns_single_provider_rows_into_one_enabled_model_and_downgrades(
        tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config
    path = str(tmp_path / "app.db")
    monkeypatch.setattr(db, "DB_PATH", path)
    config = Config(str(pathlib.Path(db.__file__).with_name("alembic.ini")))
    command.upgrade(config, "0025")
    insert = ("INSERT INTO model_providers (workspace, provider, chat_provider, chat_model, "
              "external_id, updated_at) VALUES (?, ?, ?, ?, 'kb-x', 1)")
    with sqlite3.connect(path) as conn:
        conn.execute(insert, ("bedrock-ws", "bedrock", None, "amazon.nova-micro-v1:0"))
        conn.execute(insert, ("openai-ws", "default", "openai", None))
        conn.execute(insert, ("server-ws", "default", "default", None))
        conn.execute("INSERT INTO conversations (id, workspace, owner, role_used, created_at, "
                     "updated_at) VALUES ('c1', 'w', 'u', 'public', 1, 1)")
        conn.execute("INSERT INTO messages (id, conversation_id, author, content, created_at) "
                     "VALUES ('m1', 'c1', 'user', 'q', 1)")
    conn.close()
    command.upgrade(config, "0026")
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        rows = {r["workspace"]: dict(r) for r in conn.execute("SELECT * FROM model_providers")}
        conv = dict(conn.execute("SELECT * FROM conversations").fetchone())
    conn.close()
    assert json.loads(rows["bedrock-ws"]["chat_models"]) == [
        {"provider": "bedrock", "model": "amazon.nova-micro-v1:0"}]
    assert json.loads(rows["openai-ws"]["chat_models"]) == [{"provider": "openai", "model": None}]
    assert rows["server-ws"]["chat_models"] is None
    assert conv["model_provider"] is None and conv["model_id"] is None
    # Read as the store reads them: the one model, the default.
    assert store.enabled(rows["bedrock-ws"]) == [("bedrock", "amazon.nova-micro-v1:0")]
    assert store.enabled(rows["openai-ws"]) == [("openai", models.answer_model())]
    assert store.enabled(rows["server-ws"]) == []
    command.downgrade(config, "0025")
    with sqlite3.connect(path) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(model_providers)")}
        conv_cols = {r[1] for r in conn.execute("PRAGMA table_info(conversations)")}
        kept = conn.execute("SELECT chat_provider, chat_model FROM model_providers WHERE "
                            "workspace = 'bedrock-ws'").fetchone()
        messages = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    conn.close()
    assert "chat_models" not in cols and not {"model_provider", "model_id"} & conv_cols
    assert kept == (None, "amazon.nova-micro-v1:0") and messages == 1   # nothing else lost
    command.upgrade(config, "head")
