"""
AWS Bedrock as a model provider (models.py, model_providers_store.py; PROGRESS.md
"AWS Bedrock as a model provider"). Never real AWS: STS, Converse and InvokeModel
are botocore Stubbers (they check every request against AWS's own API model) or
FakeRuntime, a client answering in the shapes Bedrock returns (recorded from its
API reference), for the tests that make many calls.

    the factory        OpenAI unchanged and the default; Bedrock's chat and
                       embeddings; MODEL_PROVIDER refused when it's unknown
    per workspace      one workspace on Bedrock, another on the server's OpenAI
    the role           AssumeRole with the workspace's ExternalId, once, its
                       credentials kept until they're about to expire
    Test connection    one Converse call and one embedding; AWS's reason on failure
    the embeddings     switching them asks first, then rebuilds the index (another
                       FINGERPRINT), questions keep the old one meanwhile
    fail closed        a Bedrock workspace's question, index and digest, with every
                       OpenAI client made to raise: nothing reaches OpenAI
    the rest           streaming, tool calls, the guards, cost, the question log
"""

import datetime
import hashlib
import io
import json
import os
import time
import uuid
from types import SimpleNamespace

import boto3
import pytest
from botocore.response import StreamingBody
from botocore.stub import Stubber
from fastapi.testclient import TestClient

import auth
import members_store
import model_providers_store
import models

PASSWORD = "correct-horse"
REGION = "eu-west-1"
ROLE = "arn:aws:iam::123456789012:role/kb-bedrock"
KEY_ID, SECRET = "AKIAIOSFODNN7EXAMPLE", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"


# ---- Fakes ---------------------------------------------------------------------------------
def _vector(text, dims):
    """A deterministic unit vector: texts sharing words point the same way."""
    v = [0.0] * dims
    for word in str(text).lower().split():
        v[int(hashlib.md5(word.encode()).hexdigest(), 16) % dims] += 1.0
    norm = sum(x * x for x in v) ** 0.5 or 1.0
    return [x / norm for x in v]


def _text_of(request):
    parts = [b.get("text", "") for b in request.get("system") or []]
    for m in request.get("messages") or []:
        parts += [b.get("text", "") for b in m["content"] if isinstance(b, dict)]
    return "\n".join(parts)


class FakeRuntime:
    """bedrock-runtime as langchain-aws calls it: converse, converse_stream and
    invoke_model, answering in Bedrock's response shapes. `calls`: (operation,
    modelId, request)."""

    def __init__(self, answer="Refunds are allowed within 30 days [S1].", tool_call=None):
        self.calls, self.answer, self.tool_call = [], answer, tool_call
        self.meta = SimpleNamespace(region_name=REGION,
                                    events=SimpleNamespace(register=lambda *a, **k: None))

    def reply(self, request):
        text = _text_of(request)
        if "ALLOW" in text and "BLOCK" in text:          # the guards' classifiers
            return "ALLOW"
        if models.TEST_PROMPT in text:
            return "OK"
        return self.answer

    def converse(self, **request):
        self.calls.append(("converse", request["modelId"], request))
        if self.tool_call and "toolConfig" in request and not any(
                "toolResult" in b for m in request["messages"] for b in m["content"]):
            content = [{"toolUse": {"toolUseId": "tool-1", **self.tool_call}}]
            stop = "tool_use"
        else:
            content, stop = [{"text": self.reply(request)}], "end_turn"
        return {"output": {"message": {"role": "assistant", "content": content}},
                "stopReason": stop, "metrics": {"latencyMs": 3},
                "usage": {"inputTokens": 40, "outputTokens": 8, "totalTokens": 48,
                          "cacheReadInputTokens": 30, "cacheWriteInputTokens": 0}}

    def converse_stream(self, **request):
        self.calls.append(("converse_stream", request["modelId"], request))
        text = self.reply(request)
        words = [w + " " for w in text.split(" ")]
        events = [{"messageStart": {"role": "assistant"}}]
        events += [{"contentBlockDelta": {"delta": {"text": w}, "contentBlockIndex": 0}}
                   for w in words]
        events += [{"contentBlockStop": {"contentBlockIndex": 0}},
                   {"messageStop": {"stopReason": "end_turn"}},
                   {"metadata": {"usage": {"inputTokens": 40, "outputTokens": len(words),
                                           "totalTokens": 40 + len(words)},
                                 "metrics": {"latencyMs": 5}}}]
        return {"stream": iter(events)}

    def invoke_model(self, **request):
        self.calls.append(("invoke_model", request["modelId"], request))
        body = json.loads(request["body"])
        data = json.dumps({"embedding": _vector(body["inputText"], body.get("dimensions") or 1024),
                           "inputTextTokenCount": len(body["inputText"].split())}).encode()
        return {"body": StreamingBody(io.BytesIO(data), len(data)),
                "contentType": "application/json"}

    def of(self, operation):
        return [c for c in self.calls if c[0] == operation]


