"""
API keys and the public API (api_keys_store.py, kb/api_auth.py, kb/routers/api_keys.py,
kb/routers/api_v1.py): a key is shown once and kept as a hash; it acts as its
person with their role as it is at each call, so demoting, turning off or
removing them limits it at once; scopes are enforced; a service key acts as a
public user only; one workspace's key never sees another's documents; every
call is audited as done with the key.

Through the real API, search index and agent: only the embedding model (a hash
stand-in) and the chat models (a fake that cites what it reads) are faked.
"""

import json
import re
import time

import pytest

import agent
import api_keys_store
import audit_store
import auth
import members_store
import rag
import safety
from kb import state
from test_citations import Allow, CitingModel
from test_indexing import env  # noqa: F401  (a fixture)

GUIDE = "# Guide\n\n## Refunds\nRefunds are paid within thirty days of a written request."
CONTRACT = "The Brightlearn vendor contract ends in March 2027 and renews for two years."
KEY = re.compile(r"^kb_live_[0-9a-f]{12}_[A-Za-z0-9_-]{40,}$")
ALL = ["ask", "search", "docs:read", "docs:write", "audit:read"]


@pytest.fixture
def kb(env, monkeypatch):  # noqa: F811
    """env (test_indexing) with fake chat models, and helpers for workspaces,
    people and keys."""
    monkeypatch.setattr(agent, "ChatOpenAI", lambda **kwargs: CitingModel(script={}))
    monkeypatch.setattr(rag, "ChatOpenAI", lambda **kwargs: Allow())
    monkeypatch.setattr(safety, "_get_intent_llm", lambda: Allow())
    monkeypatch.setattr(rag, "USE_HYDE", False)
    state.agents.clear()

    def workspace():
        """A workspace with an admin, an HR person and a member, a public guide and a
        restricted contract, indexed."""
        from kb.services import indexing
        w = env.workspace(("public", "guide.txt", GUIDE), ("restricted", "contract.txt", CONTRACT))
        members_store.set_password(f"hr@{w}.example", "HR", auth.hash_password("correct-horse"),
                                   w, "hr")
        indexing.schedule_index(w)
        env.wait(w)
        return w

    def session(w, who="admin"):
        h = env.login(w, "admin" if who == "admin" else "staff")
        if who == "hr":
            r = env.client.post("/api/auth/login", json={"username": f"hr@{w}.example",
                                                         "password": "correct-horse"})
            h = {"Authorization": f"Bearer {r.json()['token']}", "X-Role": "hr"}
        return h

    def key(w, who="admin", scopes=ALL, kind="personal", **extra):
        path = "/api/admin/api/service-keys" if kind == "service" else "/api/me/api-keys"
        r = env.client.post(path, headers=session(w, who),
                            json={"name": f"{who} key", "scopes": scopes, **extra})
        assert r.status_code == 200, r.text
        return r.json()["key"]

    env.workspace_with_docs, env.session, env.key = workspace, session, key
    yield env
    state.agents.clear()


def bearer(key):
    return {"Authorization": f"Bearer {key}"}


def titles(r):
    assert r.status_code == 200, r.text
    return {x["title"] for x in r.json()["results"]}


