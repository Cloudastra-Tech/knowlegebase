"""
Model providers apart from any one of them (models.py's REGISTRY,
model_providers_store.py): a provider for chat and one for embeddings, the
server's .env, a workspace's own API keys, failing closed. Bedrock's own paths
are tests/test_bedrock.py (its fakes are reused here); nothing reaches a network.

    the registry       what each provider offers; unknown names refused
    the server         MODEL_PROVIDER as before, CHAT_PROVIDER / EMBED_PROVIDER
                       override one side each; an embeddings provider must have them
    a workspace        chat on one provider, embeddings on another; its own API
                       key, sealed, never shown, never the server's instead
    the index          switching the embeddings asks first and rebuilds; switching
                       only the chat provider doesn't
"""

import json
from types import SimpleNamespace

import pytest

import model_providers_store
import models
from tests.test_bedrock import (KEY_ID, REGION, SECRET, client, fake_aws, indexed,  # noqa: F401
                                login, new_workspace, no_openai)

OPENAI_KEY = "sk-proj-workspace0000000000000000own"


class Recorder:
    """Stands in for ChatOpenAI / OpenAIEmbeddings: records how it was made."""
    made = []

    def __init__(self, **kwargs):
        Recorder.made.append(kwargs)
        self.kwargs = kwargs

    def embed_query(self, text):
        return [0.5] * 8

    def embed_documents(self, texts):
        return [[0.5] * 8 for _ in texts]


class FakeOpenAI:
    """openai.OpenAI as Test connection uses it (models._openai_client): models.list
    only, recording the keys it was made with. error: what listing raises."""

    def __init__(self, listed=("gpt-4o-mini", "gpt-5", "gpt-5-mini", "text-embedding-3-small")):
        self.keys, self.listed, self.error, self.lists = [], list(listed), None, 0
        self.models = SimpleNamespace(list=self._list)

    def __call__(self, key=None):
        self.keys.append(key)
        return self

    def _list(self):
        self.lists += 1
        if self.error is not None:
            raise self.error
        return [SimpleNamespace(id=m) for m in self.listed]


@pytest.fixture
def fake_openai(monkeypatch):
    made = FakeOpenAI()
    monkeypatch.setattr(models, "_openai_client", made)
    return made


@pytest.fixture
def recorder(monkeypatch):
    Recorder.made = []
    monkeypatch.setattr(models, "_openai_chat", lambda: Recorder)
    monkeypatch.setattr(models, "_openai_embeddings", lambda: Recorder)
    return Recorder.made


@pytest.fixture
def chat_only(monkeypatch):
    """A provider with chat and no embeddings (as Anthropic is) in the registry."""
    p = models.Provider(name="chatonly", label="Chat Only",
                        make_chat=lambda on, model, **kw: SimpleNamespace(model=model),
                        defaults=lambda: {"chat_model": "big", "fast_model": "small"},
                        api_key_env="CHATONLY_API_KEY")
    monkeypatch.setitem(models.REGISTRY, "chatonly", p)
    return p


