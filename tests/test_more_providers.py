"""
Google Gemini, Azure OpenAI and OpenAI-compatible servers as model providers
(models.py's "gemini", "azure" and "openai_compatible" entries). All three are
reached with the OpenAI SDK / langchain-openai: the classes are Recorders here and
the SDK clients fakes, so nothing reaches a network or a paid API.

    the wiring      Gemini on Google's OpenAI-compatible base URL; Azure's endpoint,
                    api-version and deployments; a compatible server's base URL,
                    its key optional, never the server's OPENAI_API_KEY
    the guard       an address an admin types is refused when private or loopback
                    (on save, and on every request), unless MODEL_ALLOW_PRIVATE_HOSTS
                    lists it
    the store       keys sealed and never shown, the addresses shown back, each
                    provider's credentials its own, fail closed
    test, picker    Test connection lists (Gemini, compatible) or makes one short
                    call (Azure); the chat's picker lists their models
    cost            Insights prices Gemini; a compatible server's cost is unknown
"""

import json
from types import SimpleNamespace

import openai
import pytest

import model_providers_store as store
import models
import url_guard
from kb.services import insights
from tests.test_bedrock import client, login, new_workspace  # noqa: F401
from tests.test_model_providers import (OPENAI_KEY, Recorder, fake_openai,  # noqa: F401
                                        recorder, server_env)

GEMINI_KEY = "AIzaSyWorkspace000000000000000000own"
AZURE_KEY = "azkey0000000000000000000000000own"
COMPAT_KEY = "gsk_workspace00000000000000000own"
AZURE_URL = "https://contoso.openai.azure.com"
COMPAT_URL = "https://api.groq.com/openai/v1"
PUBLIC_IP = "93.184.215.14"


@pytest.fixture(autouse=True)
def dns(monkeypatch):
    """Every host resolves to a public address unless a test says otherwise:
    hosts[name] = ip. No real DNS lookup."""
    hosts = {}

    def resolve(host, port):
        return [(2, 1, 6, "", (hosts.get(host, PUBLIC_IP), port))]

    monkeypatch.setattr(url_guard, "_resolve", resolve)
    monkeypatch.delenv("MODEL_ALLOW_PRIVATE_HOSTS", raising=False)
    return hosts


@pytest.fixture
def classes(monkeypatch, recorder):
    """AzureChatOpenAI, AzureOpenAIEmbeddings and CompatibleChat are Recorders too."""
    monkeypatch.setattr(models, "_azure_chat", lambda: Recorder)
    monkeypatch.setattr(models, "_azure_embeddings", lambda: Recorder)
    monkeypatch.setattr(models, "_compatible_chat", lambda: Recorder)
    return recorder


class FakeSDK:
    """openai.OpenAI / AzureOpenAI as Test connection uses them: models.list and one
    chat completion, recording how each client was made and what it was asked."""

    def __init__(self, listed=()):
        self.made, self.listed, self.completions, self.error = [], list(listed), [], None
        self.models = SimpleNamespace(list=self._list)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def __call__(self, *args):
        self.made.append(args)
        return self

    def _list(self):
        if self.error is not None:
            raise self.error
        return [SimpleNamespace(id=m) for m in self.listed]

    def _create(self, **kwargs):
        if self.error is not None:
            raise self.error
        self.completions.append(kwargs)
        return SimpleNamespace(choices=[])


def api_error(cls, status, message):
    import httpx
    request = httpx.Request("POST", "https://example.invalid/v1/chat/completions")
    response = httpx.Response(status, request=request, json={"error": {"message": message}})
    return cls(message, response=response, body={"message": message})


def gemini_workspace(w=None, **extra):
    w = w or new_workspace()
    store.save(w, {"chat_provider": "gemini", "embed_provider": "default",
                   "api_keys": {"gemini": GEMINI_KEY}, **extra}, "admin")
    return w


def azure_workspace(w=None, **extra):
    w = w or new_workspace()
    store.save(w, {"default_model": {"provider": "azure", "model": "gpt-4o-prod"},
                   "embed_provider": "default", "api_keys": {"azure": AZURE_KEY},
                   "endpoints": {"azure": {"base_url": AZURE_URL}}, **extra}, "admin")
    return w