@pytest.fixture
def fake_aws(monkeypatch):
    """Every boto3 client models.py makes is a fake (or a test's Stubber)."""
    runtime = FakeRuntime()
    made = {"stubs": {}}

    def new_client(session, service, region, config=None):
        made.setdefault(service, []).append(region)
        if service in made["stubs"]:
            return made["stubs"][service]
        if service == "bedrock-runtime":
            return runtime
        return boto3.client(service, region_name=region, aws_access_key_id="x",
                            aws_secret_access_key="y")
    monkeypatch.setattr(models, "_new_client", new_client)
    monkeypatch.setattr(models, "_base_session", lambda region: boto3.Session(
        aws_access_key_id="server", aws_secret_access_key="server-secret", region_name=region))
    models.forget()
    yield SimpleNamespace(runtime=runtime, made=made)
    models.forget()


@pytest.fixture
def no_openai(monkeypatch):
    """Every OpenAI client raises (and is counted) the moment one is made."""
    import langchain_openai
    import openai
    tried = []

    def boom(name):
        def init(self, *args, **kwargs):
            tried.append(name)
            raise AssertionError(f"{name} was made for a Bedrock workspace")
        return init
    for cls in (langchain_openai.ChatOpenAI, langchain_openai.OpenAIEmbeddings, openai.OpenAI,
                openai.AsyncOpenAI):
        monkeypatch.setattr(cls, "__init__", boom(cls.__name__))
    return tried


def new_workspace():
    w = f"br-{uuid.uuid4().hex[:8]}"
    for name, role in (("admin", "admin"), ("staff", "public")):
        members_store.set_password(f"{name}@{w}.example", name.title(),
                                   auth.hash_password(PASSWORD), w, role)
    return w


@pytest.fixture(scope="module")
def client():
    import api
    return TestClient(api.app)


def login(client, w, name="admin"):
    r = client.post("/api/auth/login", json={"username": f"{name}@{w}.example",
                                             "password": PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}",
            "X-Role": "hr" if name == "admin" else "public"}


def on_bedrock(w, **extra):
    """Put the workspace on Bedrock with access keys (no STS needed)."""
    return model_providers_store.save(w, {"provider": "bedrock", "region": REGION,
                                          "auth": "keys", "access_key_id": KEY_ID,
                                          "secret_access_key": SECRET, "embed_dims": 256,
                                          **extra}, "admin")[0]


# ---- The factory -----------------------------------------------------------------------------
def test_openai_stays_the_default_and_is_made_as_before(monkeypatch):
    made = []
    monkeypatch.delenv("MODEL_PROVIDER", raising=False)
    monkeypatch.setenv("ANSWER_MODEL", "gpt-answer")
    on = models.for_workspace(new_workspace())
    assert on.provider == "openai" and on.embed_name == "text-embedding-3-small"
    on.answer(openai=lambda **kw: made.append(kw), temperature=0, cache_key="kb:ws:public")
    on.fast(openai=lambda **kw: made.append(kw), temperature=0, max_tokens=30)
    on.embeddings(openai=lambda **kw: made.append(kw))
    assert made == [{"model": "gpt-answer", "temperature": 0,
                     "model_kwargs": {"prompt_cache_key": "kb:ws:public"}},
                    {"model": "gpt-4o-mini", "temperature": 0, "max_tokens": 30},
                    {"model": "text-embedding-3-small"}]


