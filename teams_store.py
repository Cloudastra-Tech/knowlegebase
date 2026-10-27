"""
teams_store.py  —  the Microsoft Teams bot's rows in config/app.db (migration 0015).

INSTALLS (teams_installs, one per workspace)
  mode custom    the customer's own Azure Bot, registered in their Microsoft Entra
                 tenant (single tenant: Azure stopped creating new multi-tenant
                 bots after 31 July 2025). Its Microsoft App ID, its tenant and its
                 client secret are pasted in; the secret is kept SEALED
                 (secret_box). Its messaging endpoint carries the row's app_key, so
                 a request is checked against that bot's App ID before anything in
                 it is read. The tenant answered is the bot's own.
  mode central   the bot run by whoever hosts this server, set in the environment
                 (TEAMS_APP_ID, TEAMS_APP_PASSWORD, TEAMS_APP_TENANT), for an
                 existing multi-tenant registration. The admin names the Entra
                 tenant their Teams is in.
  A tenant is answered by one workspace at most, so an activity's tenant names
  the workspace it's for. Status: configured, then connected once Teams has
  sent the bot something from that tenant.

SETTINGS on the same row: what to do with people Teams knows but this workspace
doesn't (unknown_users: "refuse", the default, or answer them as "public").

ACTIVITIES (teams_activities): the ids already taken in, so one Teams sends
again is answered once. Kept a day.

USERS (teams_users): a Teams user the bot has seen in a tenant: their 29:... id,
the service URL to reach them, and the bot's 1:1 chat with them once there is
one (answers the channel mustn't see go there).

THREADS (teams_threads): the conversation a member's Teams chat or channel
thread continues, so a follow-up is answered with what was said before.
"""

import secrets
import sqlite3
import threading
import time

import db
import secret_box

_lock = threading.Lock()

MODES = ("central", "custom")
UNKNOWN_USERS = ("refuse", "public")
ACTIVITIES_KEPT = 24 * 60 * 60
# The bot's client secret is sealed under the workspace's own data key, so
# secret_box.rotate() / reseal() reach it and deleting the workspace shreds it.
secret_box.register("teams_installs", "app_secret", ("workspace",))
_PUBLIC = ("workspace", "mode", "app_key", "app_id", "app_tenant", "tenant_id", "status",
           "unknown_users", "last_activity_at", "updated_by", "updated_at")


def _db():
    return db.connect()


def _row(r):
    """An install as callers see it: no secret."""
    if not r:
        return None
    d = {k: r[k] for k in _PUBLIC}
    d["has_secret"] = r["app_secret"] is not None
    return d


def get(workspace):
    with _lock, _db() as conn:
        return _row(conn.execute("SELECT * FROM teams_installs WHERE workspace = ?",
                                 (workspace,)).fetchone())


def by_tenant(tenant_id):
    """The install answering an Entra tenant, or None."""
    if not tenant_id:
        return None
    with _lock, _db() as conn:
        return _row(conn.execute("SELECT * FROM teams_installs WHERE tenant_id = ?",
                                 (tenant_id.lower(),)).fetchone())


def by_app_key(app_key):
    """The customer's own bot whose messaging endpoint carries this key, or None."""
    if not app_key:
        return None
    with _lock, _db() as conn:
        return _row(conn.execute("SELECT * FROM teams_installs WHERE app_key = ? AND "
                                 "mode = 'custom'", (app_key,)).fetchone())


class TenantTaken(Exception):
    """This Entra tenant is already answered by another workspace."""


def _ensure(conn, workspace):
    conn.execute("INSERT OR IGNORE INTO teams_installs (workspace, updated_at) VALUES (?, ?)",
                 (workspace, time.time()))


def _claim(conn, workspace, tenant_id):
    other = conn.execute("SELECT workspace FROM teams_installs WHERE tenant_id = ? AND "
                         "workspace != ?", (tenant_id, workspace)).fetchone()
    if other:
        raise TenantTaken(tenant_id)


def set_custom_app(workspace, app_id, app_tenant, client_secret, user):
    """Use the customer's own bot (single tenant): it answers its own tenant. A
    different bot (another App ID) starts over: not connected until Teams sends
    it something. Raises TenantTaken."""
    app_tenant = app_tenant.lower()
    sealed = secret_box.seal({"client_secret": client_secret}, workspace)
    with _lock, _db() as conn:
        _claim(conn, workspace, app_tenant)
        _ensure(conn, workspace)
        row = conn.execute("SELECT * FROM teams_installs WHERE workspace = ?",
                           (workspace,)).fetchone()
        same = row["mode"] == "custom" and row["app_id"] == app_id
        try:
            conn.execute(
                "UPDATE teams_installs SET mode = 'custom', app_id = ?, app_tenant = ?, "
                "app_secret = ?, tenant_id = ?, app_key = COALESCE(app_key, ?), "
                "status = CASE WHEN ? THEN status ELSE 'configured' END, "
                "updated_by = ?, updated_at = ? WHERE workspace = ?",
                (app_id, app_tenant, sealed,
                 app_tenant, secrets.token_urlsafe(18), int(same), user, time.time(),
                 workspace))
        except sqlite3.IntegrityError:          # the same tenant, claimed a moment ago
            raise TenantTaken(app_tenant)


