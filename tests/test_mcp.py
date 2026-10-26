"""
The MCP server (kb/mcp_server.py), through the official MCP SDK's own client
speaking Streamable HTTP to the app: personal keys only, tools/list shows what
the caller may use, each tool call is checked again against the key, the
person's role now and the admins' toggles, and every call is audited and in
the question log with surface "mcp".

The index is real (hash embeddings); the chat models are fakes (test_api_keys).
"""

import asyncio
import json

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

import audit_store
from test_api_keys import CONTRACT, kb  # noqa: F401  (fixtures)
from test_indexing import env  # noqa: F401


def mcp(key, *calls):
    """Connect to /mcp with `key` and run each call(client) in turn -> their results."""
    import api

    async def go():
        async with api.app.state.mcp.running():
            http = httpx2.AsyncClient(transport=httpx2.ASGITransport(app=api.app),
                                      base_url="http://kb.test",
                                      headers={"Authorization": f"Bearer {key}"})
            async with http, Client(streamable_http_client("http://kb.test/mcp",
                                                           http_client=http)) as client:
                return [await call(client) for call in calls]
    return asyncio.run(go())


def tools(client):
    async def names():
        return sorted(t.name for t in (await client.list_tools()).tools)
    return names()


def call(name, **arguments):
    async def run(client):
        result = await client.call_tool(name, arguments)
        text = result.content[0].text if result.content else ""
        return {"error": text} if result.is_error else json.loads(text)
    return run


def test_the_tools_a_key_sees_and_the_admin_toggles(kb):  # noqa: F811
    w = kb.workspace_with_docs()
    key = kb.key(w, "hr", ["ask", "search", "docs:read"])
    assert mcp(key, tools) == [["ask", "list_sources", "search_knowledge"]]

    # read_document: off until an admin turns it on.
    off = mcp(key, call("read_document", doc_key="public/guide.txt"))[0]["error"]
    assert "haven't turned on read_document" in off
    admin = kb.session(w)
    r = kb.client.put("/api/admin/api", headers=admin,
                      json={"mcp_tools": ["search_knowledge", "read_document"]})
    assert r.status_code == 200, r.text
    listed, doc, asked = mcp(key, tools, call("read_document", doc_key="restricted/contract.txt"),
                             call("ask", question="What is the refund policy?"))
    assert listed == ["read_document", "search_knowledge"]
    assert doc["text"] == CONTRACT and doc["access"] == "restricted" and not doc["truncated"]
    assert "turned on ask" in asked["error"]

    # A key without a tool's scope doesn't see it.
    narrow = kb.key(w, "hr", ["search"])
    assert mcp(narrow, tools) == [["search_knowledge"]]


def test_tools_answer_with_what_the_person_may_see_now(kb):  # noqa: F811
    w = kb.workspace_with_docs()
    kb.client.put("/api/admin/api", headers=kb.session(w),
                  json={"mcp_tools": ["search_knowledge", "ask", "read_document",
                                      "list_sources"]})
    key = kb.key(w, "hr", ["ask", "search", "docs:read"])
    found, asked, sources = mcp(key, call("search_knowledge", query="Brightlearn contract", k=3),
                                call("ask", question="What is the refund policy?"),
                                call("list_sources"))
    assert "contract.txt" in {r["title"] for r in found["results"]}
    assert asked["state"] == "answered" and "[S1]" in asked["answer"]
    assert "guide.txt" in {s["title"] for s in asked["sources"]}
    assert {"guide.txt", "contract.txt"} <= {s["title"] for s in sources["sources"]}

    r = kb.client.patch(f"/api/admin/members/hr@{w}.example", headers=kb.session(w),
                        json={"role": "public"})
    assert r.status_code == 200
    found, doc, sources = mcp(key, call("search_knowledge", query="Brightlearn contract", k=20),
                              call("read_document", doc_key="restricted/contract.txt"),
                              call("list_sources"))
    assert "contract.txt" not in {r["title"] for r in found["results"]}
    assert "may not see" in doc["error"]
    assert "contract.txt" not in {s["title"] for s in sources["sources"]}


def test_every_tool_call_is_audited_and_questions_are_logged(kb):  # noqa: F811
    import question_log_store
    w = kb.workspace_with_docs()
    key = kb.key(w, "staff", ["ask", "search"])
    question = "What is the refund policy?"
    mcp(key, call("search_knowledge", query="refunds"), call("ask", question=question),
        call("list_sources"))
    events = [e for e in audit_store.list_events(w, limit=50)[0] if e["actor_type"] == "api_key"]
    assert [e["action"] for e in events] == ["mcp.list_sources", "mcp.ask",
                                             "mcp.search_knowledge"]
    assert all(e["details"]["surface"] == "mcp" and e["actor"] == f"staff@{w}.example"
               for e in events)
    assert question not in json.dumps(events)
    (logged,) = question_log_store.rows(w)
    assert logged["surface"] == "mcp" and logged["outcome"] == "answered"


def test_only_a_personal_key_of_a_workspace_with_mcp_on_gets_in(kb):  # noqa: F811
    import api
    w = kb.workspace_with_docs()
    service = kb.key(w, "admin", ["ask", "search"], kind="service")
    personal = kb.key(w, "staff", ["search"])

    async def post(key):
        async with api.app.state.mcp.running():
            async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=api.app),
                                          base_url="http://kb.test") as http:
                return await http.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                                       headers={"Authorization": f"Bearer {key}",
                                                "Accept": "application/json, text/event-stream"})
    r = asyncio.run(post("kb_live_nope"))
    assert r.status_code == 401 and r.headers["www-authenticate"].startswith("Bearer")
    r = asyncio.run(post(service))
    assert r.status_code == 403 and "personal" in r.json()["error_description"]
    kb.client.put("/api/admin/api", headers=kb.session(w), json={"mcp_enabled": False})
    r = asyncio.run(post(personal))
    assert r.status_code == 403 and "turned the MCP server off" in r.json()["error_description"]
    kb.client.put("/api/admin/api", headers=kb.session(w), json={"mcp_enabled": True})
    assert mcp(personal, tools) == [["list_sources", "search_knowledge"]]


def test_mcp_answers_503_until_the_server_runs(kb):  # noqa: F811
    w = kb.workspace_with_docs()
    r = kb.client.post("/mcp", headers={"Authorization": f"Bearer {kb.key(w)}"}, json={})
    assert r.status_code == 503