# ---- Keys: made, shown once, used, revoked -------------------------------------------
def test_a_key_is_shown_once_and_only_its_hash_is_kept(kb):
    w = kb.workspace_with_docs()
    h = kb.session(w, "staff")
    r = kb.client.post("/api/me/api-keys", headers=h,
                       json={"name": "  My   script ", "scopes": ["ask", "search"], "days": 30})
    assert r.status_code == 200, r.text
    made = r.json()
    assert KEY.match(made["key"]) and made["name"] == "My script"
    assert made["kind"] == "personal" and made["scopes"] == ["ask", "search"]
    assert 29 * 86400 < made["expires_at"] - time.time() <= 30 * 86400

    listed = kb.client.get("/api/me/api-keys", headers=h).json()
    assert [k["id"] for k in listed["keys"]] == [made["id"]]
    assert made["key"] not in json.dumps(listed) and "hash" not in listed["keys"][0]
    assert listed["keys"][0]["hint"] == f"kb_live_{made['prefix']}_…"
    assert listed["scopes"] == ["ask", "search", "docs:read"]      # what a member may give
    assert listed["mcp_url"].endswith("/mcp") and listed["api_url"].endswith("/api/v1")
    import db
    with db.connect() as conn:
        stored = dict(conn.execute("SELECT * FROM api_keys WHERE id = ?", (made["id"],)).fetchone())
    assert made["key"] not in json.dumps(stored)
    assert stored["hash"] == api_keys_store._hash(made["key"])

    assert kb.client.get("/api/v1/sources", headers=bearer(made["key"])).status_code == 200
    assert kb.client.get("/api/me/api-keys", headers=h).json()["keys"][0]["last_used_at"]


def test_revoked_expired_and_made_up_keys_are_refused_alike(kb):
    w = kb.workspace_with_docs()
    key = kb.key(w, "staff", ["search"])
    other = kb.key(w, "staff", ["search"])
    assert kb.client.get("/api/v1/sources", headers=bearer(key)).status_code == 200

    h = kb.session(w, "staff")
    mine = kb.client.get("/api/me/api-keys", headers=h).json()["keys"]
    first = next(k for k in mine if key.startswith(f"kb_live_{k['prefix']}_"))
    assert kb.client.delete(f"/api/me/api-keys/{first['id']}", headers=h).status_code == 200
    assert kb.client.delete(f"/api/me/api-keys/{first['id']}", headers=h).status_code == 404

    import db
    with db.connect() as conn:
        conn.execute("UPDATE api_keys SET expires_at = ? WHERE prefix = ?",
                     (time.time() - 1, other.split("_")[2]))
        conn.commit()
    for bad in (key, other, key[:-2] + "xx", "kb_live_000000000000_" + "a" * 43, "nonsense", ""):
        r = kb.client.get("/api/v1/sources", headers=bearer(bad))
        assert r.status_code == 401, bad
        assert r.json()["detail"].startswith("A valid API key is required")
    statuses = {k["status"] for k in kb.client.get("/api/me/api-keys", headers=h).json()["keys"]}
    assert statuses == {"revoked", "expired"}


def test_keys_and_sessions_each_work_only_where_they_belong(kb):
    w = kb.workspace_with_docs()
    key = kb.key(w)
    session = kb.session(w)
    assert kb.client.get("/api/knowledge", headers=bearer(key)).status_code == 401
    assert kb.client.get("/api/v1/sources", headers=session).status_code == 401


def test_what_a_key_may_be_given(kb):
    w = kb.workspace_with_docs()
    staff = kb.session(w, "staff")

    def make(h, body, path="/api/me/api-keys"):
        return kb.client.post(path, headers=h, json={"name": "k", **body})
    assert make(staff, {"scopes": ["docs:write"]}).status_code == 400
    assert make(staff, {"scopes": ["audit:read"]}).status_code == 400
    assert make(staff, {"scopes": []}).status_code == 400
    assert make(staff, {"scopes": ["everything"]}).status_code == 400
    assert make(staff, {"scopes": ["ask"], "days": 10_000}).status_code == 400
    assert make(staff, {"scopes": ["ask"], "name": " "}).status_code == 400
    assert make(kb.session(w, "hr"), {"scopes": ["docs:write"]}).status_code == 200
    assert make(kb.session(w, "hr"), {"scopes": ["audit:read"]}).status_code == 400
    # Service keys: admins only, and never more than a public user's reading.
    assert make(kb.session(w, "hr"), {"scopes": ["ask"]},
                "/api/admin/api/service-keys").status_code == 403
    assert make(kb.session(w), {"scopes": ["docs:write"]},
                "/api/admin/api/service-keys").status_code == 400
    for _ in range(api_keys_store.MAX_PERSONAL - 0):
        make(staff, {"scopes": ["ask"]})
    assert "Revoke one" in make(staff, {"scopes": ["ask"]}).json()["detail"]