def compat_workspace(w=None, key=COMPAT_KEY, **extra):
    w = w or new_workspace()
    store.save(w, {"default_model": {"provider": models.COMPAT,
                                     "model": "llama-3.3-70b-versatile"},
                   "embed_provider": "default",
                   **({"api_keys": {models.COMPAT: key}} if key else {}),
                   "endpoints": {models.COMPAT: {"base_url": COMPAT_URL}}, **extra}, "admin")
    return w


# ---- The registry ------------------------------------------------------------------------------
def test_the_registry_says_what_each_new_provider_offers():
    gemini, azure, compat = (models.REGISTRY[n] for n in ("gemini", "azure", models.COMPAT))
    for p in (gemini, azure, compat):
        assert p.chat and p.embeddings and p.tool_calls and p.api_key_env
        assert not p.in_house and p.image_format == "openai"
    assert not gemini.base_url and gemini.api_key_env == "GEMINI_API_KEY"
    assert azure.base_url and azure.api_version and azure.typed_models
    assert compat.base_url and compat.key_optional and not compat.api_version
    assert compat.describe()["key_optional"] and azure.describe()["api_version"]
    assert {m.id for m in models.CATALOGUE["gemini"]} >= {models.GEMINI_CHAT, models.GEMINI_FAST}


# ---- Gemini ----------------------------------------------------------------------------------------
def test_gemini_is_called_on_googles_openai_compatible_endpoint(recorder):
    w = gemini_workspace(embed_provider="gemini")
    on = models.for_workspace(w)
    assert (on.provider, on.embed_provider) == ("gemini", "gemini")
    assert (on.answer_name, on.fast_name) == (models.GEMINI_CHAT, models.GEMINI_FAST)
    on.answer(temperature=0, max_tokens=4000)
    made = recorder[-1]
    assert made["base_url"] == models.GEMINI_BASE_URL == (
        "https://generativelanguage.googleapis.com/v1beta/openai/")
    assert made["api_key"] == GEMINI_KEY and made["model"] == models.GEMINI_CHAT
    assert made["use_responses_api"] is False and made["max_tokens"] == 4000
    assert "temperature" not in made                  # Gemini 3 runs at its own
    assert "http_client" not in made                  # Google's address: not an admin's
    assert "reasoning_effort" not in made
    # The small jobs: thinking at low effort, and room for it in a small reply.
    on.fast(max_tokens=30)
    assert recorder[-1]["reasoning_effort"] == "low"
    assert recorder[-1]["max_tokens"] == models.GEMINI_MIN_TOKENS
    on.embeddings()
    assert recorder[-1] == {"model": "gemini-embedding-001", "api_key": GEMINI_KEY,
                            "base_url": models.GEMINI_BASE_URL,
                            "check_embedding_ctx_length": False, "chunk_size": 100,
                            "model_kwargs": {"encoding_format": "float"}}
    assert on.embed_name == "gemini:gemini-embedding-001:3072"
    assert models.parse_embed_name(on.embed_name) == ("gemini", "gemini-embedding-001", 3072)


def test_the_servers_gemini_uses_its_own_key_and_none_fails_closed(server_env, recorder):
    server_env.setenv("CHAT_PROVIDER", "gemini")
    server_env.setenv("EMBED_PROVIDER", "openai")
    server_env.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(models.ProviderError, match="no API key"):
        models.current().answer()
    assert recorder == []
    server_env.setenv("GEMINI_API_KEY", "AIzaServer0000000000000000000000")
    server_env.setenv("GEMINI_CHAT_MODEL", "gemini-4-argon")
    models.current().answer()
    assert recorder[-1]["api_key"] == "AIzaServer0000000000000000000000"
    assert recorder[-1]["model"] == "gemini-4-argon"


