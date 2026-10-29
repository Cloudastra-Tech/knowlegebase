"""
health.py  —  is this server READY to serve? (GET /api/health/ready)

GET /api/health only says the process is up (Docker's HEALTHCHECK uses it, so
it must stay cheap and never fail because of a store). This deeper check is for
a deploy gate or an uptime monitor: it touches each thing a request needs.

    database           config/app.db opens read-only and answers a query (on
                       Postgres, DATABASE_URL answers one)
    search_index       the Chroma folder exists and its store opens (pgvector:
                       its table answers)
    integrations_key   the encryption key loads, and opens a workspace's data key
                       (or an old-format stored secret): a changed
                       INTEGRATIONS_KEY breaks every SSO provider and
                       integration while everything else looks fine
    toxicity_guard     only with REQUIRE_TOXICITY_GUARD=1: a toxicity check is
                       loaded (safety.guards_status()). None exists since the
                       Guardrails Hub validator was removed, so this fails

Nothing here creates or changes anything: a missing database or index is
reported, not made. Reasons are short and safe to show to anyone (the endpoint
needs no sign-in); the full error goes to the server log.
"""

import logging
import os
import pathlib
import sqlite3

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

import db
import secret_box

log = logging.getLogger("uvicorn.error")

# Tables holding values sealed by the server key itself (secret_box.py's old
# format); a wrapped data key (workspace_keys) is tried first.
_SEALED = (("sso_connections", "secret"), ("integrations", "secret"))


class NotReady(Exception):
    """A check failed; the message is the reason shown to the caller."""


def _read_only(db_path):
    if db.is_postgres():               # a pooled connection; nothing here writes
        import db_pg
        return db_pg.database(db.DATABASE_URL).connect()
    if not os.path.isfile(db_path):
        raise NotReady("no database file")
    uri = pathlib.Path(os.path.abspath(db_path)).as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=2)
    conn.execute("PRAGMA query_only = ON")
    return conn


def check_database(db_path):
    conn = _read_only(db_path)
    try:
        conn.execute("SELECT 1" if db.is_postgres() else
                     "SELECT count(*) FROM sqlite_master").fetchone()
    finally:
        conn.close()


def check_search_index(folder):
    import vector_store
    if vector_store.backend() == "pgvector":
        try:
            vector_store.check(folder)       # the table answers (VECTOR_STORE=pgvector)
        except vector_store.NotFoundError as e:
            raise NotReady(str(e)) from None
        return
    if not os.path.isdir(folder):
        raise NotReady("no search index folder (run ingest.py)")
    # Same default settings as GET /api/knowledge, so Chroma reuses one client per folder.
    vector_store.check(folder)


def _stored_secret(db_path):
    """(kind, blob): a wrapped data key ("wrapped"), else any one value sealed by
    the server key itself ("legacy"), else (None, None)."""
    if not db.is_postgres() and not os.path.isfile(db_path):
        return None, None
    conn = _read_only(db_path)
    try:
        try:
            row = conn.execute("SELECT wrapped_dek FROM workspace_keys LIMIT 1").fetchone()
        except sqlite3.OperationalError:           # table not made yet
            row = None
        if row:
            return "wrapped", row[0]
        for table, column in _SEALED:
            try:
                row = conn.execute(f"SELECT {column} FROM {table} WHERE {column} IS NOT NULL "
                                   f"AND substr({column}, 1, {len(secret_box.PREFIX)}) != ? "
                                   "LIMIT 1", (secret_box.PREFIX,)).fetchone()
            except sqlite3.OperationalError:       # table not made yet
                continue
            if row:
                return "legacy", row[0]
        return None, None
    finally:
        conn.close()


def check_integrations_key(db_path, key_path=None):
    kind, blob = _stored_secret(db_path)
    if os.getenv("KEK_PROVIDER", "env").strip().lower() == "kms":
        if kind == "wrapped":
            try:
                secret_box.kek().unwrap(blob)
            except Exception as e:
                raise NotReady(f"the KMS can't open the data keys ({type(e).__name__})") from None
        return
    key_path = key_path or secret_box.KEY_PATH
    key = os.getenv("INTEGRATIONS_KEY")
    if not key and secret_box.hosted():
        raise NotReady("hosted mode needs INTEGRATIONS_KEY")
    if not key and os.path.isfile(key_path):
        with open(key_path, "rb") as f:
            key = f.read().strip().decode()
    if not key:
        if blob:
            raise NotReady("secrets are stored but no encryption key is set")
        return                       # nothing encrypted yet; a key is made on first use
    try:
        fernet = MultiFernet([Fernet(k.strip()) for k in key.split(",") if k.strip()])
    except ValueError:
        raise NotReady("the encryption key is not a valid key") from None
    if blob:
        try:
            fernet.decrypt(blob)
        except InvalidToken:
            raise NotReady("the encryption key doesn't decrypt the stored secrets") from None


def check_toxicity_guard(guards):
    if guards["toxicity"] != "active":
        raise NotReady("REQUIRE_TOXICITY_GUARD is set but the toxicity guard is not installed")


def ready(db_path, index_folder, key_path=None, guards=None):
    """(all ok?, {check: "ok" or the reason it failed}). `guards` is
    safety.guards_status(); a missing toxicity guard only fails when it is required."""
    checks = {"database": lambda: check_database(db_path),
              "search_index": lambda: check_search_index(index_folder),
              "integrations_key": lambda: check_integrations_key(db_path, key_path)}
    if guards and guards["toxicity_required"]:
        checks["toxicity_guard"] = lambda: check_toxicity_guard(guards)
    out = {}
    for name, check in checks.items():
        try:
            check()
            out[name] = "ok"
        except NotReady as e:
            log.warning("Not ready: %s: %s", name, e)
            out[name] = str(e)
        except Exception as e:
            log.warning("Not ready: %s", name, exc_info=True)
            out[name] = f"failed ({type(e).__name__})"
    return all(v == "ok" for v in out.values()), out
