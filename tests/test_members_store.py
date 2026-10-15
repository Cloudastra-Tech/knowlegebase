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
    conn = members_store.create_connection("acme", "oidc", "Acme", "https://idp.acme.com", "kb",
                                           {"client_secret": "s"}, {}, "test")
    assert members_store.any_connections()
    members_store.update_connection("acme", conn["id"], enabled=False)
    assert not members_store.any_connections()


def test_roles_are_checked(store):
    with pytest.raises(ValueError):
        members_store.set_role("pat@acme.com", "owner")


def test_an_older_database_gains_the_new_columns_and_keeps_roles(store, tmp_path):
    """A config/app.db from before group roles: roles set by hand become overrides."""
    import sqlite3
    with sqlite3.connect(tmp_path / "app.db") as db:
        db.executescript("""
            CREATE TABLE workspaces (slug TEXT PRIMARY KEY, name TEXT NOT NULL,
                                     created_at REAL NOT NULL);
            CREATE TABLE members (username TEXT PRIMARY KEY, workspace TEXT NOT NULL,
                name TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'public',
                password_hash TEXT, created_at REAL NOT NULL, last_login_at REAL);
            INSERT INTO workspaces VALUES ('default', 'default', 0);
            INSERT INTO members VALUES ('boss', 'default', 'Boss', 'hr', 'x', 0, NULL);
            INSERT INTO members VALUES ('pat@acme.com', 'default', 'Pat', 'public', NULL, 0, NULL);
        """)
    boss, pat = members_store.get("boss"), members_store.get("pat@acme.com")
    assert (boss["role"], boss["override_role"], boss["disabled"]) == ("hr", "hr", False)
    assert (pat["role"], pat["override_role"]) == ("public", None)
    store.restart()                                  # and only once
    members_store.set_role("boss", None)
    store.restart()
    assert members_store.get("boss")["role"] == "public"


def test_a_role_comes_from_the_override_then_the_groups_then_public(store):
    rules = [{"group_name": "Engineering", "role": "hr"}, {"group_name": "IT", "role": "admin"}]
    groups = members_store.role_for_groups(rules, ["engineering", "it", "sales"])
    assert groups == "admin"                                  # the highest one
    assert members_store.role_for_groups(rules, ["sales"]) is None
    members_store.record_sso_login("pat@acme.com", "Pat", "acme", groups=(["eng"], "hr"))
    assert members_store.get("pat@acme.com")["role"] == "hr"
    members_store.set_role("pat@acme.com", "public")          # an admin overrides it
    members_store.record_sso_login("pat@acme.com", "Pat", "acme", groups=(["eng"], "hr"))
    assert members_store.get("pat@acme.com")["role"] == "public"
    members_store.set_role("pat@acme.com", None)              # back to the groups
    assert members_store.get("pat@acme.com")["role"] == "hr"
    members_store.record_sso_login("pat@acme.com", "Pat", "acme", groups=([], None))
    assert members_store.get("pat@acme.com")["role"] == "public"   # left the group
    members_store.record_sso_login("pat@acme.com", "Pat Lee", "acme")  # a provider without rules
    pat = members_store.get("pat@acme.com")
    assert (pat["groups"], pat["name"]) == ([], "Pat Lee")       # the provider's name wins
