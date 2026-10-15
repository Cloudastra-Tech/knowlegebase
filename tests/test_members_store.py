"""
members_store.py: copying the old config files in once, and the rules the
database keeps (a domain verified by one workspace, one workspace per account).
Each test gets its own empty database.
"""

import json

import pytest

import members_store


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(members_store, "DB_PATH", str(tmp_path / "app.db"))
    monkeypatch.setattr(members_store, "USERS_FILE", str(tmp_path / "users.json"))
    monkeypatch.setattr(members_store, "SSO_FILE", str(tmp_path / "sso.json"))
    monkeypatch.setattr(members_store, "_ready", False)

    def write(name, data):
        with open(tmp_path / name, "w") as f:
            f.write(data if isinstance(data, str) else json.dumps(data))

    def restart():                    # as if the server started again
        monkeypatch.setattr(members_store, "_ready", False)
    write.restart = restart
    return write


USERS = {"Swastik": {"name": "Swastik", "password": "pbkdf2$1$aa$bb", "role": "hr"},
         "bob": {"name": "Bob", "password": "pbkdf2$1$cc$dd", "workspace": "acme"}}
SSO = {"workspaces": {"default": {"domains": ["Cloudastra.in"], "hr": ["Priya@cloudastra.in"]},
                      "acme": {"domains": ["acme.com", "cloudastra.in"]},
                      "Bad Slug": {"domains": ["bad.com"]}}}


def test_the_old_files_are_copied_in(store):
    store("users.json", USERS)
    store("sso.json", SSO)
    swastik, bob, priya = (members_store.get(u) for u in
                           ("swastik", "bob", "priya@cloudastra.in"))
    assert (swastik["workspace"], swastik["role"], swastik["password_hash"]) == (
        "default", "hr", "pbkdf2$1$aa$bb")
    assert (bob["workspace"], bob["role"]) == ("acme", "public")
    assert (priya["workspace"], priya["role"], priya["password_hash"]) == ("default", "hr", None)
    assert members_store.workspace_for_domain("cloudastra.in") == "default"
    assert members_store.workspace_for_domain("acme.com") == "acme"
    assert members_store.workspace_for_domain("bad.com") is None


def test_a_domain_listed_twice_stays_with_the_first_workspace(store):
    store("sso.json", SSO)
    domains = {(d["workspace"], d["domain"]): d["verified_at"] for d in members_store.list_domains()}
    assert domains[("default", "cloudastra.in")] and not domains[("acme", "cloudastra.in")]


def test_the_files_are_copied_only_once(store):
    store("users.json", USERS)
    assert members_store.get("bob")
    members_store.remove("bob")                     # changed in the database since
    store("users.json", {**USERS, "carol": {"name": "Carol", "password": "x"}})
    store.restart()
    assert members_store.get("bob") is None and members_store.get("carol") is None


def test_a_broken_file_is_copied_once_it_is_fixed(store):
    store("sso.json", "{ not json")
    assert members_store.workspace_for_domain("cloudastra.in") is None
    store("sso.json", SSO)
    store.restart()
    assert members_store.workspace_for_domain("cloudastra.in") == "default"


def test_a_verified_domain_belongs_to_one_workspace(store):
    members_store.add_verified_domain("acme", "acme.com")
    members_store.add_verified_domain("acme", "acme.com")          # again: no change
    with pytest.raises(ValueError, match="already belongs to the workspace acme"):
        members_store.add_verified_domain("other", "ACME.com")
    assert members_store.remove_domain("acme.com")
    members_store.add_verified_domain("other", "acme.com")         # free again
    assert members_store.workspace_for_domain("acme.com") == "other"


def test_single_sign_on_never_moves_an_account_between_workspaces(store):
    members_store.set_role("pat@acme.com", "hr", workspace="acme")
    assert members_store.record_sso_login("pat@acme.com", "Pat", "other") is None
    assert members_store.get("pat@acme.com")["workspace"] == "acme"
    assert members_store.record_sso_login("pat@acme.com", "Pat", "acme")["role"] == "hr"


def test_the_login_page_asks_for_an_email_once_any_provider_is_on(store):
    assert not members_store.any_connections()
    conn = members_store.create_connection("acme", "Acme", "https://idp.acme.com", "kb", "s", {},
                                           "test")
    assert members_store.any_connections()
    members_store.update_connection("acme", conn["id"], enabled=False)
    assert not members_store.any_connections()


def test_roles_are_checked(store):
    with pytest.raises(ValueError):
        members_store.set_role("pat@acme.com", "owner")
