"""plans, subscriptions and billing owners; onboarding; pending sign-ups

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-27

Additive only: new tables, and one row per founder in billing_owners. Nothing is
enforced unless BILLING=on (kb/services/billing.py); a workspace with no
subscription row is never limited, so existing workspaces keep working as they
are when billing is turned on (an operator gives them a plan).

    plans            what can be bought (platform.md PL-16): a seat price and its
                     limits as JSON (questions a month, documents, storage,
                     connectors; null: no limit) and features (sso_oidc_saml,
                     scim, audit_export, api, slack, mcp, teams). Seeded here;
                     the operator edits rows, not code.
    subscriptions    a workspace's plan: trialing | active | past_due | canceled,
                     when its trial ends, its Stripe customer and subscription,
                     and the seats paid for
    billing_owners   who may change a workspace's billing (the founder, to start
                     with). Admins aren't owners unless made one
    stripe_events    Stripe webhook events already handled (each is taken once)
    onboarding       when each onboarding step was first seen done (PL-17), so a
                     step stays done; the steps are worked out from the data
    pending_signups  a company signing up with email and a DNS TXT record: no
                     workspace exists until the record checks out

Every table holding a workspace's rows has a `workspace` column, so deleting a
workspace deletes them (kb/services/lifecycle.py). plans has none: it's the
server's.
"""

import json
import time

from alembic import op

from migrations import dialect

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None

FEATURES = ("sso_oidc_saml", "scim", "audit_export", "api", "slack", "mcp", "teams")


def _limits(questions, docs, storage_mb, connectors, *features):
    return json.dumps({"questions_month": questions, "docs": docs, "storage_mb": storage_mb,
                       "connectors": connectors,
                       **{f: f in features for f in FEATURES}})


# (id, name, seat price in cents, buyable, order, limits)
PLANS = (
    ("trial", "Trial", 0, 0, 0,
     _limits(1000, 200, 1024, 2, "sso_oidc_saml", "audit_export", "api", "slack", "mcp",
             "teams")),
    ("starter", "Starter", 800, 1, 1, _limits(2000, 500, 2048, 2, "slack")),
    ("business", "Business", 2000, 1, 2,
     _limits(20000, 5000, 20480, 10, "sso_oidc_saml", "audit_export", "api", "slack", "mcp",
             "teams")),
    ("enterprise", "Enterprise", 3500, 1, 3, _limits(None, None, None, None, *FEATURES)),
)

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS plans (
        id          TEXT PRIMARY KEY,            -- trial, starter, business, enterprise
        name        TEXT NOT NULL,
        seat_price  INTEGER NOT NULL DEFAULT 0,  -- cents a seat a month
        currency    TEXT NOT NULL DEFAULT 'usd',
        buyable     INTEGER NOT NULL DEFAULT 1,  -- 0: given, never bought (the trial)
        sort        INTEGER NOT NULL DEFAULT 0,
        limits      TEXT NOT NULL DEFAULT '{}',  -- JSON, see the top of this file
        stripe_price TEXT,                       -- STRIPE_PRICE_<ID> in the env wins
        updated_at  REAL NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS subscriptions (
        workspace   TEXT PRIMARY KEY,
        plan        TEXT NOT NULL REFERENCES plans(id),
        status      TEXT NOT NULL CHECK (status IN ('trialing', 'active', 'past_due',
                                                    'canceled')),
        trial_ends_at REAL,                      -- trialing: read-only after this
        stripe_customer TEXT,
        stripe_subscription TEXT,
        seats       INTEGER NOT NULL DEFAULT 0,  -- paid for (Stripe's quantity)
        current_period_end REAL,
        reminders   TEXT NOT NULL DEFAULT '[]',  -- JSON: trial reminders sent (days left)
        deletion_scheduled_at REAL,              -- an expired trial was handed to deletion
        created_at  REAL NOT NULL,
        updated_at  REAL NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS subscriptions_by_customer ON subscriptions(stripe_customer)",
    """CREATE TABLE IF NOT EXISTS billing_owners (
        workspace   TEXT NOT NULL,
        username    TEXT NOT NULL,               -- members.username
        granted_by  TEXT,                        -- NULL: they founded the workspace
        granted_at  REAL NOT NULL,
        PRIMARY KEY (workspace, username))""",
    """CREATE TABLE IF NOT EXISTS stripe_events (
        id          TEXT PRIMARY KEY,            -- evt_...
        type        TEXT NOT NULL,
        workspace   TEXT,
        received_at REAL NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS onboarding (
        workspace   TEXT NOT NULL,
        step        TEXT NOT NULL,
        done_at     REAL NOT NULL,
        PRIMARY KEY (workspace, step))""",
    """CREATE TABLE IF NOT EXISTS pending_signups (
        id          TEXT PRIMARY KEY,            -- uuid
        workspace_name TEXT NOT NULL,
        email       TEXT NOT NULL,
        domain      TEXT NOT NULL,
        verify_token TEXT NOT NULL,              -- the DNS TXT record's value
        link_hash   TEXT NOT NULL,               -- sha256 of the emailed link's token
        email_verified_at REAL,                  -- the founder opened the link
        code_hash   TEXT,                        -- the invite code it will use up
        workspace   TEXT,                        -- made, once the record checked out
        created_at  REAL NOT NULL,
        expires_at  REAL NOT NULL)""",
    "CREATE UNIQUE INDEX IF NOT EXISTS pending_signups_by_link ON pending_signups(link_hash)",
    "CREATE INDEX IF NOT EXISTS pending_signups_by_email ON pending_signups(email)",
)

TABLES = ("pending_signups", "onboarding", "stripe_events", "billing_owners", "subscriptions",
          "plans")


def upgrade():
    conn = dialect.bind()
    for statement in SCHEMA:
        conn.exec_driver_sql(statement)
    now = time.time()
    for plan in PLANS:
        conn.exec_driver_sql("INSERT OR IGNORE INTO plans (id, name, seat_price, buyable, sort, "
                             "limits, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)", (*plan, now))
    # Whoever created a workspace at sign-up owns its billing, if still one of its
    # members (the audit log has who; workspaces made otherwise get one from the
    # operator console).
    conn.exec_driver_sql(
        """INSERT OR IGNORE INTO billing_owners (workspace, username, granted_by, granted_at)
           SELECT a.workspace, a.actor, NULL, a.at FROM audit_events a
             JOIN members m ON m.username = a.actor AND m.workspace = a.workspace
            WHERE a.action = 'workspace.create'""")


def downgrade():
    conn = dialect.bind()
    for table in TABLES:
        conn.exec_driver_sql(f"DROP TABLE IF EXISTS {table}")
