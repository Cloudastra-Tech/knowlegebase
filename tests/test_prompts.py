"""
Saved prompts and templates (chat.md C22; prompts_store.py, kb/routers/prompts.py):
{{variables}} are found and filled in; a person's own prompts are theirs alone;
the workspace's are kept by its admins and seen by everyone in it, and no one
else's workspace.
"""

import uuid

import pytest

import auth
import members_store
import prompts_store

PASSWORD = "correct-horse"


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    import api
    return TestClient(api.app)


def workspace(client):
    ws = f"pr-{uuid.uuid4().hex[:8]}"
    out = {}
    for name, role in (("admin", "admin"), ("pat", "public"), ("sam", "public")):
        members_store.set_password(f"{name}@{ws}.example", name, auth.hash_password(PASSWORD),
                                   ws, role)
        token = client.post("/api/auth/login", json={"username": f"{name}@{ws}.example",
                                                      "password": PASSWORD}).json()["token"]
        out[name] = {"Authorization": f"Bearer {token}", "X-Role": "hr"}
    out["ws"] = ws
    return out


def test_variables_are_found_in_order_and_filled_in():
    body = "Summarise {{ policy }} for {{customer}} by {{start date}}; cc {{customer}}. {{1bad}}"
    assert prompts_store.variables(body) == ["policy", "customer", "start date"]
    filled = prompts_store.fill(body, {"policy": "the refund policy", "customer": "Acme\nLtd",
                                       "start date": "x" * 500})
    assert filled.startswith("Summarise the refund policy for Acme Ltd by " + "x" * 200 + ";")
    assert filled.endswith("cc Acme Ltd. {{1bad}}")
    assert prompts_store.fill("Hi {{name}}", {}) == "Hi {{name}}"      # no value: left as is


def test_personal_prompts_are_their_owners(client):
    h = workspace(client)
    r = client.post("/api/prompts", headers=h["pat"],
                    json={"title": "Onboard", "body": "Explain {{policy}} to a new joiner."})
    assert r.status_code == 200, r.text
    mine = r.json()
    assert mine["variables"] == ["policy"] and not mine["workspace"] and mine["editable"]
    assert [p["id"] for p in client.get("/api/prompts", headers=h["pat"]).json()["prompts"]] == [
        mine["id"]]
    assert client.get("/api/prompts", headers=h["sam"]).json()["prompts"] == []
    for call in (lambda: client.put(f"/api/prompts/{mine['id']}", headers=h["sam"],
                                    json={"title": "x", "body": "y"}),
                 lambda: client.delete(f"/api/prompts/{mine['id']}", headers=h["sam"]),
                 lambda: client.delete(f"/api/prompts/{mine['id']}", headers=h["admin"])):
        assert call().status_code == 404
    r = client.put(f"/api/prompts/{mine['id']}", headers=h["pat"],
                   json={"title": "Onboard", "body": "Compare {{a}} with {{b}}."})
    assert r.json()["variables"] == ["a", "b"]
    assert client.delete(f"/api/prompts/{mine['id']}", headers=h["pat"]).status_code == 200


def test_workspace_prompts_are_kept_by_admins(client):
    h = workspace(client)
    other = workspace(client)
    assert client.post("/api/prompts", headers=h["pat"],
                       json={"title": "T", "body": "B", "workspace": True}).status_code == 403
    as_public = {**h["admin"], "X-Role": "public"}            # an admin viewing as public
    assert client.post("/api/prompts", headers=as_public,
                       json={"title": "T", "body": "B", "workspace": True}).status_code == 403
    r = client.post("/api/prompts", headers=h["admin"],
                    json={"title": "Customer reply", "body": "Draft a reply to {{customer}}.",
                          "workspace": True})
    shared = r.json()
    assert shared["workspace"] and shared["variables"] == ["customer"]
    seen = client.get("/api/prompts", headers=h["pat"]).json()
    assert [(p["id"], p["editable"]) for p in seen["prompts"]] == [(shared["id"], False)]
    assert not seen["can_manage_workspace"]
    assert client.put(f"/api/prompts/{shared['id']}", headers=h["pat"],
                      json={"title": "x", "body": "y"}).status_code == 403
    assert client.get("/api/prompts", headers=other["pat"]).json()["prompts"] == []
    assert client.delete(f"/api/prompts/{shared['id']}", headers=other["admin"]).status_code == 404
    assert client.delete(f"/api/prompts/{shared['id']}", headers=h["admin"]).status_code == 200


def test_a_prompt_can_be_set_to_answer_without_searching(client):
    h = workspace(client)
    plain = client.post("/api/prompts", headers=h["pat"],
                        json={"title": "Ask", "body": "Explain {{policy}}."}).json()
    assert plain["search"] is True                              # as every prompt before
    direct = client.post("/api/prompts", headers=h["pat"],
                         json={"title": "Echo", "body": "Just reply with OK.",
                               "search": False}).json()
    assert direct["search"] is False
    # Edited without saying: left as it was. Said: changed.
    r = client.put(f"/api/prompts/{direct['id']}", headers=h["pat"],
                   json={"title": "Echo", "body": "Just reply with pong."})
    assert r.json()["search"] is False and r.json()["body"] == "Just reply with pong."
    r = client.put(f"/api/prompts/{direct['id']}", headers=h["pat"],
                   json={"title": "Echo", "body": "Just reply with pong.", "search": True})
    assert r.json()["search"] is True
    client.put(f"/api/prompts/{direct['id']}", headers=h["pat"],
               json={"title": "Echo", "body": "Just reply with pong.", "search": False})
    # The chat asks the store: only for a prompt the asker may use is the search off.
    ws = h["ws"]
    pat, sam = f"pat@{ws}.example", f"sam@{ws}.example"
    assert prompts_store.searches(ws, pat, direct["id"]) is False
    assert prompts_store.searches(ws, pat, plain["id"]) is True
    assert prompts_store.searches(ws, sam, direct["id"]) is True          # not sam's
    assert prompts_store.searches(ws, pat, None) is True
    assert prompts_store.searches("elsewhere", pat, direct["id"]) is True


@pytest.mark.parametrize("body, problem", [
    ({"title": " ", "body": "x"}, "name"),
    ({"title": "T", "body": ""}, "write"),
    ({"title": "T", "body": "x" * 2001}, "2000"),
    ({"title": "T", "body": " ".join(f"{{{{v{n}}}}}" for n in range(11))}, "variables"),
])
def test_a_prompt_must_be_sensible(client, body, problem):
    h = workspace(client)
    r = client.post("/api/prompts", headers=h["pat"], json=body)
    assert r.status_code == 400 and problem in r.json()["detail"]
