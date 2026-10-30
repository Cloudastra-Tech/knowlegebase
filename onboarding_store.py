"""
onboarding_store.py  —  a new workspace's first steps, and sign-ups still waiting
on DNS (config/app.db, see db.py; tables made by migrations/versions/0020_billing_onboarding.py).

    onboarding        when each onboarding step was first seen done; the steps
                      themselves are worked out from the data (kb/services/onboarding.py).
                      A member's product tour is kept here too: one row per tour and
                      outcome, named after them ("tour:members:finished:<username>").
                      They follow the member: a new username (scim_store._rename)
                      renames them, removing the member (members_store.remove)
                      deletes them, and the checklist never reads them
    pending_signups   a company signing up with email and a DNS TXT record: the
                      founder's address, the domain and the record's token. No
                      workspace exists until the record checks out

Only SQL lives here.
"""

import hashlib
import secrets
import time
import uuid

import db

SIGNUP_HOURS = 72                  # a pending sign-up lasts this long


def _db():
    return db.connect()


# ---- Onboarding steps -------------------------------------------------------------------
def done_steps(workspace):
    """{step: done_at} of the workspace's checklist (members' tours left out)."""
    with _db() as conn:
        return {r["step"]: r["done_at"] for r in conn.execute(
            f"SELECT step, done_at FROM onboarding WHERE workspace = ? AND step NOT LIKE "
            f"'{TOUR}%'", (workspace,))}


def mark_done(workspace, steps, now=None):
    if not steps:
        return
    now = now or time.time()
    with _db() as conn:
        conn.executemany("INSERT OR IGNORE INTO onboarding (workspace, step, done_at) "
                         "VALUES (?, ?, ?)", [(workspace, s, now) for s in steps])


def steps_in(workspace, steps):
    """{step: done_at} for those of `steps` that are done."""
    steps = list(steps)
    if not steps:
        return {}
    marks = ", ".join("?" for _ in steps)
    with _db() as conn:
        return {r["step"]: r["done_at"] for r in conn.execute(
            f"SELECT step, done_at FROM onboarding WHERE workspace = ? AND step IN ({marks})",
            (workspace, *steps))}


def forget(workspace, step):
    with _db() as conn:
        conn.execute("DELETE FROM onboarding WHERE workspace = ? AND step = ?", (workspace, step))


# ---- A member's product tour ------------------------------------------------------------
TOUR = "tour:"


def tour_step(tour, outcome, username):
    return f"{TOUR}{tour}:{outcome}:{username}"


def _tours_of(conn, workspace, username):
    """[(step, done_at)] of this member's tour rows, whichever tours they are."""
    rows = conn.execute(f"SELECT step, done_at FROM onboarding WHERE workspace = ? AND step "
                        f"LIKE '{TOUR}%'", (workspace,)).fetchall()
    return [(r["step"], r["done_at"]) for r in rows
            if r["step"].split(":", 3)[3:] == [username]]


def rename_tours(conn, workspace, old, new):
    """In the caller's transaction: the member's tour rows follow a new username."""
    for step, done_at in _tours_of(conn, workspace, old):
        _, tour, outcome, _ = step.split(":", 3)
        conn.execute("INSERT OR IGNORE INTO onboarding (workspace, step, done_at) "
                     "VALUES (?, ?, ?)", (workspace, tour_step(tour, outcome, new), done_at))
        conn.execute("DELETE FROM onboarding WHERE workspace = ? AND step = ?", (workspace, step))


def forget_tours(conn, workspace, username):
    """In the caller's transaction: delete the member's tour rows. Returns how many."""
    steps = [step for step, _ in _tours_of(conn, workspace, username)]
    for step in steps:
        conn.execute("DELETE FROM onboarding WHERE workspace = ? AND step = ?", (workspace, step))
    return len(steps)


# ---- Pending sign-ups -------------------------------------------------------------------
def _hash(token):
    return hashlib.sha256((token or "").encode()).hexdigest()


def new_signup(workspace_name, email, domain, code_hash=None):
    """A pending sign-up. Returns (row, the emailed link's token: shown once)."""
    link = secrets.token_urlsafe(24)
    now = time.time()
    sid = str(uuid.uuid4())
    with _db() as conn:
        conn.execute("""INSERT INTO pending_signups (id, workspace_name, email, domain,
                            verify_token, link_hash, code_hash, created_at, expires_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                     (sid, workspace_name, email, domain, secrets.token_urlsafe(18),
                      _hash(link), code_hash, now, now + SIGNUP_HOURS * 3600))
    return signup_by_link(link), link


def signup_by_link(link, now=None):
    """The pending sign-up of an emailed link, while it lasts; else None."""
    with _db() as conn:
        row = conn.execute("SELECT * FROM pending_signups WHERE link_hash = ?",
                           (_hash(link),)).fetchone()
    if not row or row["expires_at"] < (now or time.time()):
        return None
    return dict(row)


def confirm_email(sid):
    with _db() as conn:
        conn.execute("UPDATE pending_signups SET email_verified_at = COALESCE(email_verified_at, "
                     "?) WHERE id = ?", (time.time(), sid))


def finish_signup(sid, workspace):
    """The workspace was made: the link stops working."""
    with _db() as conn:
        conn.execute("UPDATE pending_signups SET workspace = ?, expires_at = ? WHERE id = ?",
                     (workspace, time.time(), sid))


def recent_signups(email, since):
    with _db() as conn:
        return conn.execute("SELECT COUNT(*) FROM pending_signups WHERE email = ? AND "
                            "created_at >= ?", (email, since)).fetchone()[0]


def prune_signups(now=None):
    """Forget pending sign-ups a day after they expired."""
    with _db() as conn:
        return conn.execute("DELETE FROM pending_signups WHERE expires_at < ?",
                            ((now or time.time()) - 86400,)).rowcount