@pytest.fixture
def server_env(monkeypatch):
    for name in ("MODEL_PROVIDER", "CHAT_PROVIDER", "EMBED_PROVIDER"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


# ---- The registry ------------------------------------------------------------------------------
def test_the_registry_says_what_each_provider_offers(chat_only):
    openai, bedrock = models.REGISTRY["openai"], models.REGISTRY["bedrock"]
    assert openai.chat and openai.embeddings and openai.tool_calls and openai.vision
    assert openai.prompt_caching == "automatic" and openai.api_key_env == "OPENAI_API_KEY"
    assert bedrock.chat and bedrock.embeddings and bedrock.in_house
    assert bedrock.prompt_caching == "marked" and bedrock.api_key_env is None
    assert "chatonly" in models.chat_providers()
    assert "chatonly" not in models.embedding_providers()
    with pytest.raises(models.ProviderError, match="Unknown"):
        models.provider_info("nope")
    with pytest.raises(models.ProviderError, match="no embeddings"):
        models.provider_info("chatonly", "embeddings")


def test_embed_names_round_trip_even_with_colons_in_the_model_id():
    assert models.embed_name("openai", "text-embedding-3-small", None) == "text-embedding-3-small"
    name = models.embed_name("bedrock", "amazon.titan-embed-text-v2:0", 256)
    assert name == "bedrock:amazon.titan-embed-text-v2:0:256"
    assert models.parse_embed_name(name) == ("bedrock", "amazon.titan-embed-text-v2:0", 256)
    assert models.parse_embed_name("text-embedding-3-small") == (
        "openai", "text-embedding-3-small", None)


# ---- The server's providers -----------------------------------------------------------------------
def test_model_provider_alone_sets_both_as_before(server_env):
    assert models.server_providers() == ("openai", "openai")
    assert models.server_spec() == models.Spec()
    server_env.setenv("MODEL_PROVIDER", "bedrock")
    server_env.setenv("AWS_REGION", "us-east-1")
    assert models.server_providers() == ("bedrock", "bedrock")
    spec = models.server_spec()
    assert (spec.provider, spec.embed_provider, spec.region, spec.auth) == (
        "bedrock", None, "us-east-1", "default")
    assert (spec.chat_model, spec.embed_model, spec.embed_dims) == (
        models.BEDROCK_CHAT, models.BEDROCK_EMBED, models.BEDROCK_EMBED_DIMENSIONS)


def test_chat_and_embed_provider_override_one_side_each(server_env, fake_aws):
    server_env.setenv("CHAT_PROVIDER", "bedrock")
    server_env.setenv("EMBED_PROVIDER", "openai")
    server_env.setenv("AWS_REGION", "us-east-1")
    on = models.current()
    assert (on.provider, on.embed_provider) == ("bedrock", "openai")
    assert on.answer_name == models.BEDROCK_CHAT
    assert on.embed_name == "text-embedding-3-small"          # the index needn't change
    assert isinstance(on.answer(), models.BedrockChat)
    server_env.delenv("EMBED_PROVIDER")
    server_env.setenv("MODEL_PROVIDER", "openai")              # MODEL_PROVIDER fills in
    assert models.server_providers() == ("bedrock", "openai")


def test_an_embeddings_provider_must_have_embeddings(server_env, chat_only):
    server_env.setenv("EMBED_PROVIDER", "chatonly")
    with pytest.raises(models.ProviderError, match="Chat Only has no embeddings"):
        models.current()
    server_env.setenv("EMBED_PROVIDER", "openia")
    with pytest.raises(models.ProviderError, match="isn't a known provider"):
        models.current()


def test_a_chat_provider_without_embeddings_never_gets_openais_by_default(server_env, chat_only):
    server_env.setenv("CHAT_PROVIDER", "chatonly")              # local, on this server
    assert models.server_providers() == ("chatonly", "local")
    server_env.setenv("MODEL_PROVIDER", "chatonly")
    assert models.server_providers() == ("chatonly", "local")
    server_env.setenv("EMBED_PROVIDER", "openai")              # said out loud: fine
    assert models.server_providers() == ("chatonly", "openai")


# ---- A workspace: chat and embeddings apart, its own key ---------------------------------------------
def test_chat_on_bedrock_embeddings_on_openai_with_the_workspaces_key(fake_aws, recorder):
    w = new_workspace()
    model_providers_store.save(w, {"chat_provider": "bedrock", "embed_provider": "openai",
                                   "region": REGION, "auth": "keys", "access_key_id": KEY_ID,
                                   "secret_access_key": SECRET,
                                   "api_keys": {"openai": OPENAI_KEY}}, "admin")
    on = models.for_workspace(w)
    assert (on.provider, on.embed_provider) == ("bedrock", "openai")
    assert isinstance(on.answer(), models.BedrockChat)
    on.embeddings()
    assert recorder == [{"model": "text-embedding-3-small", "api_key": OPENAI_KEY}]
    # Bedrock for chat only still keeps candidates from an API reranker.
    assert on.in_house


def test_a_workspace_key_is_sealed_and_never_shown(client, recorder):
    import audit_store
    import db
    import secret_box
    w = new_workspace()
    h = login(client, w)
    r = client.put("/api/admin/assistant/models", headers=h,
                   json={"chat_provider": "openai", "embed_provider": "default",
                         "api_keys": {"openai": OPENAI_KEY}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["chat_provider"] == "openai" and body["embed_provider"] == "default"
    assert body["api_keys_saved"] == {"openai": True}
    assert OPENAI_KEY not in json.dumps(body)
    assert OPENAI_KEY not in json.dumps(client.get("/api/admin/assistant/models",
                                                   headers=h).json())
    events = audit_store.list_events(w, limit=20)[0]
    assert OPENAI_KEY not in json.dumps(events)
    (event,) = [e for e in events if e["action"] == "model_provider.update"]
    assert event["details"]["api_keys"] == {"openai": "replaced"}
    conn = db.connect()
    try:
        blob = conn.execute("SELECT api_keys FROM model_providers WHERE workspace = ?",
                            (w,)).fetchone()[0]
    finally:
        conn.close()
    assert OPENAI_KEY.encode() not in bytes(blob) and bytes(blob).startswith(secret_box.PREFIX)
    models.for_workspace(w).answer()
    assert recorder[-1]["api_key"] == OPENAI_KEY
    # The catalogue the settings screen builds its choices from.
    names = {p["name"]: p for p in body["providers"]}
    assert names["openai"]["api_key"] and names["bedrock"]["in_house"]


def test_a_provider_that_takes_a_key_needs_one_and_keys_go_with_it():
    w = new_workspace()
    with pytest.raises(model_providers_store.Invalid, match="OpenAI API key"):
        model_providers_store.save(w, {"chat_provider": "openai"}, "admin")
    with pytest.raises(model_providers_store.Invalid, match="API key"):
        model_providers_store.save(w, {"chat_provider": "openai",
                                       "api_keys": {"openai": "short"}}, "admin")
    with pytest.raises(model_providers_store.Invalid, match="doesn't take an API key"):
        model_providers_store.save(w, {"api_keys": {"bedrock": OPENAI_KEY}}, "admin")
    model_providers_store.save(w, {"chat_provider": "openai",
                                   "api_keys": {"openai": OPENAI_KEY}}, "admin")
    # Back to the server's: a key for a provider no longer used isn't kept.
    model_providers_store.save(w, {"chat_provider": "default"}, "admin")
    assert model_providers_store.get(w)["api_keys"] is None
    assert models.for_workspace(w).spec.api_keys is None


def test_an_embeddings_provider_without_embeddings_is_refused(chat_only):
    w = new_workspace()
    with pytest.raises(model_providers_store.Invalid, match="Chat Only has no embeddings API"):
        model_providers_store.save(w, {"embed_provider": "chatonly"}, "admin")
    model_providers_store.save(w, {"chat_provider": "chatonly",
                                   "api_keys": {"chatonly": OPENAI_KEY}}, "admin")
    on = models.for_workspace(w)
    assert (on.provider, on.embed_provider) == ("chatonly", "openai")   # the server's
    assert on.answer().model == "big"


def test_a_missing_workspace_key_fails_closed_never_the_servers(recorder, monkeypatch):
    import db
    monkeypatch.setenv("OPENAI_API_KEY", "sk-server-key-should-not-be-used")
    w = new_workspace()
    model_providers_store.save(w, {"chat_provider": "openai",
                                   "api_keys": {"openai": OPENAI_KEY}}, "admin")
    conn = db.connect()
    with conn:
        conn.execute("UPDATE model_providers SET api_keys = NULL WHERE workspace = ?", (w,))
    conn.close()
    with pytest.raises(models.ProviderError, match="none is saved"):
        models.for_workspace(w).answer()
    assert recorder == []


def test_an_unreadable_workspace_key_fails_closed(recorder):
    import db
    w = new_workspace()
    model_providers_store.save(w, {"chat_provider": "openai",
                                   "api_keys": {"openai": OPENAI_KEY}}, "admin")
    conn = db.connect()
    with conn:
        conn.execute("UPDATE model_providers SET api_keys = ? WHERE workspace = ?",
                     (b"kbw1:1:not-a-token", w))
    conn.close()
    on = models.for_workspace(w)
    with pytest.raises(models.ProviderError, match="can't be read"):
        on.answer()
    with pytest.raises(models.ProviderError):
        on.embeddings()
    assert recorder == []


def test_a_row_from_before_the_split_reads_as_before(fake_aws):
    import db
    from tests.test_bedrock import on_bedrock
    w = new_workspace()
    on_bedrock(w)
    conn = db.connect()
    with conn:
        conn.execute("UPDATE model_providers SET chat_provider = NULL, embed_provider = NULL "
                     "WHERE workspace = ?", (w,))
    conn.close()
    on = models.for_workspace(w)
    assert (on.provider, on.embed_provider) == ("bedrock", "bedrock")
    assert on.embed_name == "bedrock:amazon.titan-embed-text-v2:0:256"
    assert model_providers_store.view(w)["chat_provider"] == "bedrock"


def test_switching_a_sides_provider_clears_its_models(fake_aws):
    from tests.test_bedrock import on_bedrock
    w = new_workspace()
    on_bedrock(w, chat_model="amazon.nova-micro-v1:0")
    model_providers_store.save(w, {"chat_provider": "openai",
                                   "api_keys": {"openai": OPENAI_KEY}}, "admin")
    row = model_providers_store.get(w)
    assert row["chat_model"] is None and row["embed_dims"] == 256    # embeddings untouched
    assert row["provider"] == "bedrock"         # older code keeps the text in AWS
    assert models.for_workspace(w).answer_name == models.answer_model()


def test_test_connection_only_lists_models_and_hides_the_key(client, recorder, fake_openai):
    """Free: the provider lists its models (OpenAI's models.list), once per
    provider. No completion and no embedding is made (nothing is billed)."""
    w = new_workspace()
    h = login(client, w)
    r = client.post("/api/admin/assistant/models/test", headers=h,
                    json={"chat_provider": "openai", "embed_provider": "openai",
                          "api_keys": {"openai": OPENAI_KEY},
                          "chat_models": [{"provider": "openai", "model": "gpt-5"},
                                          {"provider": "openai", "model": "gpt-9-custom"}]})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "providers": [
        {"provider": "openai", "label": "OpenAI", "ok": True, "models": 4,
         "missing": ["gpt-9-custom"]}]}
    assert recorder == []                         # no ChatOpenAI, no OpenAIEmbeddings
    assert fake_openai.keys == [OPENAI_KEY] and fake_openai.lists == 1
    assert models.for_workspace(w).spec.api_keys is None              # nothing saved

    fake_openai.error = RuntimeError(f"Incorrect API key provided: {OPENAI_KEY}")
    r = client.post("/api/admin/assistant/models/test", headers=h,
                    json={"chat_provider": "openai", "api_keys": {"openai": OPENAI_KEY}})
    (row,) = r.json()["providers"]
    assert r.json()["ok"] is False and row["ok"] is False
    assert "Incorrect API key" in row["error"]
    assert OPENAI_KEY not in r.text and recorder == []


# ---- The index: only an embeddings change rebuilds it ----------------------------------------------
def test_only_an_embeddings_change_rebuilds_the_index(client, indexed, fake_aws):
    import ingest
    from kb.services import indexing
    w = indexed.ws
    h = login(client, w)
    old = indexing.current(w)[0]
    aws = {"region": REGION, "auth": "keys", "access_key_id": KEY_ID,
           "secret_access_key": SECRET}
    # Chat on Bedrock, embeddings still the server's OpenAI: nothing to rebuild.
    r = client.put("/api/admin/assistant/models", headers=h,
                   json={"chat_provider": "bedrock", **aws})
    assert r.status_code == 200, r.text
    assert r.json()["reindex"] is False
    assert indexing.fingerprint(w) == ingest.FINGERPRINT
    assert indexing.current(w)[0] == old
    # Embeddings to Bedrock: confirmed first, then rebuilt (1024 numbers, not 1536).
    r = client.put("/api/admin/assistant/models", headers=h, json={"embed_provider": "bedrock"})
    assert r.status_code == 409 and "rebuilt" in r.json()["detail"]
    r = client.put("/api/admin/assistant/models", headers=h,
                   json={"embed_provider": "bedrock", "confirm_reindex": True})
    assert r.status_code == 200 and r.json()["reindex"] is True
    assert indexed.wait()["status"] == "idle"
    assert indexing.current(w)[0] != old
    assert indexing.embeddings_of(w) == "bedrock:amazon.titan-embed-text-v2:0:1024"
    assert r.json()["effective"]["embed_provider"] == "bedrock"
