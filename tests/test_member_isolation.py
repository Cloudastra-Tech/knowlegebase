"""
F7: every change to a member is scoped to a workspace, in the store and through
the API, so an admin of one workspace can never touch another's members, and a
username taken in one workspace can't be claimed (or moved) from another.
"""

import uuid

import pytest

import members_store

PASSWORD = "correct-horse"


def two_workspaces():
    import auth
    a, b = (f"iso-{x}-{uuid.uuid4().hex[:6]}" for x in "ab")
    for ws in (a, b):
        for name, role in (("admin", "admin"), ("staff", "public")):
            members_store.set_password(f"{name}@{ws}.example", name, auth.hash_password(PASSWORD),
                                       ws, role)
    return a, b


def test_the_store_changes_a_member_only_in_their_own_workspace():
    a, b = two_workspaces()
    theirs = f"staff@{b}.example"
    before = members_store.get(theirs)
    assert members_store.set_disabled(theirs, True, a) is False
    assert members_store.remove(theirs, a) is False
    with pytest.raises(members_store.WrongWorkspace):
        members_store.set_role(theirs, "admin", a)
    with pytest.raises(members_store.WrongWorkspace):
        members_store.set_password(theirs, "Mallory", "pbkdf2$1$aa$bb", a, "admin")
    assert members_store.get(theirs) == before                      # nothing changed, or moved
    # In their own workspace each still works.
    assert members_store.set_disabled(theirs, True, b) is True
    members_store.set_role(theirs, "hr", b)
    assert members_store.get(theirs)["role"] == "hr"
    assert members_store.remove(theirs, b) is True


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    import api
    return TestClient(api.app, follow_redirects=False)


def login(client, username):
    r = client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}", "X-Role": "hr"}


def test_an_admin_of_one_workspace_cant_touch_anothers_members(client):
    a, b = two_workspaces()
    admin_a = login(client, f"admin@{a}.example")
    theirs = f"staff@{b}.example"
    their_session = login(client, theirs)
    before = members_store.get(theirs)
    for method, path, body in (
            ("PATCH", f"/api/admin/members/{theirs}", {"role": "admin"}),
            ("PATCH", f"/api/admin/members/{theirs}", {"disabled": True}),
            ("POST", f"/api/admin/members/{theirs}/sign-out", None),
            ("DELETE", f"/api/admin/members/{theirs}", None),
            ("PATCH", f"/api/admin/members/admin@{b}.example", {"role": "public"})):
        r = client.request(method, path, headers=admin_a, json=body)
        assert r.status_code == 404, (method, path, r.text)
    assert members_store.get(theirs) == before
    assert client.get("/api/auth/me", headers=their_session).status_code == 200  # still in
    listed = client.get("/api/admin/members", headers=admin_a).json()["members"]
    assert {m["username"] for m in listed} == {f"admin@{a}.example", f"staff@{a}.example"}


def test_the_cli_never_moves_an_account_between_workspaces(monkeypatch, capsys):
    import auth
    a, b = two_workspaces()
    monkeypatch.setattr("getpass.getpass", lambda prompt="": "long-enough-pw")
    with pytest.raises(SystemExit) as e:
        auth._cli(["add", f"staff@{b}.example", "--workspace", a])
    assert "belongs to the workspace" in str(e.value)
    with pytest.raises(SystemExit):
        auth._cli(["role", f"staff@{b}.example", "admin", "--workspace", a])
    assert members_store.get(f"staff@{b}.example")["workspace"] == b
    assert members_store.get(f"staff@{b}.example")["role"] == "public"
    # Without --workspace, a change applies where the account is (as it always did).
    auth._cli(["role", f"staff@{b}.example", "hr"])
    auth._cli(["add", f"staff@{b}.example"])
    assert capsys.readouterr().out.splitlines() == [
        f"staff@{b}.example is now hr (workspace: {b}).",
        f"Updated staff@{b}.example (workspace: {b}, role: hr)."]
    with pytest.raises(SystemExit) as e:
        auth._cli(["disable", "nobody-at-all"])
    assert str(e.value) == "No account named nobody-at-all."
