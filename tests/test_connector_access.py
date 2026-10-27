"""
Who may ask about files synced by a connector (Google Drive, Notion), and who
gets the Outline wiki — tested through the real API, index and retriever.

Only OpenAI's embeddings are faked (deterministic vectors, no network); the
indexing (ingest.py), the access tags, the permission-filtered retriever
(rag.py) and the role checks are the real code.
"""

import json
import os
import time
import uuid

import pytest

SECRET = "The launch codename is BLUEFALCON and nobody outside HR may know it."
QUESTION = "What is the launch codename BLUEFALCON?"


@pytest.fixture
def env(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from langchain_core.embeddings import DeterministicFakeEmbedding
    import api
    import auth
    import ingest
    import members_store
    import rag

    fake = lambda **kwargs: DeterministicFakeEmbedding(size=32)   # noqa: E731
    monkeypatch.setattr(ingest, "OpenAIEmbeddings", fake)
    monkeypatch.setattr(rag, "OpenAIEmbeddings", fake)
    monkeypatch.setattr(ingest, "DB_FOLDER", str(tmp_path / "chroma"))
    monkeypatch.setattr(rag, "DB_FOLDER", str(tmp_path / "chroma"))
    monkeypatch.chdir(tmp_path)          # workspace documents live under data/workspaces/

    ws = f"acme-{uuid.uuid4().hex[:8]}"
    password = auth.hash_password("correct-horse")
    for name, role in (("admin", "admin"), ("hr", "hr"), ("staff", "public")):
        members_store.set_password(f"{name}@{ws}.example", name.title(), password, ws, role)
    return TestClient(api.app), ws


def login(client, ws, name, role="hr"):
    r = client.post("/api/auth/login", json={"username": f"{name}@{ws}.example",
                                             "password": "correct-horse"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}", "X-Role": role}


def connect_notion(ws):
    """A Notion connection whose sync has written one page (as notion.sync does)."""
    import integrations_store
    import workspaces
    integrations_store.connect(ws, "notion", "Acme", {"access_token": "x"}, "admin")
    folder = workspaces.connector_dir(ws, "notion")
    os.makedirs(folder)
    with open(os.path.join(folder, "page1.txt"), "w", encoding="utf-8") as f:
        f.write(f"Launch plan\n\n{SECRET}")
    with open(os.path.join(folder, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump({"page1.txt": {"id": "page1", "title": "Launch plan",
                                 "url": "https://notion.so/page1", "provider": "Notion"}}, f)


def wait_for_index(client, headers):
    deadline = time.time() + 60
    while time.time() < deadline:
        state = client.get("/api/index/status", headers=headers).json()
        if state["status"] != "indexing":
            assert state["status"] == "idle", state
            return state
        time.sleep(0.1)
    raise AssertionError("indexing didn't finish")


def reindex(client, headers):
    assert client.post("/api/reindex", headers=headers).status_code == 200
    return wait_for_index(client, headers)


def found(ws, role):
    """Does `role`'s permission-filtered search reach the synced page?"""
    import rag
    from kb.services import indexing
    retriever, _ = rag.load_chain(role, indexing.current(ws)[0])
    return any("BLUEFALCON" in d.page_content for d in retriever.invoke(QUESTION))


def knowledge(client, headers):
    return {i["title"]: i["access"] for i in
            client.get("/api/knowledge", headers=headers).json()["items"]}


def notion_access(client, headers):
    items = client.get("/api/integrations", headers=headers).json()["integrations"]
    return next(i for i in items if i["id"] == "notion")["access"]


def test_synced_files_are_hr_only_until_an_admin_opens_them_to_everyone(env):
    client, ws = env
    admin, staff = login(client, ws, "admin"), login(client, ws, "staff")
    connect_notion(ws)
    summary = reindex(client, admin)["summary"]
    assert summary["restricted"] == summary["chunks"] > 0

    assert notion_access(client, admin) == "restricted"
    assert found(ws, "hr")
    assert not found(ws, "public")
    assert knowledge(client, admin) == {"Launch plan": "restricted"}
    assert knowledge(client, staff) == {}              # not even the title

    r = client.put("/api/integrations/notion/access", json={"access": "everyone"},
                   headers=admin)
    assert r.status_code == 200, r.text
    wait_for_index(client, admin)                      # the change re-indexes by itself
    assert notion_access(client, admin) == "everyone"
    assert found(ws, "public")
    assert knowledge(client, staff) == {"Launch plan": "public"}

    # ...and closing it again takes it away from public users again.
    client.put("/api/integrations/notion/access", json={"access": "restricted"}, headers=admin)
    wait_for_index(client, admin)
    assert not found(ws, "public")
    assert knowledge(client, staff) == {}


@pytest.mark.parametrize("name, x_role, status", [
    ("staff", "hr", 403),          # a public member (the header can't raise them)
    ("admin", "public", 403),      # an admin viewing the app as a public user
    ("hr", "public", 403),
    ("hr", "hr", 200),             # HR manages integrations, so it sets this too
    ("admin", "hr", 200),
])
def test_only_hr_and_admins_set_who_can_ask(env, name, x_role, status):
    import integrations_store
    client, ws = env
    connect_notion(ws)
    r = client.put("/api/integrations/notion/access", json={"access": "everyone"},
                   headers=login(client, ws, name, x_role))
    assert r.status_code == status, r.text
    expected = "everyone" if status == 200 else "restricted"
    assert integrations_store.access_of(integrations_store.get(ws, "notion")) == expected


@pytest.mark.parametrize("provider, body, status", [
    ("notion", {"access": "public"}, 422),             # not a connection level
    ("google_drive", {"access": "everyone"}, 400),     # not connected
    ("intercom", {"access": "everyone"}, 404),        # not a live connector
])
def test_setting_who_can_ask_checks_its_input(env, provider, body, status):
    client, ws = env
    connect_notion(ws)
    r = client.put(f"/api/integrations/{provider}/access", json=body,
                   headers=login(client, ws, "admin"))
    assert r.status_code == status, r.text


def test_an_existing_connection_without_the_setting_is_restricted(env):
    import db
    import integrations_store
    client, ws = env
    connect_notion(ws)
    # Made before the setting existed: chosen folders, no access key; and an
    # older row whose settings column is empty.
    integrations_store.update(ws, "notion", settings={"folders": [{"id": "f1"}]})
    with db.connect() as conn:
        conn.execute("INSERT INTO integrations (workspace, provider, status) "
                     "VALUES (?, 'google_drive', 'connected')", (ws,))
        conn.execute("UPDATE integrations SET settings=NULL WHERE workspace=? "
                     "AND provider='google_drive'", (ws,))

    admin = login(client, ws, "admin")
    assert notion_access(client, admin) == "restricted"
    items = client.get("/api/integrations", headers=admin).json()["integrations"]
    assert next(i for i in items if i["id"] == "google_drive")["access"] == "restricted"
    assert integrations_store.chunk_access_levels(ws) == {"notion": "restricted",
                                                         "google_drive": "restricted"}
    # Setting it keeps the rest of the settings.
    client.put("/api/integrations/notion/access", json={"access": "everyone"}, headers=admin)
    assert integrations_store.get(ws, "notion")["settings"] == {"folders": [{"id": "f1"}],
                                                                "access": "everyone"}


def test_files_left_by_a_removed_connection_stay_restricted(env):
    import workspaces
    client, ws = env
    folder = workspaces.connector_dir(ws, "google_drive")   # no connection row at all
    os.makedirs(folder)
    with open(os.path.join(folder, "f1.txt"), "w", encoding="utf-8") as f:
        f.write(SECRET)
    with open(os.path.join(folder, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump({"f1.txt": {"title": "Old Drive file", "provider": "Google Drive"}}, f)
    admin = login(client, ws, "admin")
    assert reindex(client, admin)["summary"]["restricted"] > 0
    assert not found(ws, "public")
    assert knowledge(client, login(client, ws, "staff")) == {}


# ---- Outline wiki --------------------------------------------------------------
@pytest.fixture
def outline(monkeypatch):
    """The wiki configured on the server; records whether each agent gets it."""
    from kb import state
    from kb.services import agents
    built = {}
    monkeypatch.setenv("OUTLINE_API_TOKEN", "ol_api_test")
    monkeypatch.setattr(state, "agents", {})
    monkeypatch.setattr(agents, "build_agent",
                        lambda role, collection, outline, **kw: built.setdefault(role, outline))
    return built


@pytest.mark.parametrize("min_role, public_gets_it, hr_gets_it", [
    (None, True, True),            # default: today's behaviour, everyone
    ("public", True, True),
    ("hr", False, True),
    ("HR ", False, True),
    ("admin", False, False),       # not a level we can tell apart: fail closed
])
def test_outline_is_only_given_to_roles_from_outline_min_role(outline, monkeypatch,
                                                              min_role, public_gets_it,
                                                              hr_gets_it):
    from kb.services import agents
    if min_role is None:
        monkeypatch.delenv("OUTLINE_MIN_ROLE", raising=False)
    else:
        monkeypatch.setenv("OUTLINE_MIN_ROLE", min_role)
    agents.get_agent("default", "public")
    agents.get_agent("default", "hr")
    assert outline == {"public": public_gets_it, "hr": hr_gets_it}


def test_outline_status_follows_the_role(outline, monkeypatch):
    from fastapi.testclient import TestClient
    import api
    import auth
    import members_store
    monkeypatch.setenv("OUTLINE_MIN_ROLE", "hr")
    client = TestClient(api.app)
    members_store.set_password("wiki-reader@default.example", "Reader",
                               auth.hash_password("correct-horse"), "default", "admin")
    token = client.post("/api/auth/login", json={"username": "wiki-reader@default.example",
                                                 "password": "correct-horse"}).json()["token"]

    def live(x_role):
        headers = {"Authorization": f"Bearer {token}", "X-Role": x_role}
        items = client.get("/api/integrations", headers=headers).json()["integrations"]
        return next(i for i in items if i["id"] == "outline")["live_search"]

    assert live("hr") is True
    assert live("public") is False


def test_a_synced_outline_wiki_replaces_the_live_tool(outline):
    """With the server's wiki in .env AND a synced Outline connection, the
    workspace searches its synced copy: the live tool isn't offered."""
    import integrations_store
    from kb.services import agents
    assert agents.outline_on("default", "hr")
    integrations_store.connect("default", "outline", "Wiki", {"token": "ol_api_x"}, "admin")
    try:
        assert not agents.outline_on("default", "hr")
        assert not agents.outline_on("default", "public")
    finally:
        integrations_store.remove("default", "outline")
    assert agents.outline_on("default", "hr")


def test_outline_collections_limit_search_and_reading(monkeypatch):
    import agent
    calls = []

    def fake_request(endpoint, payload, timeout=15):
        calls.append((endpoint, payload))
        if endpoint == "documents.info":
            return {"data": {"id": payload["id"], "collectionId": payload["id"][:2]}}, None
        cid = payload.get("collectionId")
        return {"data": [
            {"ranking": 0.5 if cid == "c1" else 0.9, "document": {"id": f"in-{cid}",
                                                                   "collectionId": cid}},
            # A server that ignored the filter would send this: dropped here.
            {"ranking": 1.0, "document": {"id": "elsewhere", "collectionId": "c9"}},
        ]}, None

    monkeypatch.setattr(agent, "_outline_request", fake_request)
    monkeypatch.setenv("OUTLINE_COLLECTIONS", "c1, c2")
    data, err = agent._outline_post("documents.search", {"query": "x", "limit": 5})
    assert err is None
    assert [r["document"]["id"] for r in data["data"]] == ["in-c2", "in-c1"]
    assert [p.get("collectionId") for _, p in calls] == ["c1", "c2"]

    assert agent._outline_post("documents.info", {"id": "c1-doc"})[1] is None
    data, err = agent._outline_post("documents.info", {"id": "c9-doc"})
    assert data is None and "isn't in a wiki collection" in err

    monkeypatch.delenv("OUTLINE_COLLECTIONS")
    calls.clear()
    agent._outline_post("documents.search", {"query": "x", "limit": 5})
    assert calls == [("documents.search", {"query": "x", "limit": 5})]