def test_the_server_can_run_on_bedrock(monkeypatch, fake_aws):
    monkeypatch.setenv("MODEL_PROVIDER", "bedrock")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("BEDROCK_CHAT_MODEL", "us.amazon.nova-pro-v1:0")
    on = models.for_workspace(new_workspace())
    assert (on.provider, on.answer_name, on.fast_name) == (
        "bedrock", "us.amazon.nova-pro-v1:0", models.BEDROCK_FAST)
    assert on.embed_name == "bedrock:amazon.titan-embed-text-v2:0:1024"
    chat = on.answer(temperature=0)
    assert isinstance(chat, models.BedrockChat) and chat.model_id == "us.amazon.nova-pro-v1:0"
    assert chat.prompt_cache                      # Nova caches prompts
    assert isinstance(on.embeddings(), models.BedrockEmbed)


def test_an_unknown_provider_is_refused_not_read_as_openai(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "bedrok")
    with pytest.raises(models.ProviderError):
        models.current().answer()


def test_a_workspace_on_bedrock_leaves_the_others_on_the_server(fake_aws):
    a, b = new_workspace(), new_workspace()
    on_bedrock(a, chat_model="anthropic.claude-haiku-4-5-20251001-v1:0")
    assert models.for_workspace(a).provider == "bedrock"
    assert models.for_workspace(a).answer_name == "anthropic.claude-haiku-4-5-20251001-v1:0"
    assert models.for_workspace(b).provider == "openai"
    with models.scope(a):
        assert models.current().spec.workspace == a and models.current().bedrock
    assert models.current().provider == "openai"


def test_bedrock_needs_what_it_needs_before_it_is_saved():
    w = new_workspace()
    for change, said in (({"provider": "bedrock"}, "region"),
                         ({"provider": "bedrock", "region": REGION}, "role"),
                         ({"provider": "bedrock", "region": "us-east-1; rm"}, "region"),
                         ({"provider": "bedrock", "region": REGION, "auth": "keys"}, "access key"),
                         ({"role_arn": "arn:aws:iam::1:role/x"}, "role ARN"),
                         ({"chat_model": "nova pro"}, "model id"),
                         ({"embed_model": "amazon.titan-embed-text-v2:0", "embed_dims": 384},
                          "1024, 512, 256")):
        with pytest.raises(model_providers_store.Invalid, match=said):
            model_providers_store.save(w, change, "admin")
    assert models.for_workspace(w).provider == "openai"


# ---- The role ---------------------------------------------------------------------------------
def _assume_response(expires_in):
    return {"Credentials": {
        "AccessKeyId": "ASIAEXAMPLEEXAMPLE01", "SecretAccessKey": "s" * 40,
        "SessionToken": "token",
        "Expiration": datetime.datetime.now(datetime.timezone.utc)
        + datetime.timedelta(seconds=expires_in)},
        "AssumedRoleUser": {"AssumedRoleId": "AROAEXAMPLE:knowledge", "Arn": ROLE}}


def test_the_role_is_assumed_once_with_the_external_id_and_refreshed_before_it_expires(
        fake_aws):
    w = new_workspace()
    view = model_providers_store.save(w, {"provider": "bedrock", "region": REGION,
                                          "role_arn": ROLE}, "admin")[0]
    assert view["external_id"].startswith("kb-") and len(view["external_id"]) > 20
    sts = boto3.client("sts", region_name=REGION, aws_access_key_id="x",
                       aws_secret_access_key="y")
    stub = Stubber(sts)
    expected = {"RoleArn": ROLE, "ExternalId": view["external_id"],
                "RoleSessionName": f"knowledge-{w}", "DurationSeconds": 3600}
    stub.add_response("assume_role", _assume_response(3600), expected)
    stub.add_response("assume_role", _assume_response(3600), expected)
    fake_aws.made["stubs"]["sts"] = sts
    with stub:
        spec = models.for_workspace(w).spec
        runtime = models.runtime_client(spec)
        assert models.runtime_client(spec) is runtime          # cached
        models.client(spec, "bedrock")                         # same session: no new role
        credentials = models._cached_session(spec).get_credentials()
        assert credentials.get_frozen_credentials().access_key == "ASIAEXAMPLEEXAMPLE01"
        assert len(stub._queue) == 1                           # assumed once
        # Shortly before they expire, they're refreshed: assumed again, once.
        credentials._expiry_time = (datetime.datetime.now(datetime.timezone.utc)
                                    + datetime.timedelta(seconds=60))
        credentials.get_frozen_credentials()
        stub.assert_no_pending_responses()


