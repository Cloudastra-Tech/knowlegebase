"""
slack_store.py  —  the Slack app's rows in config/app.db (migration 0011).

INSTALLS (slack_installs, one per workspace)
  mode central   the app run by whoever hosts this server, set in the environment
                 (SLACK_CLIENT_ID, SLACK_CLIENT_SECRET, SLACK_SIGNING_SECRET). It
                 has one set of request URLs for every workspace.
  mode custom    the customer's own internal app, made in their Slack from our
                 manifest (slack/manifest.yml): its client secret and signing
                 secret are pasted in and kept SEALED (secret_box). Its request
                 URLs carry the row's app_key, so a request is checked with that
                 app's signing secret before anything in it is read.
  The bot token from the install is sealed too, and never leaves this module
  except through bot_token(). A Slack team (team_id) is installed in one
  workspace at most, so an event's team names the workspace it's for.

SETTINGS on the same row: what to do with people Slack knows but this workspace
doesn't (unknown_users: "refuse", the default, or answer them as "public"), and
auto-answer in help channels (off by default).

EVENTS (slack_events): the ids of events, commands and clicks already taken in.
Slack sends an event again when we're slow to answer (X-Slack-Retry-Num), and
taking one twice would answer a question twice. Kept a day.

THREADS (slack_threads): the conversation a member's Slack thread (or DM)
continues, so a follow-up is answered with what was said before.
"""

import json
import secrets
import sqlite3
import threading
import time

import conversations_store
import db
import secret_box

_lock = threading.Lock()

MODES = ("central", "custom")
UNKNOWN_USERS = ("refuse", "public")
EVENTS_KEPT = 24 * 60 * 60
MAX_AUTO_CHANNELS = 20
_PUBLIC = ("workspace", "mode", "app_key", "client_id", "status", "team_id", "team_name",
           "app_id", "bot_user_id", "scopes", "unknown_users", "auto_answer",
           "auto_answer_channels", "installed_by", "installed_at", "updated_by", "updated_at")


def _db():
    return db.connect()


def _row(r):
    """An install as callers see it: no secrets."""
    if not r:
        return None
    d = {k: r[k] for k in _PUBLIC}
    d["auto_answer"] = bool(d["auto_answer"])
    d["auto_answer_channels"] = json.loads(d["auto_answer_channels"] or "[]")
    d["has_app_secrets"] = r["app_secrets"] is not None
    return d


def get(workspace):
    with _lock, _db() as conn:
        return _row(conn.execute("SELECT * FROM slack_installs WHERE workspace = ?",
                                 (workspace,)).fetchone())


def by_team(team_id):
    """The install for a Slack team (only an installed one), or None."""
    if not team_id:
        return None
    with _lock, _db() as conn:
        return _row(conn.execute("SELECT * FROM slack_installs WHERE team_id = ? AND "
                                 "status = 'installed'", (team_id,)).fetchone())


def by_app_key(app_key):
    """The custom app whose request URLs carry this key, or None."""
    if not app_key:
        return None
    with _lock, _db() as conn:
        return _row(conn.execute("SELECT * FROM slack_installs WHERE app_key = ? AND "
                                 "mode = 'custom'", (app_key,)).fetchone())


def _ensure(conn, workspace):
    conn.execute("INSERT OR IGNORE INTO slack_installs (workspace, updated_at) VALUES (?, ?)",
                 (workspace, time.time()))


def set_custom_app(workspace, client_id, client_secret, signing_secret, user):
    """Use the customer's own app. A new app (another client id) ends the install
    of the one before: its bot token belonged to that app."""
    sealed = secret_box.seal({"client_secret": client_secret,
                              "signing_secret": signing_secret}, workspace)
    with _lock, _db() as conn:
        _ensure(conn, workspace)
        row = conn.execute("SELECT * FROM slack_installs WHERE workspace = ?",
                           (workspace,)).fetchone()
        same = row["mode"] == "custom" and row["client_id"] == client_id
        conn.execute(
            "UPDATE slack_installs SET mode = 'custom', client_id = ?, app_secrets = ?, "
            "app_key = COALESCE(app_key, ?), updated_by = ?, updated_at = ? WHERE workspace = ?",
            (client_id, sealed, secrets.token_urlsafe(18), user, time.time(), workspace))
        if not same:
            _forget_install(conn, workspace)


def use_central_app(workspace, user):
    """Back to the app in the environment: the custom app's secrets are dropped,
    and its install with them."""
    with _lock, _db() as conn:
        _ensure(conn, workspace)
        was = conn.execute("SELECT mode FROM slack_installs WHERE workspace = ?",
                           (workspace,)).fetchone()["mode"]
        conn.execute("UPDATE slack_installs SET mode = 'central', client_id = NULL, "
                     "app_secrets = NULL, app_key = NULL, updated_by = ?, updated_at = ? "
                     "WHERE workspace = ?", (user, time.time(), workspace))
        if was != "central":
            _forget_install(conn, workspace)


def app_secrets(workspace):
    """{client_secret, signing_secret} of a custom app, or None."""
    with _lock, _db() as conn:
        r = conn.execute("SELECT app_secrets FROM slack_installs WHERE workspace = ?",
                         (workspace,)).fetchone()
    return secret_box.unseal(r["app_secrets"], workspace) if r and r["app_secrets"] else None


