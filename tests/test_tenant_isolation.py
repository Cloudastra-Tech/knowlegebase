"""
Tenant isolation through the routes: an admin of workspace B, signed in and
viewing as HR, can never see or change workspace A's documents, search index,
index status, knowledge listing, connections, conversations or answers, even
knowing their names and ids. (Members admin: test_member_isolation.py; a
passage by id: test_citations.py; search itself: eval/harness.py's leak suite.)

Real indexing and Chroma; only the embedding model is faked (test_indexing.env).
"""

from test_indexing import env  # noqa: F401  (a fixture)

SECRET = "Project Bluebird ships to the Lisbon office on the ninth of March."


def two_workspaces(env):  # noqa: F811
    from kb.services import indexing
    a = env.workspace(("public", "bluebird.txt", SECRET),
                      ("restricted", "bluebird-pay.txt", "Bluebird bonuses are private."))
    b = env.workspace(("public", "handbook.txt", "Workspace B's own handbook."))
    for ws in (a, b):
        indexing.schedule_index(ws)
        env.wait(ws)
    return a, b, env.login(a), env.login(b)


def test_documents_and_the_index_stay_in_their_workspace(env):  # noqa: F811
    import integrations_store
    a, b, admin_a, admin_b = two_workspaces(env)
    get = lambda path, h: env.client.get(path, headers=h)  # noqa: E731

    # A sees its own; B sees only B's.
    names = lambda r: {d["name"] for d in r.json()["docs"]}  # noqa: E731
    assert names(get("/api/docs", admin_a)) == {"bluebird.txt", "bluebird-pay.txt"}
    assert names(get("/api/docs", admin_b)) == {"handbook.txt"}
    for path in ("/api/docs/public/bluebird.txt", "/api/docs/restricted/bluebird-pay.txt"):
        assert get(path, admin_a).status_code == 200
        assert get(path, admin_b).status_code == 404

    # B can't change or delete A's document by its name: it's B's own folder.
    r = env.client.put("/api/docs/public/bluebird.txt", json={"content": "overwritten"},
                       headers=admin_b)
    assert r.status_code == 404
    assert env.client.delete("/api/docs/public/bluebird.txt",
                             headers=admin_b).status_code == 404
    assert get("/api/docs/public/bluebird.txt", admin_a).json()["content"] == SECRET

    # The knowledge listing and the search index.
    titles = lambda r: {i["title"] for i in r.json()["items"]}  # noqa: E731
    assert titles(get("/api/knowledge", admin_a)) == {"bluebird.txt", "bluebird-pay.txt"}
    assert titles(get("/api/knowledge", admin_b)) == {"handbook.txt"}
    assert {k for k, _ in env.chunks(b).values()} == {"public/handbook.txt"}
    assert all(not k.startswith("public/bluebird") for k, _ in env.chunks(b).values())

    # Index status: B's own index job, not A's.
    status_b = get("/api/index/status", admin_b).json()
    assert status_b["status"] == "idle" and "bluebird" not in str(status_b)
    # A re-index B asks for runs on B's documents only.
    assert env.client.post("/api/reindex", headers=admin_b).status_code == 200
    env.wait(b)
    assert {k for k, _ in env.chunks(b).values()} == {"public/handbook.txt"}

    # Connections: A's Notion connection isn't B's.
    integrations_store.connect(a, "notion", "A's Notion", "secret-token", "admin")
    try:
        listed = {i["id"]: i for i in get("/api/integrations", admin_b).json()["integrations"]}
        assert listed["notion"]["status"] != "connected"
        assert get("/api/integrations", admin_b).json()["workspace"] == b
        assert env.client.delete("/api/integrations/notion", headers=admin_b).status_code in (
            200, 404)
        assert integrations_store.get(a, "notion") is not None    # still A's
    finally:
        integrations_store.remove(a, "notion")


def test_conversations_stay_with_their_owner_across_workspaces(env):  # noqa: F811
    import conversations_store
    a, b, admin_a, admin_b = two_workspaces(env)
    cid = env.client.post("/api/conversations", json={}, headers=admin_a).json()["id"]
    question = conversations_store.add_message(cid, "user", "What is Project Bluebird?")
    mid = conversations_store.add_message(cid, "assistant", SECRET, parent_id=question,
                                          state="answered")

    assert env.client.get(f"/api/conversations/{cid}", headers=admin_a).status_code == 200
    for method, path, body in (
            ("get", f"/api/conversations/{cid}", None),
            ("patch", f"/api/conversations/{cid}", {"title": "mine now"}),
            ("get", f"/api/messages/{mid}/markdown", None),
            ("put", f"/api/messages/{mid}/feedback", {"rating": 1}),
            ("delete", f"/api/conversations/{cid}", None)):
        r = env.client.request(method.upper(), path, json=body, headers=admin_b)
        assert r.status_code == 404, (method, path, r.status_code, r.text)
    # Forking someone else's answer reads exactly as forking one that doesn't exist.
    fork = lambda m: env.client.post(f"/api/conversations/{cid}/fork",  # noqa: E731
                                     json={"message_id": m}, headers=admin_b)
    theirs, missing = fork(mid), fork("0" * 32)
    assert (theirs.status_code, theirs.json()) == (missing.status_code, missing.json())
    assert theirs.status_code in (400, 404)
    listed = env.client.get("/api/conversations", headers=admin_b).json()["conversations"]
    assert cid not in {c["id"] for c in listed}
    found = env.client.get("/api/conversations/search", params={"q": "Bluebird"},
                           headers=admin_b).json()
    assert "Bluebird" not in str(found)
    assert env.client.get(f"/api/conversations/{cid}", headers=admin_a).status_code == 200