# ---- Scopes, and the role as it is now ------------------------------------------------
def test_each_endpoint_needs_its_scope(kb):
    w = kb.workspace_with_docs()
    only_search = kb.key(w, "admin", ["search"])
    calls = {"ask": ("post", "/api/v1/ask", {"question": "What about refunds?"}),
             "docs:read": ("get", "/api/v1/documents", None),
             "docs:write": ("post", "/api/v1/documents",
                            {"name": "n.txt", "access": "public", "content": "x"}),
             "audit:read": ("get", "/api/v1/audit", None)}
    for scope, (method, path, body) in calls.items():
        r = kb.client.request(method.upper(), path, headers=bearer(only_search), json=body)
        assert r.status_code == 403 and scope in r.json()["detail"], (scope, r.text)
    assert kb.client.post("/api/v1/search", headers=bearer(only_search),
                          json={"query": "refunds"}).status_code == 200


def test_a_demoted_person_s_key_is_limited_at_once(kb):
    w = kb.workspace_with_docs()
    key = kb.key(w, "hr", ["search", "docs:read", "docs:write"])
    assert "contract.txt" in titles(kb.client.post("/api/v1/search", headers=bearer(key),
                                                   json={"query": "Brightlearn contract"}))
    assert kb.client.get("/api/v1/documents/restricted/contract.txt",
                         headers=bearer(key)).json()["content"] == CONTRACT

    admin = kb.session(w)
    r = kb.client.patch(f"/api/admin/members/hr@{w}.example", headers=admin, json={"role": "public"})
    assert r.status_code == 200, r.text
    # The same key, a moment later: public now.
    assert "contract.txt" not in titles(kb.client.post("/api/v1/search", headers=bearer(key),
                                                       json={"query": "Brightlearn contract"}))
    assert kb.client.get("/api/v1/documents/restricted/contract.txt",
                         headers=bearer(key)).status_code == 404
    docs = kb.client.get("/api/v1/documents", headers=bearer(key)).json()["documents"]
    assert [d["name"] for d in docs] == ["guide.txt"]
    r = kb.client.post("/api/v1/documents", headers=bearer(key),
                       json={"name": "new.txt", "access": "public", "content": "hello"})
    assert r.status_code == 403 and "no longer" in r.json()["detail"]

    # Turned off, or removed: the key stops.
    kb.client.patch(f"/api/admin/members/hr@{w}.example", headers=admin, json={"role": "hr"})
    assert kb.client.get("/api/v1/documents", headers=bearer(key)).status_code == 200
    kb.client.patch(f"/api/admin/members/hr@{w}.example", headers=admin, json={"disabled": True})
    assert kb.client.get("/api/v1/documents", headers=bearer(key)).status_code == 401
    kb.client.patch(f"/api/admin/members/hr@{w}.example", headers=admin, json={"disabled": False})
    assert kb.client.get("/api/v1/documents", headers=bearer(key)).status_code == 200
    assert kb.client.delete(f"/api/admin/members/hr@{w}.example", headers=admin).status_code == 200
    assert kb.client.get("/api/v1/documents", headers=bearer(key)).status_code == 401


def test_an_admin_can_revoke_anyone_s_key_in_their_workspace_only(kb):
    w, other = kb.workspace_with_docs(), kb.workspace_with_docs()
    key = kb.key(w, "staff", ["search"])
    listed = kb.client.get("/api/admin/api", headers=kb.session(w)).json()["keys"]
    (row,) = [k for k in listed if k["created_by"] == f"staff@{w}.example"]
    assert kb.client.delete(f"/api/admin/api/keys/{row['id']}",
                            headers=kb.session(other)).status_code == 404
    assert kb.client.delete(f"/api/me/api-keys/{row['id']}",
                            headers=kb.session(w, "hr")).status_code == 404   # not theirs
    assert kb.client.get("/api/v1/sources", headers=bearer(key)).status_code == 200
    assert kb.client.delete(f"/api/admin/api/keys/{row['id']}",
                            headers=kb.session(w)).status_code == 200
    assert kb.client.get("/api/v1/sources", headers=bearer(key)).status_code == 401