# ---- Azure OpenAI ----------------------------------------------------------------------------------
def test_azure_is_called_by_deployment_with_its_endpoint_and_api_version(classes):
    w = azure_workspace(embed_provider="azure", embed_model="embed-small-prod")
    on = models.for_workspace(w)
    assert (on.provider, on.answer_name) == ("azure", "gpt-4o-prod")
    # No fast deployment named: the answer's does the small jobs.
    assert on.fast_name == "gpt-4o-prod"
    on.answer(temperature=0, max_tokens=500)
    made = classes[-1]
    assert (made["azure_endpoint"], made["azure_deployment"], made["model"]) == (
        AZURE_URL, "gpt-4o-prod", "gpt-4o-prod")
    assert made["api_version"] == models.AZURE_API_VERSION == "2024-10-21"
    assert made["api_key"] == AZURE_KEY and made["temperature"] == 0
    assert made["use_responses_api"] is False
    # The endpoint is an admin's: every request goes through url_guard.
    assert (made["http_client"], made["http_async_client"]) == url_guard.model_http()
    on.embeddings()
    made = classes[-1]
    assert (made["azure_deployment"], made["api_version"], made["api_key"]) == (
        "embed-small-prod", "2024-10-21", AZURE_KEY)
    assert made["check_embedding_ctx_length"] is False
    assert on.embed_name == "azure:embed-small-prod:None"
    # An api-version typed in, and a fast deployment of its own.
    store.save(w, {"endpoints": {"azure": {"api_version": "2025-04-01-preview"}},
                   "fast_model": "gpt-4o-mini-prod"}, "admin")
    on = models.for_workspace(w)
    on.fast()
    assert classes[-1]["api_version"] == "2025-04-01-preview"
    assert classes[-1]["azure_deployment"] == "gpt-4o-mini-prod"
    assert classes[-1]["azure_endpoint"] == AZURE_URL         # kept from before


def test_azure_needs_an_endpoint_and_a_deployment():
    w = new_workspace()
    with pytest.raises(store.Invalid, match="Azure OpenAI endpoint"):
        store.save(w, {"default_model": {"provider": "azure", "model": "gpt-4o-prod"},
                       "api_keys": {"azure": AZURE_KEY}}, "admin")
    with pytest.raises(store.Invalid, match="deployment that answers"):
        store.save(w, {"chat_provider": "azure", "api_keys": {"azure": AZURE_KEY},
                       "endpoints": {"azure": {"base_url": AZURE_URL}}}, "admin")
    with pytest.raises(store.Invalid, match="API version"):
        azure_workspace(w, endpoints={"azure": {"base_url": AZURE_URL,
                                                "api_version": "latest"}})
    with pytest.raises(store.Invalid, match="deployment that embeds"):
        azure_workspace(w, embed_provider="azure")