def use_central_app(workspace, tenant_id, user):
    """Use the bot in the environment, answering this tenant. A custom bot's
    secret is dropped. Raises TenantTaken."""
    tenant_id = tenant_id.lower()
    with _lock, _db() as conn:
        _claim(conn, workspace, tenant_id)
        _ensure(conn, workspace)
        row = conn.execute("SELECT mode, tenant_id FROM teams_installs WHERE workspace = ?",
                           (workspace,)).fetchone()
        same = row["mode"] == "central" and row["tenant_id"] == tenant_id
        try:
            conn.execute(
                "UPDATE teams_installs SET mode = 'central', app_id = NULL, app_tenant = NULL, "
                "app_secret = NULL, app_key = NULL, tenant_id = ?, "
                "status = CASE WHEN ? THEN status ELSE 'configured' END, "
                "updated_by = ?, updated_at = ? WHERE workspace = ?",
                (tenant_id, int(same), user, time.time(), workspace))
        except sqlite3.IntegrityError:
            raise TenantTaken(tenant_id)


def app_secret(workspace):
    """A custom bot's client secret, or None."""
    with _lock, _db() as conn:
        r = conn.execute("SELECT app_secret FROM teams_installs WHERE workspace = ?",
                         (workspace,)).fetchone()
    if not (r and r["app_secret"]):
        return None
    return secret_box.unseal(r["app_secret"], workspace)["client_secret"]


def set_settings(workspace, user, unknown_users):
    if unknown_users not in UNKNOWN_USERS:
        raise ValueError("unknown_users")
    with _lock, _db() as conn:
        _ensure(conn, workspace)
        conn.execute("UPDATE teams_installs SET unknown_users = ?, updated_by = ?, "
                     "updated_at = ? WHERE workspace = ?",
                     (unknown_users, user, time.time(), workspace))


def remove(workspace):
    """Disconnect: the bot, its secret, the tenant and the settings are forgotten,
    with the users, threads and activities seen in that tenant (rows that have
    no workspace column of their own). Returns the install as it was, or None."""
    with _lock, _db() as conn:
        r = conn.execute("SELECT * FROM teams_installs WHERE workspace = ?",
                         (workspace,)).fetchone()
        conn.execute("DELETE FROM teams_installs WHERE workspace = ?", (workspace,))
        if r and r["tenant_id"]:
            conn.execute("DELETE FROM teams_users WHERE tenant_id = ?", (r["tenant_id"],))
            conn.execute("DELETE FROM teams_threads WHERE tenant_id = ?", (r["tenant_id"],))
            conn.execute("DELETE FROM teams_activities WHERE tenant_id = ?", (r["tenant_id"],))
    return _row(r)


def seen(workspace):
    """Teams sent the bot something for this workspace: it's connected."""
    with _lock, _db() as conn:
        conn.execute("UPDATE teams_installs SET status = 'connected', last_activity_at = ? "
                     "WHERE workspace = ?", (time.time(), workspace))


# ---- Activities taken in --------------------------------------------------------------
def first_time(key, tenant_id=None):
    """True the first time an activity is seen (then it's remembered); False when
    Teams sends it again. Older than ACTIVITIES_KEPT is forgotten as it goes."""
    now = time.time()
    with _lock, _db() as conn:
        if secrets.randbelow(50) == 0:
            conn.execute("DELETE FROM teams_activities WHERE received_at < ?",
                         (now - ACTIVITIES_KEPT,))
        return conn.execute("INSERT OR IGNORE INTO teams_activities (activity_id, tenant_id, "
                            "received_at) VALUES (?, ?, ?)", (key, tenant_id, now)).rowcount == 1


# ---- Users ------------------------------------------------------------------------------
def remember_user(tenant_id, aad_object_id, user_id, service_url, personal_conversation=None):
    """Note how to reach a Teams user (the 1:1 chat is kept once known)."""
    with _lock, _db() as conn:
        conn.execute(
            "INSERT INTO teams_users (tenant_id, aad_object_id, user_id, service_url, "
            "personal_conversation, updated_at) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (tenant_id, aad_object_id) DO UPDATE SET user_id = excluded.user_id, "
            "service_url = excluded.service_url, personal_conversation = "
            "COALESCE(excluded.personal_conversation, personal_conversation), "
            "updated_at = excluded.updated_at",
            (tenant_id, aad_object_id, user_id, service_url, personal_conversation, time.time()))


def user(tenant_id, aad_object_id):
    with _lock, _db() as conn:
        r = conn.execute("SELECT * FROM teams_users WHERE tenant_id = ? AND aad_object_id = ?",
                         (tenant_id, aad_object_id)).fetchone()
    return dict(r) if r else None


# ---- Threads ----------------------------------------------------------------------------
def thread_conversation(tenant_id, conversation, aad_object_id, idle=None):
    """The conversation a Teams chat or thread continues; None if there is none,
    or (idle: seconds) its last question is older than that."""
    with _lock, _db() as conn:
        r = conn.execute("SELECT conversation_id, updated_at FROM teams_threads WHERE "
                         "tenant_id = ? AND conversation = ? AND aad_object_id = ?",
                         (tenant_id, conversation, aad_object_id)).fetchone()
    if not r or (idle and r["updated_at"] < time.time() - idle):
        return None
    return r["conversation_id"]


def remember_thread(tenant_id, conversation, aad_object_id, conversation_id):
    with _lock, _db() as conn:
        conn.execute("INSERT OR REPLACE INTO teams_threads (tenant_id, conversation, "
                     "aad_object_id, conversation_id, updated_at) VALUES (?, ?, ?, ?, ?)",
                     (tenant_id, conversation, aad_object_id, conversation_id, time.time()))
