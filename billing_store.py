"""
billing_store.py  —  plans, subscriptions and who owns a workspace's billing
(config/app.db, see db.py; tables made by migrations/versions/0020_billing_onboarding.py).

    plans            what can be bought: a seat price, limits and features (JSON)
    subscriptions    one per workspace that billing applies to: its plan, status,
                     trial end, Stripe ids and the seats paid for. A workspace
                     without one is never limited (made before billing, or with
                     BILLING off)
    billing_owners   who may change a workspace's billing
    stripe_events    Stripe webhook events already handled

Only SQL lives here; kb/services/billing.py decides what it means.
"""

import json
import time

import db

STATUSES = ("trialing", "active", "past_due", "canceled")


def _db():
    return db.connect()


# ---- Plans ------------------------------------------------------------------------------
def _plan(row):
    if not row:
        return None
    p = dict(row)
    p["limits"] = json.loads(p["limits"] or "{}")
    p["buyable"] = bool(p["buyable"])
    return p


def plans():
    with _db() as conn:
        return [_plan(r) for r in conn.execute("SELECT * FROM plans ORDER BY sort, id")]


def plan(plan_id):
    with _db() as conn:
        return _plan(conn.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone())


def plan_for_price(price_id):
    """The plan a Stripe price is for (its stripe_price column), or None."""
    with _db() as conn:
        return _plan(conn.execute("SELECT * FROM plans WHERE stripe_price = ?",
                                  (price_id,)).fetchone())


# ---- Subscriptions ----------------------------------------------------------------------
def _sub(row):
    if not row:
        return None
    s = dict(row)
    s["reminders"] = json.loads(s["reminders"] or "[]")
    return s


def subscription(workspace):
    with _db() as conn:
        return _sub(conn.execute("SELECT * FROM subscriptions WHERE workspace = ?",
                                 (workspace,)).fetchone())


def subscription_for_customer(customer):
    with _db() as conn:
        return _sub(conn.execute("SELECT * FROM subscriptions WHERE stripe_customer = ?",
                                 (customer,)).fetchone())


def subscriptions():
    with _db() as conn:
        return [_sub(r) for r in conn.execute("SELECT * FROM subscriptions ORDER BY workspace")]


def start_trial(workspace, plan_id, ends_at):
    """A new workspace's trial. Does nothing if it already has a subscription."""
    now = time.time()
    with _db() as conn:
        conn.execute("INSERT OR IGNORE INTO subscriptions (workspace, plan, status, trial_ends_at, "
                     "created_at, updated_at) VALUES (?, ?, 'trialing', ?, ?, ?)",
                     (workspace, plan_id, ends_at, now, now))
    return subscription(workspace)


def save_subscription(workspace, **fields):
    """Create or change a workspace's subscription (plan and status are needed to create)."""
    if "status" in fields and fields["status"] not in STATUSES:
        raise ValueError(f"status must be one of: {', '.join(STATUSES)}")
    if "reminders" in fields:
        fields["reminders"] = json.dumps(fields["reminders"])
    now = time.time()
    with _db() as conn:
        if conn.execute("SELECT 1 FROM subscriptions WHERE workspace = ?",
                        (workspace,)).fetchone():
            cols = ", ".join(f"{k} = ?" for k in fields)
            conn.execute(f"UPDATE subscriptions SET {cols}, updated_at = ? WHERE workspace = ?",
                         (*fields.values(), now, workspace))
        else:
            fields = {"plan": "trial", "status": "trialing", **fields}
            cols = ", ".join(fields)
            conn.execute(f"INSERT INTO subscriptions (workspace, {cols}, created_at, updated_at) "
                         f"VALUES (?, {', '.join('?' * len(fields))}, ?, ?)",
                         (workspace, *fields.values(), now, now))
    return subscription(workspace)


# ---- Billing owners ---------------------------------------------------------------------
def owners(workspace):
    with _db() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT username, granted_by, granted_at FROM billing_owners WHERE workspace = ? "
            "ORDER BY granted_at, username", (workspace,))]


def is_owner(workspace, username):
    with _db() as conn:
        return conn.execute("SELECT 1 FROM billing_owners WHERE workspace = ? AND username = ?",
                            (workspace, (username or "").strip().lower())).fetchone() is not None


def add_owner(workspace, username, granted_by=None):
    with _db() as conn:
        return conn.execute("INSERT OR IGNORE INTO billing_owners (workspace, username, "
                            "granted_by, granted_at) VALUES (?, ?, ?, ?)",
                            (workspace, username.strip().lower(), granted_by,
                             time.time())).rowcount > 0


def remove_owner(workspace, username):
    with _db() as conn:
        return conn.execute("DELETE FROM billing_owners WHERE workspace = ? AND username = ?",
                            (workspace, username.strip().lower())).rowcount > 0


# ---- Stripe events ----------------------------------------------------------------------
def first_time(event_id, event_type, workspace=None):
    """Record a webhook event; False if it was already handled (Stripe retries)."""
    with _db() as conn:
        return conn.execute("INSERT OR IGNORE INTO stripe_events (id, type, workspace, "
                            "received_at) VALUES (?, ?, ?, ?)",
                            (event_id, event_type, workspace, time.time())).rowcount > 0


def forget_event(event_id):
    """An event whose handling failed: Stripe's retry gets another go."""
    with _db() as conn:
        conn.execute("DELETE FROM stripe_events WHERE id = ?", (event_id,))