class TeamTaken(Exception):
    """This Slack team is already installed in another workspace."""


def installed(workspace, team_id, team_name, app_id, bot_user_id, bot_token, scopes, user):
    """Record a finished install (oauth.v2.access). Raises TeamTaken."""
    sealed = secret_box.seal(bot_token, workspace)
    with _lock, _db() as conn:
        _ensure(conn, workspace)
        other = conn.execute("SELECT workspace FROM slack_installs WHERE team_id = ? AND "
                             "workspace != ?", (team_id, workspace)).fetchone()
        if other:
            raise TeamTaken(team_id)
        try:
            conn.execute(
                "UPDATE slack_installs SET status = 'installed', team_id = ?, team_name = ?, "
                "app_id = ?, bot_user_id = ?, bot_token = ?, scopes = ?, installed_by = ?, "
                "installed_at = ?, updated_by = ?, updated_at = ? WHERE workspace = ?",
                (team_id, team_name, app_id, bot_user_id, sealed, scopes,
                 user, time.time(), user, time.time(), workspace))
        except sqlite3.IntegrityError:          # the same team, installed a moment ago
            raise TeamTaken(team_id)


def bot_token(workspace):
    with _lock, _db() as conn:
        r = conn.execute("SELECT bot_token FROM slack_installs WHERE workspace = ? AND "
                         "status = 'installed'", (workspace,)).fetchone()
    return secret_box.unseal(r["bot_token"], workspace) if r and r["bot_token"] else None


def _forget_install(conn, workspace, status="configured"):
    conn.execute("UPDATE slack_installs SET status = ?, team_id = NULL, team_name = NULL, "
                 "app_id = NULL, bot_user_id = NULL, bot_token = NULL, scopes = NULL "
                 "WHERE workspace = ?", (status, workspace))


def uninstalled(workspace, status="configured"):
    """The bot token is gone: removed here ("configured") or by Slack ("revoked").
    The app's settings and a custom app's secrets stay, to install again."""
    with _lock, _db() as conn:
        _forget_install(conn, workspace, status)


def remove(workspace):
    """Everything: the install, the custom app and the settings."""
    with _lock, _db() as conn:
        conn.execute("DELETE FROM slack_installs WHERE workspace = ?", (workspace,))


def set_settings(workspace, user, unknown_users=None, auto_answer=None,
                 auto_answer_channels=None):
    fields = {}
    if unknown_users is not None:
        if unknown_users not in UNKNOWN_USERS:
            raise ValueError("unknown_users")
        fields["unknown_users"] = unknown_users
    if auto_answer is not None:
        fields["auto_answer"] = int(bool(auto_answer))
    if auto_answer_channels is not None:
        fields["auto_answer_channels"] = json.dumps(list(dict.fromkeys(
            auto_answer_channels))[:MAX_AUTO_CHANNELS])
    fields.update(updated_by=user, updated_at=time.time())
    cols = ", ".join(f"{k} = ?" for k in fields)
    with _lock, _db() as conn:
        _ensure(conn, workspace)
        conn.execute(f"UPDATE slack_installs SET {cols} WHERE workspace = ?",
                     (*fields.values(), workspace))


# ---- Events taken in --------------------------------------------------------------------
def first_time(event_id, team_id=None):
    """True the first time an event id is seen (then it's remembered); False for a
    retry or a duplicate. Ids older than EVENTS_KEPT are forgotten as it goes."""
    now = time.time()
    with _lock, _db() as conn:
        if secrets.randbelow(50) == 0:
            conn.execute("DELETE FROM slack_events WHERE received_at < ?", (now - EVENTS_KEPT,))
        return conn.execute("INSERT OR IGNORE INTO slack_events (event_id, team_id, received_at) "
                            "VALUES (?, ?, ?)", (event_id, team_id, now)).rowcount == 1


# ---- Threads ------------------------------------------------------------------------------
def thread_conversation(team_id, channel, thread_ts, slack_user):
    with _lock, _db() as conn:
        r = conn.execute("SELECT conversation_id FROM slack_threads WHERE team_id = ? AND "
                         "channel = ? AND thread_ts = ? AND slack_user = ?",
                         (team_id, channel, thread_ts, slack_user)).fetchone()
    return r["conversation_id"] if r else None


def remember_thread(team_id, channel, thread_ts, slack_user, conversation_id):
    with _lock, _db() as conn:
        conn.execute("INSERT OR REPLACE INTO slack_threads (team_id, channel, thread_ts, "
                     "slack_user, conversation_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                     (team_id, channel, thread_ts, slack_user, conversation_id, time.time()))


# ---- Answers posted in Slack ----------------------------------------------------------------
def answer_message(workspace, message_id):
    """An answer in this workspace by its id, with its conversation's owner
    (anyone in a channel may rate it, not only the asker), or None."""
    with _lock, _db() as conn:
        r = conn.execute("SELECT c.owner FROM messages m JOIN conversations c "
                         "ON c.id = m.conversation_id WHERE m.id = ? AND c.workspace = ? "
                         "AND c.deleted_at IS NULL", (message_id, workspace)).fetchone()
    if not r:
        return None
    m = conversations_store.get_message(workspace, r["owner"], message_id)
    return {**m, "owner": r["owner"]} if m else None
