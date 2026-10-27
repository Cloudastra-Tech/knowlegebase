"""
lifecycle_store.py  —  where a workspace is in its life, and who else may look in.

On its `workspaces` row (migration 0013):

    active      the usual: no suspended_at, no deleting_at
    suspended   an operator suspended it (PL-18): nobody signs in, and no API
                key, SCIM token or Slack request is taken
    deleting    an admin asked for it to be deleted (PL-06): it is purged at
                deleting_at (GRACE_DAYS later) unless cancelled first. Until then
                only its admins sign in, to export or cancel; members can't.

SUPPORT ACCESS: an admin lets Cloudastra support in until support_until
("Allow support for 72 hours"). Only then may an operator open the workspace as
support (kb/routers/operator.py), and every request they make is in its audit log.

EXPORTS (workspace_exports): an admin's export, made by a job into a ZIP under
EXPORT_ROOT/<workspace>/ and downloadable for EXPORT_HOURS.

AUDIT RETENTION (audit_days): NULL keeps the audit log for good (the default);
otherwise events older than that many days (at least AUDIT_MIN_DAYS) are deleted.
"""

import json
import os
import time
import uuid

import db

GRACE_DAYS = 7
SUPPORT_HOURS = (24, 72, 168)       # what an admin may grant
EXPORT_HOURS = 24
EXPORT_ROOT = os.getenv("EXPORT_DIR", os.path.join("data", "exports"))
AUDIT_MIN_DAYS = 365
AUDIT_DAY_CHOICES = (365, 730, 1095, 2555)       # 1, 2, 3 and 7 years; None: for good

ACTIVE, SUSPENDED, DELETING = "active", "suspended", "deleting"


def grace_days():
    try:
        return max(1, int(os.getenv("WORKSPACE_DELETE_GRACE_DAYS", "")))
    except ValueError:
        return GRACE_DAYS


def _db():
    return db.connect()


def _ensure(conn, workspace):
    conn.execute("INSERT OR IGNORE INTO workspaces (slug, name, created_at) VALUES (?, ?, ?)",
                 (workspace, workspace, time.time()))


def state(workspace):
    """{"status", "suspended_at", "suspended_reason", "deleting_at",
    "delete_requested_at", "delete_requested_by", "support_until",
    "support_granted_by", "audit_days"} (an unknown workspace is active)."""
    with _db() as conn:
        row = conn.execute("SELECT * FROM workspaces WHERE slug = ?", (workspace,)).fetchone()
    keys = ("suspended_at", "suspended_reason", "suspended_by", "deleting_at",
            "delete_requested_at", "delete_requested_by", "support_until", "support_granted_by",
            "audit_days")
    out = {k: (row[k] if row else None) for k in keys}
    if out["support_until"] and out["support_until"] < time.time():
        out["support_until"] = out["support_granted_by"] = None
    out["status"] = (SUSPENDED if out["suspended_at"] else
                     DELETING if out["deleting_at"] else ACTIVE)
    return out


def status(workspace):
    return state(workspace)["status"]


def signs_in(workspace, role):
    """May someone with `role` use the workspace now? Suspended: nobody. Being
    deleted: its admins only (to export or cancel)."""
    s = status(workspace)
    return s == ACTIVE or (s == DELETING and role == "admin")


# ---- Suspend (an operator) -------------------------------------------------------------
def suspend(workspace, reason, by):
    with _db() as conn:
        _ensure(conn, workspace)
        conn.execute("UPDATE workspaces SET suspended_at = ?, suspended_reason = ?, "
                     "suspended_by = ? WHERE slug = ?", (time.time(), reason, by, workspace))


def unsuspend(workspace):
    with _db() as conn:
        return conn.execute("UPDATE workspaces SET suspended_at = NULL, suspended_reason = NULL, "
                            "suspended_by = NULL WHERE slug = ? AND suspended_at IS NOT NULL",
                            (workspace,)).rowcount > 0


# ---- Delete (an admin), with a grace period -------------------------------------------
def schedule_deletion(workspace, by, days=None, now=None):
    """Purge it `days` (grace_days()) from now. Returns when."""
    now = now or time.time()
    when = now + (days or grace_days()) * 86400
    with _db() as conn:
        _ensure(conn, workspace)
        conn.execute("UPDATE workspaces SET deleting_at = ?, delete_requested_at = ?, "
                     "delete_requested_by = ? WHERE slug = ?", (when, now, by, workspace))
    return when


