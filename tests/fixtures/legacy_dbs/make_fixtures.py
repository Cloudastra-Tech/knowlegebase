"""
make_fixtures.py  —  rebuild the pre-Alembic databases test_migrations.py upgrades.

Each <date>-<commit>.sql here is a config/app.db made by the stores AS THEY WERE at
that commit (their own CREATE TABLE / ALTER TABLE code, run from git history),
then filled with a few rows of every table that existed, and dumped as SQL. They
stand for the databases production may still have: one per schema change.

    venv/bin/python tests/fixtures/legacy_dbs/make_fixtures.py

Needs the git history (not a shallow clone). The .sql files are committed, so
the tests themselves never need git. Only rerun it to add a fixture.
"""

import json
import os
import pathlib
import sqlite3
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
STORES = ("workspaces.py", "secret_box.py", "integrations_store.py", "sessions_store.py",
          "members_store.py")
# The commit just after each change to the app.db schema (oldest first).
COMMITS = (
    ("2026-10-14", "89a4ad7"),   # before the SSO epic: sessions + integrations only
    ("2026-10-15", "6d72bac"),   # workspaces, domains and members in the database
    ("2026-10-15", "33ff439"),   # OIDC + SAML providers, before group roles
    ("2026-10-15", "8f41e58"),   # group roles, with an admin override
    ("2026-10-15", "b64ccab"),   # require SSO, sign out at the provider
    ("2026-10-26", "c63ff56"),   # sign-in failures get a reference
    ("2026-10-26", "8615fad"),   # one role source per workspace
    ("2026-10-26", "9d794f0"),   # SAML metadata refresh
    ("2026-10-26", "2989391"),   # single-use invite codes (= main 1f71dd1)
    ("2026-10-27", "c6b9f1a"),   # sessions say whose they are (= dev before Alembic)
)

# Opens every store once, the way the app at that commit did on its first request.
TOUCH = """
import importlib, sys
for name in ("integrations_store", "sessions_store", "members_store"):
    try:
        module = importlib.import_module(name)
    except ImportError:
        continue
    module._db().close()
"""

# One or more rows per table; only the columns a table has at that commit are used.
ROWS = {
    "workspaces": [
        {"slug": "default", "name": "Default", "created_at": 1.0},
        {"slug": "acme", "name": "Acme", "created_at": 2.0, "require_sso": 0},
    ],
    "workspace_domains": [
        {"workspace": "acme", "domain": "acme.com", "verify_token": "tok", "verified_at": 3.0},
        {"workspace": "default", "domain": "acme.com", "verify_token": None,
         "verified_at": None},
    ],
    "members": [
        # Made before group roles: a role set by hand, which becomes an override.
        {"username": "boss", "workspace": "default", "name": "Boss", "role": "hr",
         "password_hash": "pbkdf2$1$aa$bb", "created_at": 4.0, "last_login_at": 40.0},
        {"username": "pat@acme.com", "workspace": "acme", "name": "Pat", "role": "public",
         "password_hash": None, "created_at": 5.0, "last_login_at": 50.0,
         "groups": json.dumps(["eng"])},
        {"username": "admin@acme.com", "workspace": "acme", "name": "Ada", "role": "admin",
         "password_hash": None, "created_at": 6.0, "last_login_at": None,
         "override_role": "admin", "disabled": 0},
    ],
    "sso_connections": [
        {"id": "c1", "workspace": "acme", "protocol": "oidc", "label": "Okta",
         "issuer": "https://idp.example", "client_id": "kb", "secret": b"sealed-oidc",
         "metadata": "{}", "enabled": 1, "created_by": "admin@acme.com", "created_at": 7.0,
         "updated_at": 7.0, "last_login_at": 100.0},
        {"id": "c2", "workspace": "acme", "protocol": "saml", "label": "Entra",
         "issuer": "https://sts.example", "client_id": None, "secret": b"sealed-saml",
         "metadata": json.dumps({"sso_url": "https://sts.example/sso"}), "enabled": 1,
         "created_by": "admin@acme.com", "created_at": 8.0, "updated_at": 9.0,
         "last_login_at": 200.0},
    ],
    "saml_requests": [{"request_id": "r1", "connection_id": "c2", "expires": 9e9}],
    "saml_assertions": [{"assertion_id": "a1", "expires": 9e9}],
    "sso_events": [
        {"workspace": "acme", "connection_id": "c1", "email": "pat@acme.com",
         "outcome": "signed_in", "reason": None, "at": 10.0},
        {"workspace": "acme", "connection_id": "c2", "email": "eve@evil.example",
         "outcome": "refused", "reason": "domain_not_allowed", "at": 11.0},
    ],
    "group_role_rules": [{"connection_id": "c2", "group_name": "eng", "role": "hr"}],
    "signup_codes_used": [{"code_hash": "f" * 64, "workspace": "acme", "used_at": 12.0}],
    "sessions": [
        {"token_hash": "1" * 64, "expires": 9e9,
         "user": json.dumps({"username": "Pat@Acme.com", "name": "Pat", "workspace": "acme"})},
        {"token_hash": "2" * 64, "expires": 9e9,
         "user": json.dumps({"username": "boss", "name": "Boss", "workspace": "default"})},
    ],
    "integrations": [
        {"workspace": "acme", "provider": "google_drive", "status": "connected",
         "account": "it@acme.com", "secret": b"sealed-tokens",
         "settings": json.dumps({"folders": [{"id": "f1"}], "access": "everyone"}),
         "connected_by": "admin@acme.com", "connected_at": 13.0, "last_sync_at": 14.0,
         "last_error": None, "doc_count": 3},
    ],
}


def build(commit, out):
    with tempfile.TemporaryDirectory() as work:
        for name in STORES:
            source = subprocess.run(["git", "show", f"{commit}:{name}"], cwd=HERE,
                                    capture_output=True)
            if source.returncode == 0:
                pathlib.Path(work, name).write_bytes(source.stdout)
        db = os.path.join(work, "config", "app.db")
        env = dict(os.environ, APP_DB=db, USERS_FILE=os.path.join(work, "none.json"),
                   SSO_CONFIG=os.path.join(work, "none.json"),
                   INTEGRATIONS_KEY="x" * 43 + "=")
        subprocess.run([sys.executable, "-c", TOUCH], cwd=work, env=env, check=True)
        conn = sqlite3.connect(db)
        for table, rows in ROWS.items():
            have = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
            for row in rows if have else ():
                cols = [c for c in row if c in have]
                conn.execute(f"INSERT INTO {table} ({', '.join(cols)}) "
                             f"VALUES ({', '.join('?' for _ in cols)})", [row[c] for c in cols])
        conn.commit()
        out.write_text("\n".join(conn.iterdump()) + "\n")
        conn.close()


if __name__ == "__main__":
    for date, commit in COMMITS:
        path = HERE / f"{date}-{commit}.sql"
        build(commit, path)
        print("wrote", path.name)