def test_a_role_that_cant_be_assumed_fails_closed_with_awss_reason(fake_aws):
    w = new_workspace()
    model_providers_store.save(w, {"provider": "bedrock", "region": REGION, "role_arn": ROLE},
                               "admin")
    sts = boto3.client("sts", region_name=REGION, aws_access_key_id="x",
                       aws_secret_access_key="y")
    stub = Stubber(sts)
    stub.add_client_error("assume_role", "AccessDenied", "not authorized to perform "
                          "sts:AssumeRole", http_status_code=403)
    fake_aws.made["stubs"]["sts"] = sts
    with stub, pytest.raises(models.ProviderError, match="AccessDenied"):
        models.for_workspace(w).answer()


# ---- Test connection, and the settings screen's API ----------------------------------------------
def test_test_connection_makes_one_converse_call_and_one_embedding(client, fake_aws):
    w = new_workspace()
    h = login(client, w)
    runtime = boto3.client("bedrock-runtime", region_name=REGION, aws_access_key_id="x",
                           aws_secret_access_key="y")
    stub = Stubber(runtime)
    stub.add_response("converse", {
        "output": {"message": {"role": "assistant", "content": [{"text": "OK"}]}},
        "stopReason": "end_turn", "metrics": {"latencyMs": 120},
        "usage": {"inputTokens": 14, "outputTokens": 2, "totalTokens": 16}},
        {"modelId": models.BEDROCK_CHAT, "system": [], "inferenceConfig": {
            "maxTokens": 10, "temperature": 0},
         "messages": [{"role": "user", "content": [{"text": models.TEST_PROMPT}]}]})
    data = json.dumps({"embedding": [0.1] * 1024, "inputTextTokenCount": 2}).encode()
    stub.add_response("invoke_model", {"body": StreamingBody(io.BytesIO(data), len(data)),
                                       "contentType": "application/json"},
                      {"modelId": models.BEDROCK_EMBED, "accept": "application/json",
                       "contentType": "application/json",
                       "body": json.dumps({"inputText": "connection test", "dimensions": 1024})})
    fake_aws.made["stubs"]["bedrock-runtime"] = runtime
    with stub:
        r = client.post("/api/admin/assistant/models/test", headers=h,
                        json={"region": REGION, "auth": "keys", "access_key_id": KEY_ID,
                              "secret_access_key": SECRET})
        stub.assert_no_pending_responses()
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "chat_model": models.BEDROCK_CHAT, "reply": "OK",
                        "tokens": 16, "embed_model": models.BEDROCK_EMBED, "dimensions": 1024}
    # Testing saves nothing but when it last worked: the workspace is still on OpenAI.
    assert models.for_workspace(w).provider == "openai"
    assert client.get("/api/admin/assistant/models", headers=h).json()["tested_at"]


def test_a_failed_test_says_why_and_is_audited(client, fake_aws):
    import audit_store
    w = new_workspace()
    h = login(client, w)
    runtime = boto3.client("bedrock-runtime", region_name=REGION, aws_access_key_id="x",
                           aws_secret_access_key="y")
    stub = Stubber(runtime)
    stub.add_client_error("converse", "AccessDeniedException",
                          "You don't have access to the model with the specified model ID.")
    fake_aws.made["stubs"]["bedrock-runtime"] = runtime
    with stub:
        r = client.post("/api/admin/assistant/models/test", headers=h,
                        json={"region": REGION, "auth": "keys", "access_key_id": KEY_ID,
                              "secret_access_key": SECRET})
    assert r.status_code == 200
    assert r.json()["ok"] is False and "AccessDeniedException" in r.json()["error"]
    (event,) = [e for e in audit_store.list_events(w, limit=20)[0]
                if e["action"] == "model_provider.test"]
    assert event["details"]["ok"] is False
    assert SECRET not in json.dumps(event) and KEY_ID not in json.dumps(event)


