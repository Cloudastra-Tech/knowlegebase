"""digests, drafts and outbound webhooks

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-27

New tables only (platform.md PL-30, PL-31, PL-20); nothing existing changes. Every
one has a `workspace` column, so a workspace's purge (kb/services/lifecycle.py)
deletes its rows like any other table's.

    doc_changes           what changed in a workspace's documents: a document
                          added, modified or removed by an upload, an edit or a
                          connector sync, with its title and the ACL it had
    digest_subscriptions  who gets a digest of those changes, how often, of which
                          sources or folders, by email or Slack DM
    doc_summaries         the short summary of one version of a document (a
                          digest's line, a folder summary's first step), made once
    drafts                documents written by the assistant for review: a folder's
                          summary or a doc for a content gap. The text is a file in
                          <docs>/drafts/, which is never indexed until published
    webhooks              an admin's endpoints: the URL, the events it wants and its
                          signing secret (sealed under the workspace's key)
    webhook_deliveries    each event sent to each endpoint, retried for 24 hours
"""

from alembic import op

from migrations import dialect

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

TABLES = (
    """CREATE TABLE IF NOT EXISTS doc_changes (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        workspace    TEXT NOT NULL,
        doc_key      TEXT NOT NULL,         -- as ingest.doc_files keys it: public/a.txt, ...
        provider     TEXT NOT NULL,         -- "uploads", or the connector's id
        folder       TEXT,                  -- the upload's access folder, the item's parent
        title        TEXT,
        change       TEXT NOT NULL CHECK (change IN ('added', 'modified', 'removed')),
        access       TEXT NOT NULL,         -- its chunks' access tag when it changed
        acl          TEXT NOT NULL DEFAULT '["group:hr"]',  -- JSON: its ACL then (access.py)
        version      TEXT,                  -- a hash of its content then (none: removed)
        actor        TEXT,                  -- who uploaded or edited it (none: a sync)
        at           REAL NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS doc_changes_by_workspace ON doc_changes(workspace, at)",
    """CREATE TABLE IF NOT EXISTS digest_subscriptions (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        workspace    TEXT NOT NULL,
        username     TEXT NOT NULL,
        frequency    TEXT NOT NULL DEFAULT 'weekly' CHECK (frequency IN ('daily', 'weekly')),
        sources      TEXT NOT NULL DEFAULT '[]',   -- JSON: providers; empty = all of them
        folders      TEXT NOT NULL DEFAULT '[]',   -- JSON: folders; empty = all of them
        channel      TEXT NOT NULL DEFAULT 'email' CHECK (channel IN ('email', 'slack')),
        enabled      INTEGER NOT NULL DEFAULT 1,
        unsubscribe_hash  TEXT NOT NULL UNIQUE,    -- sha256 of the email link's token
        unsubscribe_token BLOB NOT NULL,           -- the token, sealed (it goes in each email)
        last_sent_at REAL,
        next_due_at  REAL NOT NULL,
        last_error   TEXT,
        created_at   REAL NOT NULL,
        updated_at   REAL NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS digest_subscriptions_due "
    "ON digest_subscriptions(enabled, next_due_at)",
    "CREATE INDEX IF NOT EXISTS digest_subscriptions_by_user "
    "ON digest_subscriptions(workspace, username)",
    """CREATE TABLE IF NOT EXISTS doc_summaries (
        workspace    TEXT NOT NULL,
        doc_key      TEXT NOT NULL,
        version      TEXT NOT NULL,         -- sha256 of the content summarised
        summary      TEXT NOT NULL,
        model        TEXT,
        created_at   REAL NOT NULL,
        PRIMARY KEY (workspace, doc_key, version))""",
    """CREATE TABLE IF NOT EXISTS drafts (
        id           TEXT PRIMARY KEY,      -- random hex; the file is <docs>/drafts/<id>.txt
        workspace    TEXT NOT NULL,
        kind         TEXT NOT NULL CHECK (kind IN ('summary', 'gap')),
        title        TEXT NOT NULL,
        scope        TEXT NOT NULL DEFAULT '{}',   -- JSON: {provider, folder} or {gap_id, label}
        status       TEXT NOT NULL DEFAULT 'generating'
                     CHECK (status IN ('generating', 'draft', 'failed', 'published')),
        sources      TEXT NOT NULL DEFAULT '[]',   -- JSON: the documents it was written from
        max_access   TEXT NOT NULL DEFAULT 'restricted',  -- the highest of their levels
        audience     TEXT NOT NULL DEFAULT '[["nobody"]]',  -- JSON: their ACLs as clauses
                                            -- (access.clauses): who may read the draft
        error        TEXT,
        created_by   TEXT NOT NULL,
        created_at   REAL NOT NULL,
        updated_at   REAL NOT NULL,
        published_as TEXT,                  -- the document's key once published
        published_by TEXT,
        published_at REAL)""",
    "CREATE INDEX IF NOT EXISTS drafts_by_workspace ON drafts(workspace, created_at)",
    """CREATE TABLE IF NOT EXISTS webhooks (
        id           TEXT PRIMARY KEY,      -- random hex
        workspace    TEXT NOT NULL,
        url          TEXT NOT NULL,
        description  TEXT,
        events       TEXT NOT NULL,         -- JSON list of event names
        secret       BLOB NOT NULL,         -- the signing secret, sealed (secret_box)
        secret_hint  TEXT NOT NULL,         -- its last 4 characters, to tell secrets apart
        enabled      INTEGER NOT NULL DEFAULT 1,
        created_by   TEXT NOT NULL,
        created_at   REAL NOT NULL,
        updated_at   REAL NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS webhooks_by_workspace ON webhooks(workspace)",
    """CREATE TABLE IF NOT EXISTS webhook_deliveries (
        id           TEXT PRIMARY KEY,      -- random hex: the X-KB-Delivery header
        webhook_id   TEXT NOT NULL REFERENCES webhooks(id) ON DELETE CASCADE,
        workspace    TEXT NOT NULL,
        event        TEXT NOT NULL,
        event_id     TEXT NOT NULL,         -- the same for every endpoint the event went to
        payload      TEXT NOT NULL,         -- the JSON body sent
        status       TEXT NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending', 'delivered', 'failed')),
        attempts     INTEGER NOT NULL DEFAULT 0,
        next_at      REAL,                  -- when it's tried next (pending)
        response_code INTEGER,
        error        TEXT,
        created_at   REAL NOT NULL,
        last_attempt_at REAL,
        delivered_at REAL)""",
    "CREATE INDEX IF NOT EXISTS webhook_deliveries_due "
    "ON webhook_deliveries(workspace, status, next_at)",
    "CREATE INDEX IF NOT EXISTS webhook_deliveries_by_webhook "
    "ON webhook_deliveries(webhook_id, created_at)",
)


def upgrade():
    conn = dialect.bind()
    for statement in TABLES:
        conn.exec_driver_sql(statement)


def downgrade():
    conn = dialect.bind()
    for table in ("webhook_deliveries", "webhooks", "drafts", "doc_summaries",
                  "digest_subscriptions", "doc_changes"):
        conn.exec_driver_sql(f"DROP TABLE IF EXISTS {table}")