def cancel_deletion(workspace):
    with _db() as conn:
        return conn.execute("UPDATE workspaces SET deleting_at = NULL, delete_requested_at = NULL, "
                            "delete_requested_by = NULL WHERE slug = ? AND deleting_at IS NOT NULL",
                            (workspace,)).rowcount > 0


def due_for_purge(now=None):
    with _db() as conn:
        return [r[0] for r in conn.execute("SELECT slug FROM workspaces WHERE deleting_at <= ?",
                                           (now or time.time(),))]


# ---- Support access ---------------------------------------------------------------------
def grant_support(workspace, hours, by):
    until = time.time() + hours * 3600
    with _db() as conn:
        _ensure(conn, workspace)
        conn.execute("UPDATE workspaces SET support_until = ?, support_granted_by = ? "
                     "WHERE slug = ?", (until, by, workspace))
    return until


def revoke_support(workspace):
    with _db() as conn:
        conn.execute("UPDATE workspaces SET support_until = NULL, support_granted_by = NULL "
                     "WHERE slug = ?", (workspace,))


def support_open(workspace, now=None):
    return bool((state(workspace)["support_until"] or 0) > (now or time.time()))


# ---- Audit retention --------------------------------------------------------------------
def set_audit_days(workspace, days):
    if days is not None and days not in AUDIT_DAY_CHOICES:
        raise ValueError(f"audit retention must be one of {AUDIT_DAY_CHOICES} days, or null")
    with _db() as conn:
        _ensure(conn, workspace)
        conn.execute("UPDATE workspaces SET audit_days = ? WHERE slug = ?", (days, workspace))


def audit_retention():
    """{workspace: days} of every workspace that doesn't keep its audit log for good."""
    with _db() as conn:
        return {r[0]: r[1] for r in conn.execute(
            "SELECT slug, audit_days FROM workspaces WHERE audit_days IS NOT NULL")}


# ---- Exports ---------------------------------------------------------------------------
def export_dir(workspace):
    return os.path.join(EXPORT_ROOT, workspace)


def _export(row):
    if not row:
        return None
    e = dict(row)
    e["counts"] = json.loads(e["counts"] or "{}")
    if e["status"] == "done" and (e["expires_at"] or 0) < time.time():
        e["status"] = "expired"
    return e


def new_export(workspace, by):
    eid = str(uuid.uuid4())
    with _db() as conn:
        conn.execute("INSERT INTO workspace_exports (id, workspace, requested_by, created_at) "
                     "VALUES (?, ?, ?, ?)", (eid, workspace, by, time.time()))
    return get_export(workspace, eid)


def get_export(workspace, eid):
    with _db() as conn:
        return _export(conn.execute("SELECT * FROM workspace_exports WHERE id = ? AND "
                                    "workspace = ?", (eid, workspace)).fetchone())


def exports(workspace, limit=5):
    with _db() as conn:
        return [_export(r) for r in conn.execute(
            "SELECT * FROM workspace_exports WHERE workspace = ? ORDER BY created_at DESC "
            "LIMIT ?", (workspace, limit))]


def update_export(eid, **fields):
    if "counts" in fields:
        fields["counts"] = json.dumps(fields["counts"])
    cols = ", ".join(f"{k} = ?" for k in fields)
    with _db() as conn:
        conn.execute(f"UPDATE workspace_exports SET {cols} WHERE id = ?", (*fields.values(), eid))


def expired_exports(now=None):
    """Exports past their expiry (or failed a day ago): (workspace, id, file_name)."""
    now = now or time.time()
    with _db() as conn:
        return [tuple(r) for r in conn.execute(
            "SELECT workspace, id, file_name FROM workspace_exports WHERE expires_at < ? "
            "OR (status = 'failed' AND created_at < ?)", (now, now - 86400))]


def forget_export(eid):
    with _db() as conn:
        conn.execute("DELETE FROM workspace_exports WHERE id = ?", (eid,))