def test_the_screen_shows_the_external_id_and_policies_never_the_secret(client, fake_aws):
    w = new_workspace()
    h = login(client, w)
    on_bedrock(w, chat_model="us.amazon.nova-pro-v1:0")
    body = client.get("/api/admin/assistant/models", headers=h).json()
    assert body["provider"] == "bedrock" and body["keys_saved"]
    assert body["access_key_hint"] == KEY_ID[-4:]
    assert SECRET not in json.dumps(body) and KEY_ID not in json.dumps(body)
    trust = body["trust_policy"]["Statement"][0]
    assert trust["Action"] == "sts:AssumeRole"
    assert trust["Condition"] == {"StringEquals": {"sts:ExternalId": body["external_id"]}}
    chat, embed = body["permissions_policy"]["Statement"]
    assert chat["Action"] == ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
    # An inference profile needs the profile AND its foundation model in every region.
    assert f"arn:aws:bedrock:{REGION}:*:inference-profile/us.amazon.nova-pro-v1:0" \
        in chat["Resource"]
    assert "arn:aws:bedrock:*::foundation-model/amazon.nova-pro-v1:0" in chat["Resource"]
    assert embed["Resource"] == [
        f"arn:aws:bedrock:{REGION}::foundation-model/amazon.titan-embed-text-v2:0"]
    # Members can't read or change it.
    staff = login(client, w, "staff")
    assert client.get("/api/admin/assistant/models", headers=staff).status_code == 403
    assert client.put("/api/admin/assistant/models", headers=staff,
                      json={"provider": "default"}).status_code == 403


def test_a_saved_secret_is_sealed_under_the_workspaces_key():
    import db
    import secret_box
    w = new_workspace()
    on_bedrock(w)
    conn = db.connect()
    try:
        blob = conn.execute("SELECT access_keys FROM model_providers WHERE workspace = ?",
                            (w,)).fetchone()[0]
    finally:
        conn.close()
    assert SECRET.encode() not in bytes(blob) and bytes(blob).startswith(secret_box.PREFIX)
    assert models.for_workspace(w).spec.keys == (KEY_ID, SECRET)
    # A role needs no stored secret: switching drops the keys.
    model_providers_store.save(w, {"auth": "role", "role_arn": ROLE}, "admin")
    assert model_providers_store.get(w)["access_keys"] is None


# ---- Switching the embeddings rebuilds the index -------------------------------------------------
@pytest.fixture
def indexed(monkeypatch, tmp_path, fake_aws):
    """A workspace with one document, indexed (the job runner, Chroma: real)."""
    import ingest
    import rag
    import workspaces
    from eval.harness import HashEmbeddings
    from kb.services import indexing
    for module in (ingest, rag):
        monkeypatch.setattr(module, "OpenAIEmbeddings", lambda **kw: HashEmbeddings(dim=64))
        monkeypatch.setattr(module, "DB_FOLDER", str(tmp_path / "chroma"))
    monkeypatch.chdir(tmp_path)
    w = new_workspace()
    os.makedirs(os.path.join(workspaces.docs_root(w), "public"))
    with open(os.path.join(workspaces.docs_root(w), "public", "refunds.txt"), "w") as f:
        f.write("Refund policy\n\nCustomers may ask for a refund within 30 days of purchase.")

    def wait():
        deadline = time.time() + 30
        while time.time() < deadline:
            if indexing.index_status(w)["status"] != "indexing":
                return indexing.index_status(w)
            time.sleep(0.05)
        raise AssertionError("indexing didn't finish")
    indexing.schedule_index(w)
    assert wait()["status"] == "idle"
    return SimpleNamespace(ws=w, wait=wait)


def test_switching_the_embeddings_asks_first_then_rebuilds_the_index(client, indexed,
                                                                     fake_aws):
    from kb.services import indexing
    w = indexed.ws
    h = login(client, w)
    old = indexing.current(w)[0]
    assert indexing.embeddings_of(w) == "text-embedding-3-small"
    change = {"provider": "bedrock", "region": REGION, "auth": "keys",
              "access_key_id": KEY_ID, "secret_access_key": SECRET, "embed_dims": 256}
    r = client.put("/api/admin/assistant/models", headers=h, json=change)
    assert r.status_code == 409 and "rebuilt" in r.json()["detail"]
    assert models.for_workspace(w).provider == "openai"          # nothing saved
    r = client.put("/api/admin/assistant/models", headers=h,
                   json={**change, "confirm_reindex": True})
    assert r.status_code == 200, r.text
    assert r.json()["reindex"] is True
    assert indexed.wait()["status"] == "idle"
    new = indexing.current(w)[0]
    assert new != old and new.endswith("__g2")
    assert indexing.embeddings_of(w) == "bedrock:amazon.titan-embed-text-v2:0:256"
    assert indexing.fingerprint(w) != indexing.fingerprint(new_workspace())
    embedded = fake_aws.runtime.of("invoke_model")
    assert embedded and {c[1] for c in embedded} == {"amazon.titan-embed-text-v2:0"}
    # The same embeddings again: no rebuild, nothing to confirm.
    r = client.put("/api/admin/assistant/models", headers=h,
                   json={"chat_model": "amazon.nova-lite-v1:0"})
    assert r.status_code == 200 and r.json()["reindex"] is False