# ---- OpenAI-compatible servers ------------------------------------------------------------------------
def test_a_compatible_server_gets_its_base_url_and_key_never_openais(classes, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-server-openai-must-not-leave")
    monkeypatch.setenv("OPENAI_COMPAT_API_KEY", "server-compat-key-must-not-be-used")
    w = compat_workspace(embed_provider=models.COMPAT, embed_model="nomic-embed-text")
    on = models.for_workspace(w)
    on.answer(temperature=0, max_tokens=300)
    made = classes[-1]
    assert (made["base_url"], made["api_key"], made["model"]) == (
        COMPAT_URL, COMPAT_KEY, "llama-3.3-70b-versatile")
    assert made["http_client"] is url_guard.model_http()[0]
    on.embeddings()
    made = classes[-1]
    assert (made["base_url"], made["api_key"], made["model"]) == (
        COMPAT_URL, COMPAT_KEY, "nomic-embed-text")
    assert made["check_embedding_ctx_length"] is False and "http_client" in made
    # A server that takes no key: none is sent, never the server's (OpenAI's or its own).
    w = compat_workspace(key=None)
    assert store.view(w)["api_keys_saved"] == {}
    models.for_workspace(w).answer()
    assert classes[-1]["api_key"] == models.NO_KEY
    assert not [m for m in classes if m.get("api_key") in (
        "sk-server-openai-must-not-leave", "server-compat-key-must-not-be-used")]


def test_a_compatible_server_is_sent_max_tokens():
    chat = models._compatible_chat()(model="llama-3.3-70b-versatile", api_key="x",
                                     base_url=COMPAT_URL, max_tokens=50,
                                     use_responses_api=False)
    payload = chat._get_request_payload("Hi")
    assert payload["max_tokens"] == 50 and "max_completion_tokens" not in payload
    assert payload["model"] == "llama-3.3-70b-versatile"


def test_a_compatible_embedding_model_must_be_named():
    w = new_workspace()
    with pytest.raises(store.Invalid, match="embedding model"):
        compat_workspace(w, embed_provider=models.COMPAT)


# ---- The guard: no private addresses ----------------------------------------------------------------
@pytest.mark.parametrize("url, ip, why", [
    ("https://metadata.internal", "169.254.169.254", "private network address"),
    ("https://localhost:11434/v1", "127.0.0.1", "private network address"),
    ("https://vllm.corp.example/v1", "10.0.0.5", "private network address"),
    ("https://ipv6.example/v1", "::1", "private network address"),
    ("http://api.groq.com/openai/v1", PUBLIC_IP, "https://"),
    ("https://user:pw@api.groq.com/v1", PUBLIC_IP, "valid web address"),
    ("https://api.groq.com/v1?key=abc", PUBLIC_IP, "query string"),
])
def test_a_private_or_unsafe_base_url_is_refused(client, dns, url, ip, why):
    from urllib.parse import urlsplit
    dns[urlsplit(url).hostname] = ip
    w = new_workspace()
    h = login(client, w)
    r = client.put("/api/admin/assistant/models", headers=h, json={
        "default_model": {"provider": models.COMPAT, "model": "llama3.1"},
        "endpoints": {models.COMPAT: {"base_url": url}}})
    assert r.status_code == 400 and why in r.json()["detail"], r.text
    assert models.for_workspace(w).provider == "openai"            # nothing saved
    # Test connection is refused the same way, before anything is called.
    r = client.post("/api/admin/assistant/models/test", headers=h, json={
        "default_model": {"provider": models.COMPAT, "model": "llama3.1"},
        "endpoints": {models.COMPAT: {"base_url": url}}})
    assert r.status_code == 400 and why in r.json()["detail"]
    # Azure's endpoint too.
    with pytest.raises(store.Invalid, match="Azure OpenAI endpoint"):
        azure_workspace(new_workspace(), endpoints={"azure": {"base_url": url}})


def test_a_listed_private_host_is_allowed_over_http(monkeypatch, dns, classes):
    dns["localhost"] = "127.0.0.1"
    monkeypatch.setenv("MODEL_ALLOW_PRIVATE_HOSTS", "localhost:11434")
    w = new_workspace()
    store.save(w, {"default_model": {"provider": models.COMPAT, "model": "llama3.1:8b"},
                   "endpoints": {models.COMPAT: {"base_url": "http://localhost:11434/v1/"}}},
               "admin")
    assert store.view(w)["endpoints"] == {
        models.COMPAT: {"base_url": "http://localhost:11434/v1", "api_version": None}}
    models.for_workspace(w).answer()
    assert classes[-1]["base_url"] == "http://localhost:11434/v1"
    # Another port on the same host isn't listed.
    with pytest.raises(store.Invalid, match="https://"):
        store.save(w, {"endpoints": {models.COMPAT: {"base_url": "http://localhost:8000/v1"}}},
                   "admin")
    # Sign-in's and connectors' lists don't open it.
    monkeypatch.delenv("MODEL_ALLOW_PRIVATE_HOSTS")
    monkeypatch.setenv("SSO_ALLOW_PRIVATE_HOSTS", "localhost:11434")
    monkeypatch.setenv("CONNECTOR_ALLOW_PRIVATE_HOSTS", "localhost:11434")
    with pytest.raises(store.Invalid):
        store.save(w, {"endpoints": {models.COMPAT: {"base_url": "http://localhost:11434/v1"}}},
                   "admin")


def test_every_request_is_checked_again(dns):
    """A host saved while public whose DNS now points inside is refused per request,
    and the reason reaches the admin (not just "Connection error")."""
    sync, _ = url_guard.model_http()
    request = sync.build_request("GET", "https://api.groq.com/openai/v1/models")
    (hook,) = sync.event_hooks["request"]
    hook(request)                                     # public: fine
    dns["api.groq.com"] = "10.1.2.3"
    with pytest.raises(url_guard.UnsafeURL, match="private network address"):
        hook(request)
    try:
        try:
            hook(request)
        except url_guard.UnsafeURL as e:
            raise openai.APIConnectionError(request=request) from e
    except openai.APIConnectionError as wrapped:
        assert "private network address" in models.describe_error(wrapped)


# ---- The store: sealed keys, addresses shown, fail closed ---------------------------------------------
def test_keys_are_never_returned_and_addresses_are(client, classes):
    import audit_store
    w = new_workspace()
    h = login(client, w)
    r = client.put("/api/admin/assistant/models", headers=h, json={
        "default_model": {"provider": "azure", "model": "gpt-4o-prod"},
        "chat_models": [{"provider": "azure", "model": "gpt-4o-prod"},
                        {"provider": "gemini", "model": "gemini-4-argon"},
                        {"provider": models.COMPAT, "model": "llama-3.3-70b-versatile"}],
        "api_keys": {"azure": AZURE_KEY, "gemini": GEMINI_KEY, models.COMPAT: COMPAT_KEY},
        "endpoints": {"azure": {"base_url": AZURE_URL + "/", "api_version": "2024-10-21"},
                      models.COMPAT: {"base_url": COMPAT_URL}}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["api_keys_saved"] == {"azure": True, "gemini": True, models.COMPAT: True}
    assert body["endpoints"] == {
        "azure": {"base_url": AZURE_URL, "api_version": "2024-10-21"},
        models.COMPAT: {"base_url": COMPAT_URL, "api_version": None}}
    texts = [r.text, client.get("/api/admin/assistant/models", headers=h).text,
             json.dumps(audit_store.list_events(w, limit=20)[0])]
    for text in texts:
        for key in (AZURE_KEY, GEMINI_KEY, COMPAT_KEY):
            assert key not in text
    assert {p["name"]: p["typed_models"] for p in body["providers"]}["azure"] is True
    assert body["provider_defaults"]["azure"]["api_version"] == "2024-10-21"
    # Removing a provider's key takes its address and models with it.
    store.save(w, {"api_keys": {models.COMPAT: None}}, "admin")
    view = store.view(w)
    assert models.COMPAT not in view["endpoints"]
    assert [m["provider"] for m in view["chat_models"]] == ["azure", "gemini"]


def test_a_missing_address_fails_closed_never_the_servers(monkeypatch, classes):
    import db
    import secret_box
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://server.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "server-azure-key-0000000000000")
    w = azure_workspace()
    conn = db.connect()
    with conn:   # the sealed JSON with the key but no endpoint (an older row, a bad write)
        conn.execute("UPDATE model_providers SET api_keys = ? WHERE workspace = ?",
                     (secret_box.seal({"azure": AZURE_KEY}, w), w))
    conn.close()
    with pytest.raises(models.ProviderError, match="no endpoint"):
        models.for_workspace(w).answer()
    assert classes == []


def test_moving_only_the_chat_provider_keeps_the_index(recorder):
    w = new_workspace()
    before = models.for_workspace(w).embed_name
    gemini_workspace(w)
    assert models.for_workspace(w).embed_name == before
    compat_workspace(w)
    assert models.for_workspace(w).embed_name == before


# ---- Test connection ----------------------------------------------------------------------------------
def test_test_connection_lists_gemini_and_a_compatible_server(client, monkeypatch, recorder,
                                                              fake_openai):
    sdk = FakeSDK(["models/gemini-3.8-flash", "models/gemini-3.5-flash-lite",
                   "llama-3.3-70b-versatile"])
    monkeypatch.setattr(models, "_compatible_client", sdk)
    w = new_workspace()
    h = login(client, w)
    body = {"default_model": {"provider": "gemini", "model": "gemini-3.8-flash"},
            "chat_models": [{"provider": "gemini", "model": "gemini-3.8-flash"},
                            {"provider": models.COMPAT, "model": "llama-3.3-70b-versatile"}],
            "api_keys": {"gemini": GEMINI_KEY, models.COMPAT: COMPAT_KEY},
            "endpoints": {models.COMPAT: {"base_url": COMPAT_URL}}}
    r = client.post("/api/admin/assistant/models/test", headers=h, json=body)
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "providers": [
        {"provider": "gemini", "label": "Google Gemini", "ok": True, "models": 3,
         "missing": []},
        {"provider": models.COMPAT, "label": "OpenAI-compatible", "ok": True, "models": 3,
         "missing": []},
        {"provider": "openai", "label": "OpenAI", "ok": True, "models": 4,
         "missing": []}]}                           # search: the server's, listed too
    assert sdk.made == [(GEMINI_KEY, models.GEMINI_BASE_URL, False),
                        (COMPAT_KEY, COMPAT_URL, True)]
    assert recorder == []                               # no message, no embedding
    # A refused key: its class and message, the key blanked out.
    sdk.error = api_error(openai.AuthenticationError, 401, f"API key not valid: {GEMINI_KEY}")
    r = client.post("/api/admin/assistant/models/test", headers=h, json=body)
    rows = {p["provider"]: p for p in r.json()["providers"]}
    assert r.json()["ok"] is False and "AuthenticationError" in rows["gemini"]["error"]
    assert GEMINI_KEY not in r.text


def test_test_connection_on_azure_makes_one_short_reply(client, monkeypatch, classes):
    sdk = FakeSDK()
    monkeypatch.setattr(models, "_azure_client", sdk)
    w = new_workspace()
    h = login(client, w)
    body = {"default_model": {"provider": "azure", "model": "gpt-4o-prod"},
            "embed_provider": "azure", "embed_model": "embed-small-prod",
            "api_keys": {"azure": AZURE_KEY},
            "endpoints": {"azure": {"base_url": AZURE_URL}}}
    r = client.post("/api/admin/assistant/models/test", headers=h, json=body)
    assert r.status_code == 200, r.text
    (row,) = r.json()["providers"]
    assert row["ok"] and row["checked"] == ["gpt-4o-prod", "embed-small-prod"]
    assert sdk.made == [(AZURE_KEY, AZURE_URL, "2024-10-21", True)]
    (call,) = sdk.completions
    assert call["model"] == "gpt-4o-prod" and call["max_completion_tokens"] == 16
    assert classes[-1]["azure_deployment"] == "embed-small-prod"     # one short embedding
    sdk.error = api_error(openai.NotFoundError, 404,
                          f"The API deployment for this resource does not exist {AZURE_KEY}")
    r = client.post("/api/admin/assistant/models/test", headers=h, json=body)
    (row,) = r.json()["providers"]
    assert not row["ok"] and row["error"].startswith("NotFoundError")
    assert AZURE_KEY not in r.text


# ---- The chat's picker --------------------------------------------------------------------------------
def test_the_picker_lists_the_new_providers_models(client):
    w = new_workspace()
    store.save(w, {"default_model": {"provider": "gemini", "model": "gemini-3.8-flash"},
                   "chat_models": [{"provider": "gemini", "model": "gemini-3.8-flash"},
                                   {"provider": "azure", "model": "gpt-4o-prod"},
                                   {"provider": models.COMPAT, "model": "llama-3.3-70b"}],
                   "api_keys": {"gemini": GEMINI_KEY, "azure": AZURE_KEY},
                   "endpoints": {"azure": {"base_url": AZURE_URL},
                                 models.COMPAT: {"base_url": COMPAT_URL}}}, "admin")
    picker = client.get("/api/models", headers=login(client, w, "staff")).json()
    assert [(m["provider"], m["provider_label"], m["model"], m["label"])
            for m in picker["models"]] == [
        ("gemini", "Google Gemini", "gemini-3.8-flash", "Gemini 3.8 Flash"),
        ("azure", "Azure OpenAI", "gpt-4o-prod", "gpt-4o-prod"),
        (models.COMPAT, "OpenAI-compatible", "llama-3.3-70b", "llama-3.3-70b")]
    assert picker["default"] == {"provider": "gemini", "model": "gemini-3.8-flash"}
    for key in (GEMINI_KEY, AZURE_KEY, AZURE_URL, COMPAT_URL):
        assert key not in json.dumps(picker)
    # The compatible server's models can't read images (nothing says they can).
    assert [m["vision"] for m in picker["models"]] == [True, True, False]


# ---- Cost ---------------------------------------------------------------------------------------------
def test_insights_prices_gemini_and_not_a_compatible_server():
    assert insights.cost_of("gemini-3.8-flash", 1_000_000, 1_000_000) == pytest.approx(4.50)
    assert insights.cost_of("gemini-3.8-flash", 1_000_000, 0, "gemini") == pytest.approx(0.75)
    assert insights.cost_of("gemini-9-unknown", 1000, 1000, "gemini") is None
    assert insights.cost_of("gpt-4o-mini", 1000, 1000, models.COMPAT) is None
    assert insights.cost_of("llama-3.3-70b", 1000, 1000, models.COMPAT) is None
    # Azure: the model its reply names, at OpenAI's list price; a dated snapshot too.
    assert insights.cost_of("gpt-4o-2024-11-20", 1_000_000, 0, "azure") == pytest.approx(2.50)
    assert insights.cost_of("my-deployment", 1000, 1000, "azure") is None
    assert insights.cost_of("gpt-4o-mini", 1_000_000, 0) == pytest.approx(0.15)   # as before