# ---- Tenancy and service keys ---------------------------------------------------------
def test_a_key_never_reaches_another_workspace(kb):
    a = kb.workspace_with_docs()
    b = kb.env_b = kb.workspace(("public", "beta.txt", "Beta refunds take ninety days."))
    from kb.services import indexing
    indexing.schedule_index(b)
    kb.wait(b)
    key = kb.key(a)
    found = titles(kb.client.post("/api/v1/search", headers=bearer(key),
                                  json={"query": "refunds ninety days", "k": 20}))
    assert "beta.txt" not in found and "guide.txt" in found
    assert kb.client.get("/api/v1/documents/public/beta.txt", headers=bearer(key)).status_code == 404
    names = [d["name"] for d in kb.client.get("/api/v1/documents", headers=bearer(key)).json()["documents"]]
    assert "beta.txt" not in names
    events = kb.client.get("/api/v1/audit", headers=bearer(key)).json()["events"]
    assert events and {e["actor"].split("@")[-1] for e in events
                       if "@" in (e["actor"] or "")} <= {f"{a}.example"}


def test_a_service_key_acts_as_a_public_user_only(kb):
    w = kb.workspace_with_docs()
    admin = kb.session(w)
    r = kb.client.post("/api/admin/api/service-keys", headers=admin,
                       json={"name": "Intranet", "scopes": ["ask", "search", "docs:read"]})
    assert r.status_code == 200, r.text
    key = r.json()["key"]
    assert r.json()["kind"] == "service"
    # Made by an admin, it still never sees restricted documents.
    assert "contract.txt" not in titles(kb.client.post("/api/v1/search", headers=bearer(key),
                                                       json={"query": "Brightlearn contract"}))
    assert kb.client.get("/api/v1/documents/restricted/contract.txt",
                         headers=bearer(key)).status_code == 404
    answer = kb.client.post("/api/v1/ask", headers=bearer(key),
                            json={"question": "When does the Brightlearn contract end?"}).json()
    assert all(s["access"] == "public" for s in answer["sources"])
    assert "March 2027" not in answer["answer"]
    assert kb.client.post("/api/v1/ask", headers=bearer(key),
                          json={"question": "Hi", "conversation_id": "new"}).status_code == 400
    # It stops once the admin who made it isn't one.
    members_store.set_password(f"boss@{w}.example", "Boss", auth.hash_password("x"), w, "admin")
    kb.client.patch(f"/api/admin/members/admin@{w}.example",
                    headers=kb.session(w), json={"role": "hr"})
    assert kb.client.get("/api/v1/sources", headers=bearer(key)).status_code == 401


def test_the_admins_can_turn_the_api_off(kb):
    w = kb.workspace_with_docs()
    key = kb.key(w)
    r = kb.client.put("/api/admin/api", headers=kb.session(w), json={"api_enabled": False})
    assert r.status_code == 200 and r.json()["api_enabled"] is False
    r = kb.client.get("/api/v1/sources", headers=bearer(key))
    assert r.status_code == 403 and "turned the API off" in r.json()["detail"]
    assert kb.client.put("/api/admin/api", headers=kb.session(w, "hr"),
                         json={"api_enabled": True}).status_code == 403
    assert kb.client.put("/api/admin/api", headers=kb.session(w),
                         json={"mcp_tools": ["nope"]}).status_code == 400