def test_until_the_rebuild_is_done_another_providers_index_is_searched_by_keywords_only(
        indexed, fake_aws, no_openai):
    """The workspace left OpenAI but its index is OpenAI's: questions aren't embedded
    by OpenAI, nor by Bedrock against OpenAI's vectors."""
    import rag
    from kb.services import indexing
    w = indexed.ws
    on_bedrock(w)                                   # saved, not rebuilt yet
    on = models.for_workspace(w, index_embeddings=indexing.embeddings_of(w))
    assert on.query_embeddings() is None
    with models.scope(on):
        retriever, _ = rag.load_chain("public", indexing.current(w)[0])
    assert isinstance(retriever.dense, rag._NoHits)
    found = retriever.invoke("refund within 30 days")
    assert found and "refund" in found[0].page_content.lower()
    assert not no_openai and not fake_aws.runtime.of("invoke_model")


# ---- Fail closed: nothing reaches OpenAI ------------------------------------------------------
def test_a_bedrock_workspace_never_calls_openai(client, indexed, fake_aws, no_openai,
                                                monkeypatch):
    """A full question (guards, HyDE, the agent streaming, follow-ups, a title, the
    semantic answer cache), an index run and a digest, with every OpenAI client
    made to raise: all of it on the workspace's Bedrock models."""
    import assistant_store
    import digests_store
    import mailer
    from kb.services import answering, digests, indexing, insights, suggestions, summaries
    w = indexed.ws
    h = login(client, w)
    on_bedrock(w, chat_model="amazon.nova-pro-v1:0", fast_model="amazon.nova-micro-v1:0")
    assistant_store.save_settings(w, {"answer_cache": "semantic"}, "admin")
    monkeypatch.setenv("ANSWER_CACHE", "on")
    monkeypatch.setenv("CHAT_AUTO_TITLE", "on")
    monkeypatch.setenv("CHAT_FOLLOWUPS", "llm")
    monkeypatch.setattr(mailer, "send", lambda *a, **k: None)

    # INDEX: a rebuild with the new embeddings, then an incremental run.
    indexing.schedule_index(w)
    assert indexed.wait()["status"] == "idle"
    assert indexing.embeddings_of(w).startswith("bedrock:")

    # ASK, streamed, as a member.
    events = []
    staff = {"username": f"staff@{w}.example", "role": "public"}
    done = answering.ask(w, staff, "public", "How long do customers have to ask for a refund?",
                         surface="web", on_event=events.append)
    assert done.state != "blocked", done.answer
    assert "30 days" in done.answer
    assert any(e["type"] == "answer" and "30" in e["text"] for e in events)   # streamed
    with models.scope_for(w):
        assert suggestions.chat_title("How long do customers have to ask for a refund?")
        suggestions.followups("refunds?", done.answer, done.state, done.sources)

    # The guards ran on the workspace's FAST model; the answer on its chat model.
    calls = fake_aws.runtime.calls
    guards = [c for c in calls if "prompt-injection" in _text_of(c[2])]
    assert guards and {c[1] for c in guards} == {"amazon.nova-micro-v1:0"}
    assert any(c[0] == "converse_stream" and c[1] == "amazon.nova-pro-v1:0"
               and "toolConfig" in c[2] for c in calls)

    # DIGEST: the change summarised by the workspace's FAST model.
    r = client.post("/api/digests", headers=h, json={"frequency": "weekly", "channel": "email"})
    assert r.status_code == 200, r.text
    sub = r.json()
    client.post("/api/docs", headers=h, json={"name": "leave.txt", "access": "public",
                                              "content": "Twenty days of leave a year."})
    import db
    with db.connect() as conn:
        conn.execute("UPDATE digest_subscriptions SET next_due_at = 0, created_at = ? "
                     "WHERE id = ?", (time.time() - 3600, sub["id"]))
    before = len(calls)
    digests.send(digests_store.subscription(w, sub["id"]))
    assert any(c[0] == "converse" and c[1] == "amazon.nova-micro-v1:0"
               and "leave" in _text_of(c[2]).lower() for c in calls[before:])
    with models.scope_for(w):
        assert summaries.llm("Summarise: twenty days of leave.")
        assert insights.label(["How much leave?", "Leave carry over?"])

    assert no_openai == []


