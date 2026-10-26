"""the Slack app: installs, delivered events, and threads

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-27

People ask from Slack (platform.md PL-22, slack_store.py). New tables only:
nothing that exists is changed.

    slack_installs  per workspace: which Slack app (ours, set in the environment,
                    or the customer's own, its credentials sealed), the Slack team
                    it's installed in and the bot token (sealed by secret_box),
                    and the workspace's Slack settings
    slack_events    the events already taken in, so a delivery Slack retries
                    (X-Slack-Retry-Num) is answered once; pruned after a day
    slack_threads   a Slack thread (or DM) a member asks in -> the conversation
                    its follow-ups continue
"""

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

TABLES = (
    """CREATE TABLE IF NOT EXISTS slack_installs (
        workspace    TEXT PRIMARY KEY REFERENCES workspaces(slug) ON DELETE CASCADE,
        mode         TEXT NOT NULL DEFAULT 'central' CHECK (mode IN ('central', 'custom')),
                                            -- central: the app set in the environment;
                                            -- custom: the customer's own internal app
        app_key      TEXT UNIQUE,           -- custom: the id in its request URLs
        client_id    TEXT,                  -- custom
        app_secrets  BLOB,                  -- custom: sealed {client_secret, signing_secret}
        status       TEXT NOT NULL DEFAULT 'configured'
                     CHECK (status IN ('configured', 'installed', 'revoked')),
        team_id      TEXT UNIQUE,           -- once installed: one workspace per Slack team
        team_name    TEXT,
        app_id       TEXT,
        bot_user_id  TEXT,
        bot_token    BLOB,                  -- sealed
        scopes       TEXT,
        unknown_users TEXT NOT NULL DEFAULT 'refuse'
                     CHECK (unknown_users IN ('refuse', 'public')),
        auto_answer  INTEGER NOT NULL DEFAULT 0,
        auto_answer_channels TEXT NOT NULL DEFAULT '[]',   -- JSON: channel ids
        installed_by TEXT,
        installed_at REAL,
        updated_by   TEXT,
        updated_at   REAL)""",
    """CREATE TABLE IF NOT EXISTS slack_events (
        event_id     TEXT PRIMARY KEY,      -- Slack's event_id, or a command's / action's own key
        team_id      TEXT,
        received_at  REAL NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS slack_events_by_time ON slack_events(received_at)",
    """CREATE TABLE IF NOT EXISTS slack_threads (
        team_id      TEXT NOT NULL,
        channel      TEXT NOT NULL,
        thread_ts    TEXT NOT NULL,         -- the thread's first message; a DM: "dm"
        slack_user   TEXT NOT NULL,
        conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
        created_at   REAL NOT NULL,
        PRIMARY KEY (team_id, channel, thread_ts, slack_user))""",
)


def upgrade():
    conn = op.get_bind()
    for statement in TABLES:
        conn.exec_driver_sql(statement)


def downgrade():
    conn = op.get_bind()
    for table in ("slack_threads", "slack_events", "slack_installs"):
        conn.exec_driver_sql(f"DROP TABLE IF EXISTS {table}")