# ---- The endpoints -----------------------------------------------------------------------
def test_ask_answers_with_citations_and_keeps_a_conversation(kb):
    w = kb.workspace_with_docs()
    key = kb.key(w, "staff", ["ask"])
    r = kb.client.post("/api/v1/ask", headers=bearer(key),
                       json={"question": "What is the refund policy?"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["state"] == "answered" and "[S1]" in body["answer"]
    assert body["sources"][0]["title"] == "guide.txt" and body["sources"][0]["n"] == 1
    assert body["conversation_id"] is None

    r = kb.client.post("/api/v1/ask", headers=bearer(key),
                       json={"question": "What is the refund policy?", "conversation_id": "new"})
    cid = r.json()["conversation_id"]
    assert cid and r.json()["message_id"]
    kb.client.post("/api/v1/ask", headers=bearer(key),
                   json={"question": "And how do I ask for one?", "conversation_id": cid})
    staff = kb.session(w, "staff")
    messages = kb.client.get(f"/api/conversations/{cid}", headers=staff).json()["messages"]
    assert [m["author"] for m in messages] == ["user", "assistant", "user", "assistant"]
    assert messages[1]["state"] == "answered"
    assert kb.client.post("/api/v1/ask", headers=bearer(key),
                          json={"question": "x", "conversation_id": "nope"}).status_code == 404


def test_ask_can_stream(kb):
    w = kb.workspace_with_docs()
    key = kb.key(w, "staff", ["ask"])
    r = kb.client.post("/api/v1/ask", headers=bearer(key),
                       json={"question": "What is the refund policy?", "stream": True})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    events = [json.loads(c[len("data: "):]) for c in r.text.split("\n\n") if c]
    assert events[0]["type"] == "status" and events[-1]["type"] == "done"
    assert any(e["type"] == "answer" for e in events)
    assert events[-1]["state"] == "answered" and events[-1]["sources"]


def test_a_refused_question_is_an_answer_not_an_error(kb):
    w = kb.workspace_with_docs()
    key = kb.key(w, "staff", ["ask", "search"])
    r = kb.client.post("/api/v1/ask", headers=bearer(key), json={"question": "x" * 4001})
    assert r.status_code == 422
    r = kb.client.post("/api/v1/search", headers=bearer(key), json={"query": "   "})
    assert r.status_code == 400


def test_search_returns_passages_and_no_answer(kb, monkeypatch):
    w = kb.workspace_with_docs()
    key = kb.key(w, "staff", ["search"])
    monkeypatch.setattr(CitingModel, "_answer", lambda *a: pytest.fail("search wrote an answer"))
    r = kb.client.post("/api/v1/search", headers=bearer(key), json={"query": "refunds", "k": 1})
    (hit,) = r.json()["results"]
    assert hit["title"] == "guide.txt" and hit["access"] == "public"
    assert hit["doc_key"] == "public/guide.txt" and "thirty days" in hit["text"]
    assert hit["doc_id"] and hit["chunk_id"] and hit["rank"] == 1
    assert kb.client.post("/api/v1/search", headers=bearer(key),
                          json={"query": "refunds", "k": 99}).status_code == 422


def test_documents_and_sources(kb, monkeypatch):
    from kb.services import indexing
    queued = []
    monkeypatch.setattr(indexing, "schedule_index", lambda ws, docs=None, **kw: queued.append(docs))
    w = kb.workspace_with_docs()
    key = kb.key(w, "hr", ["docs:read", "docs:write", "search"])
    r = kb.client.post("/api/v1/documents", headers=bearer(key),
                       json={"name": "faq.txt", "access": "restricted", "content": "Ask HR."})
    assert r.status_code == 200, r.text
    assert queued[-1] == ["restricted/faq.txt"]
    r = kb.client.post("/api/v1/documents/upload", headers=bearer(key),
                       files=[("files", ("notes.md", b"# Notes\nhello", "text/markdown"))],
                       data={"access": "public"})
    assert r.json() == {"saved": ["notes.md"]}
    names = {(d["access"], d["name"]) for d in
             kb.client.get("/api/v1/documents", headers=bearer(key)).json()["documents"]}
    assert names == {("public", "guide.txt"), ("public", "notes.md"),
                     ("restricted", "contract.txt"), ("restricted", "faq.txt")}
    assert kb.client.get("/api/v1/documents/restricted/faq.txt",
                         headers=bearer(key)).json()["content"] == "Ask HR."
    assert kb.client.delete("/api/v1/documents/restricted/faq.txt",
                            headers=bearer(key)).status_code == 200
    assert kb.client.get("/api/v1/documents/restricted/faq.txt",
                         headers=bearer(key)).status_code == 404
    sources = kb.client.get("/api/v1/sources", headers=bearer(key)).json()["sources"]
    assert {"guide.txt", "contract.txt"} <= {s["title"] for s in sources}


def test_the_audit_log_needs_an_admin_s_key(kb):
    w = kb.workspace_with_docs()
    admin_key = kb.key(w, "admin", ["audit:read"])
    r = kb.client.get("/api/v1/audit?limit=5", headers=bearer(admin_key))
    assert r.status_code == 200 and len(r.json()["events"]) <= 5
    r = kb.client.get("/api/v1/audit?action=api_key.", headers=bearer(admin_key))
    assert {e["action"] for e in r.json()["events"]} == {"api_key.create"}


# ---- Every call is audited -------------------------------------------------------------
def test_every_call_is_audited_as_done_with_the_key(kb):
    w = kb.workspace_with_docs()
    key = kb.key(w, "staff", ["ask", "search", "docs:read"])
    prefix = key.split("_")[2]
    question = "What is the refund policy?"
    kb.client.post("/api/v1/ask", headers=bearer(key), json={"question": question})
    kb.client.post("/api/v1/search", headers=bearer(key), json={"query": "refunds"})
    kb.client.get("/api/v1/sources", headers=bearer(key))
    kb.client.get("/api/v1/documents", headers=bearer(key))
    events = [e for e in audit_store.list_events(w, limit=50)[0] if e["actor_type"] == "api_key"]
    assert [e["action"] for e in events] == ["api.documents.list", "api.sources.list",
                                             "api.search", "api.ask"]
    for e in events:
        assert e["actor"] == f"staff@{w}.example"
        assert e["details"]["surface"] == "api" and e["details"]["key"] == prefix
    ask = events[-1]
    assert ask["details"]["state"] == "answered" and ask["details"]["chars"] == len(question)
    assert question not in json.dumps(events)
    made = [e for e in audit_store.list_events(w, limit=50)[0] if e["action"] == "api_key.create"]
    assert made and made[0]["details"]["prefix"] == prefix and key not in json.dumps(made)


def test_keys_are_rate_limited(kb, monkeypatch):
    w = kb.workspace_with_docs()
    key = kb.key(w, "staff", ["search"])
    monkeypatch.setenv("RATE_LIMITS", "on")
    monkeypatch.setenv("RATE_LIMIT_API_KEY", "2")
    from kb import state as kb_state
    with kb_state.hits_lock:
        kb_state.hits.clear()
    codes = [kb.client.get("/api/v1/sources", headers=bearer(key)).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    with kb_state.hits_lock:
        kb_state.hits.clear()


# ---- The OpenAPI document ----------------------------------------------------------------
def test_the_public_api_publishes_its_own_openapi_only(monkeypatch):
    from fastapi.testclient import TestClient
    from kb.app import create_app
    monkeypatch.delenv("API_DOCS", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    client = TestClient(create_app())
    assert client.get("/openapi.json").status_code != 200 or \
        "openapi" not in client.get("/openapi.json").text
    r = client.get("/api/v1/openapi.json")
    assert r.status_code == 200
    doc = r.json()
    assert doc["info"]["title"] == "Knowledge Assistant API"
    assert doc["paths"] and all(p.startswith("/api/v1/") for p in doc["paths"])
    assert "/api/v1/openapi.json" not in doc["paths"]
    assert {"/api/v1/ask", "/api/v1/search", "/api/v1/documents", "/api/v1/sources",
            "/api/v1/audit"} <= set(doc["paths"])
    assert doc["components"]["securitySchemes"]["HTTPBearer"]["scheme"] == "bearer"