def test_the_answer_cache_keeps_providers_apart(monkeypatch, fake_aws):
    from kb.services import answer_cache
    w = new_workspace()
    with models.scope_for(w):
        openai_release = answer_cache._release()
    on_bedrock(w)
    with models.scope_for(w):
        bedrock_release = answer_cache._release()
    assert openai_release != bedrock_release and "bedrock" in bedrock_release


# ---- Streaming, tool calls, the guards ---------------------------------------------------------
def test_replies_are_plain_text_streamed_and_whole(fake_aws):
    w = new_workspace()
    on_bedrock(w)
    fake_aws.runtime.answer = "Thirty days, then store credit."
    chat = models.for_workspace(w).answer(temperature=0)
    whole = chat.invoke("How long?")
    assert whole.content == "Thirty days, then store credit."
    assert whole.usage_metadata["input_tokens"] == 70          # 40 + 30 read from the cache
    assert whole.usage_metadata["input_token_details"]["cache_read"] == 30
    chunks = list(chat.stream("How long?"))
    assert all(isinstance(c.content, str) for c in chunks)
    assert "".join(c.content for c in chunks).strip() == "Thirty days, then store credit."


def test_the_system_prompt_is_marked_for_bedrocks_prompt_cache(fake_aws, monkeypatch):
    from langchain_core.messages import HumanMessage, SystemMessage
    w = new_workspace()
    on_bedrock(w)
    chat = models.for_workspace(w).answer(temperature=0)
    chat.invoke([SystemMessage(content="The rules."), HumanMessage(content="Hi")])
    (_, _, request), = fake_aws.runtime.of("converse")
    assert request["system"] == [{"text": "The rules."}, {"cachePoint": {"type": "default"}}]
    monkeypatch.setenv("BEDROCK_PROMPT_CACHE", "off")
    assert not models.for_workspace(w).answer().prompt_cache


def test_tool_calls_come_back_as_tool_calls(fake_aws):
    from langchain_core.tools import tool

    @tool
    def calculator(expression: str) -> str:
        """Work out an arithmetic expression."""
        return "4"
    w = new_workspace()
    on_bedrock(w)
    fake_aws.runtime.tool_call = {"name": "calculator", "input": {"expression": "2+2"}}
    reply = models.for_workspace(w).answer(temperature=0).bind_tools([calculator]).invoke(
        "What is 2+2?")
    assert reply.tool_calls == [{"name": "calculator", "args": {"expression": "2+2"},
                                 "id": "tool-1", "type": "tool_call"}]
    (_, _, request), = fake_aws.runtime.of("converse")
    assert request["toolConfig"]["tools"][0]["toolSpec"]["name"] == "calculator"


def test_the_guards_classify_with_the_workspaces_bedrock_model(fake_aws):
    import safety
    w = new_workspace()
    on_bedrock(w, fast_model="amazon.nova-lite-v1:0")
    with models.scope_for(w):
        assert safety.check_prompt_injection("What is our refund policy?")[0]
        fake_aws.runtime.answer = "BLOCK"
    seen = [c for c in fake_aws.runtime.calls if "prompt-injection" in _text_of(c[2])]
    assert seen and seen[0][1] == "amazon.nova-lite-v1:0"


def test_an_api_reranker_is_skipped_for_a_bedrock_workspace_a_private_one_is_not(fake_aws):
    import rerankers
    w = new_workspace()
    on_bedrock(w)
    on = models.for_workspace(w)
    assert not on.allows_reranker(rerankers.CohereReranker("key"))
    assert on.allows_reranker(rerankers.LocalCrossEncoder())
    assert on.allows_reranker(rerankers.BedrockReranker())
    assert models.for_workspace(new_workspace()).allows_reranker(rerankers.CohereReranker("k"))


def test_bedrocks_reranker_calls_the_rerank_api(fake_aws):
    import rerankers
    w = new_workspace()
    on_bedrock(w)
    agent_runtime = boto3.client("bedrock-agent-runtime", region_name=REGION,
                                 aws_access_key_id="x", aws_secret_access_key="y")
    stub = Stubber(agent_runtime)
    stub.add_response("rerank", {"results": [{"index": 1, "relevanceScore": 0.9},
                                             {"index": 0, "relevanceScore": 0.2}]}, {
        "queries": [{"type": "TEXT", "textQuery": {"text": "refunds"}}],
        "sources": [{"type": "INLINE", "inlineDocumentSource": {
            "type": "TEXT", "textDocument": {"text": t}}} for t in ("leave", "refund rules")],
        "rerankingConfiguration": {"type": "BEDROCK_RERANKING_MODEL",
                                   "bedrockRerankingConfiguration": {
                                       "numberOfResults": 2, "modelConfiguration": {
                                           "modelArn": f"arn:aws:bedrock:{REGION}::"
                                                       "foundation-model/cohere.rerank-v3-5:0"}}}})
    fake_aws.made["stubs"]["bedrock-agent-runtime"] = agent_runtime
    with stub, models.scope_for(w):
        assert rerankers.BedrockReranker().scores("refunds", ["leave", "refund rules"]) == [0.2,
                                                                                            0.9]


# ---- Cost and the question log ---------------------------------------------------------------
def test_bedrock_models_are_priced_or_unknown_never_guessed():
    from kb.services import insights
    assert insights.cost_of("amazon.nova-pro-v1:0", 1_000_000, 1_000_000) == pytest.approx(4.0)
    assert insights.cost_of("us.amazon.nova-lite-v1:0", 1_000_000, 0) == pytest.approx(0.06)
    assert insights.cost_of("global.anthropic.claude-haiku-4-5-20251001-v1:0", 1_000_000,
                            1_000_000) == pytest.approx(6.0)
    assert insights.cost_of("meta.llama3-70b-instruct-v1:0", 1000, 1000) is None
    assert insights.cost_of("gpt-4o-mini", 1_000_000, 0) == pytest.approx(0.15)   # as before
    assert insights.cost_of("some-new-gpt", 1_000_000, 0) == pytest.approx(0.15)


def test_the_question_log_records_the_provider_and_model(fake_aws):
    import question_log_store
    from kb.services import insights, question_log
    w = new_workspace()
    on_bedrock(w)
    usage = SimpleNamespace(tokens_in=100, tokens_out=10, tokens_cached=None,
                            model="meta.llama3-70b-instruct-v1:0")
    question_log.record(w, "staff", "public", "web", "Hi?", "answered", answer="Hello",
                        usage=usage)
    (row,) = question_log_store.rows(w)
    assert (row["provider"], row["model"]) == ("bedrock", "meta.llama3-70b-instruct-v1:0")
    day = question_log_store.day_of(time.time())
    report = insights.report(w, "hr", day, day)
    assert report["totals"]["cost_unknown_models"] == ["meta.llama3-70b-instruct-v1:0"]
    assert report["totals"]["cost_usd"] == 0                   # not guessed


# ---- The one-shot chat when Bedrock fails --------------------------------------------------
def test_the_one_shot_chat_says_plainly_when_bedrock_fails(client, fake_aws, no_openai):
    """Bedrock throttles the answer: POST /api/chat says so with a 502, never a
    bare 500, and nothing falls back to OpenAI."""
    from botocore.exceptions import ClientError
    w = new_workspace()
    on_bedrock(w)

    def throttled(**request):
        raise ClientError({"Error": {"Code": "ThrottlingException", "Message": "Too many"}},
                          "ConverseStream")
    fake_aws.runtime.converse_stream = throttled
    fake_aws.runtime.converse = throttled
    r = client.post("/api/chat", json={"question": "How long do refunds take?"},
                    headers=login(client, w, "staff"))
    assert r.status_code == 502, r.text
    assert r.json()["detail"].startswith("The assistant couldn't answer just now (")
    assert no_openai == []


def test_the_one_shot_chat_says_plainly_when_bedrock_isnt_set_up(client, fake_aws, no_openai):
    """A workspace on Bedrock whose settings can't be used (no AWS region): a 502
    naming the error, not a 500, and not OpenAI instead."""
    import db
    w = new_workspace()
    on_bedrock(w)
    conn = db.connect()
    with conn:
        conn.execute("UPDATE model_providers SET region = NULL WHERE workspace = ?", (w,))
    conn.close()
    models.forget()
    r = client.post("/api/chat", json={"question": "How long do refunds take?"},
                    headers=login(client, w, "staff"))
    assert r.status_code == 502, r.text
    assert "ProviderError" in r.json()["detail"]
    assert no_openai == []
