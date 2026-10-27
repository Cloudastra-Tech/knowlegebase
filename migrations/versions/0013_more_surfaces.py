"""more surfaces: the Teams bot, the browser extension and email-in

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-27

People ask from Microsoft Teams (platform.md PL-23, teams_store.py), the browser
extension (PL-24) and by email (PL-25, email_in_store.py). Existing rows are all
kept:

    question_log.surface   may now also be teams, extension or email. SQLite
                           can't change a CHECK in place, so the table is copied
                           into a new one with the wider CHECK, every row and
                           column as it was, and its indexes and the trigger that
                           clears a deleted chat's words are made again
    api_keys.client        NULL, or "extension": a key the browser extension was
                           given when someone connected it (its questions are
                           logged with surface "extension")
    teams_installs         per workspace: which Teams bot (ours, set in the
                           environment, or the customer's own Azure Bot, its
                           client secret sealed by secret_box), the Microsoft
                           Entra tenant it answers, and its settings
    teams_activities       activities already taken in, so one Teams sends again
                           is answered once; pruned after a day
    teams_users            a Teams user the bot has seen: their 29:... id, the
                           service URL, and the bot's 1:1 chat with them (to send
                           them an answer privately)
    teams_threads          a Teams chat or channel thread a member asks in -> the
                           conversation its follow-ups continue
    email_in_settings      per workspace: email-in on or off (off unless an admin
                           turns it on)
    email_in_messages      every email taken in, by its Message-ID: once only, who
                           sent it, what became of it, and our reply's Message-ID
                           (a reply to that continues the same conversation)

Downgrading drops the new tables and column and leaves question_log's wider
CHECK as it is (it accepts everything the old one did).
"""

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

OLD_CHECK = "surface IN ('web', 'api', 'slack', 'mcp')"
NEW_CHECK = "surface IN ('web', 'api', 'slack', 'mcp', 'teams', 'extension', 'email')"

QUESTION_LOG_AFTER = (
    "CREATE INDEX IF NOT EXISTS question_log_by_day ON question_log(workspace, day)",
    "CREATE INDEX IF NOT EXISTS question_log_by_message ON question_log(message_id) "
    "WHERE message_id IS NOT NULL",
    """CREATE TRIGGER IF NOT EXISTS question_log_forget_message
        AFTER DELETE ON messages BEGIN
            UPDATE question_log SET question = NULL, answer = NULL, embedding = NULL,
                                    message_id = NULL
             WHERE message_id = OLD.id;
        END""",
)

TABLES = (
    """CREATE TABLE IF NOT EXISTS teams_installs (
        workspace    TEXT PRIMARY KEY REFERENCES workspaces(slug) ON DELETE CASCADE,
        mode         TEXT NOT NULL DEFAULT 'custom' CHECK (mode IN ('central', 'custom')),
                                            -- central: the bot set in the environment;
                                            -- custom: the customer's own Azure Bot
        app_key      TEXT UNIQUE,           -- custom: the id in its messaging endpoint
        app_id       TEXT,                  -- custom: its Microsoft App ID
        app_tenant   TEXT,                  -- custom: the tenant its app registration is in
        app_secret   BLOB,                  -- custom: sealed client secret
        tenant_id    TEXT UNIQUE,           -- the Entra tenant answered: one workspace each
        status       TEXT NOT NULL DEFAULT 'configured'
                     CHECK (status IN ('configured', 'connected')),
        unknown_users TEXT NOT NULL DEFAULT 'refuse'
                     CHECK (unknown_users IN ('refuse', 'public')),
        last_activity_at REAL,
        updated_by   TEXT,
        updated_at   REAL)""",
    """CREATE TABLE IF NOT EXISTS teams_activities (
        activity_id  TEXT PRIMARY KEY,      -- the activity's id, with its conversation's
        tenant_id    TEXT,
        received_at  REAL NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS teams_activities_by_time ON teams_activities(received_at)",
    """CREATE TABLE IF NOT EXISTS teams_users (
        tenant_id    TEXT NOT NULL,
        aad_object_id TEXT NOT NULL,
        user_id      TEXT NOT NULL,         -- the Teams user id (29:...)
        service_url  TEXT NOT NULL,
        personal_conversation TEXT,         -- the bot's 1:1 chat with them, once known
        updated_at   REAL NOT NULL,
        PRIMARY KEY (tenant_id, aad_object_id))""",
    """CREATE TABLE IF NOT EXISTS teams_threads (
        tenant_id    TEXT NOT NULL,
        conversation TEXT NOT NULL,         -- the Teams conversation id (a channel
                                            -- thread's carries ;messageid=)
        aad_object_id TEXT NOT NULL,
        conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
        updated_at   REAL NOT NULL,
        PRIMARY KEY (tenant_id, conversation, aad_object_id))""",
    """CREATE TABLE IF NOT EXISTS email_in_settings (
        workspace    TEXT PRIMARY KEY REFERENCES workspaces(slug) ON DELETE CASCADE,
        enabled      INTEGER NOT NULL DEFAULT 0,
        updated_by   TEXT,
        updated_at   REAL)""",
    """CREATE TABLE IF NOT EXISTS email_in_messages (
        message_id   TEXT PRIMARY KEY,      -- the email's Message-ID (or the provider's id)
        workspace    TEXT NOT NULL,
        sender       TEXT NOT NULL,         -- the From address, lower case
        received_at  REAL NOT NULL,
        status       TEXT NOT NULL,         -- queued, answered, refused, dropped, failed
        reason       TEXT,                  -- why it was refused or dropped
        conversation_id TEXT,               -- the conversation it was answered in
        reply_message_id TEXT)              -- the Message-ID of our reply""",
    "CREATE INDEX IF NOT EXISTS email_in_by_reply ON email_in_messages(reply_message_id) "
    "WHERE reply_message_id IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS email_in_by_sender ON email_in_messages(workspace, sender, "
    "received_at)",
)
NEW_TABLES = ("teams_installs", "teams_activities", "teams_users", "teams_threads",
              "email_in_settings", "email_in_messages")


def _has_column(conn, table, column):
    return any(row[1] == column for row in conn.exec_driver_sql(f"PRAGMA table_info({table})"))


def _widen_question_log(conn):
    """Copy question_log into a table whose CHECK allows the new surfaces."""
    row = conn.exec_driver_sql("SELECT sql FROM sqlite_master WHERE type = 'table' AND "
                               "name = 'question_log'").fetchone()
    if not row or OLD_CHECK not in row[0]:
        return                              # already wide (or made by a later baseline)
    create = row[0].replace(OLD_CHECK, NEW_CHECK)
    create = create.replace("question_log", "question_log_new", 1)
    columns = ", ".join(r[1] for r in conn.exec_driver_sql("PRAGMA table_info(question_log)"))
    # The trigger names question_log: drop it first, or renaming the new table fails.
    conn.exec_driver_sql("DROP TRIGGER IF EXISTS question_log_forget_message")
    conn.exec_driver_sql("DROP TABLE IF EXISTS question_log_new")
    conn.exec_driver_sql(create)
    conn.exec_driver_sql(f"INSERT INTO question_log_new ({columns}) "
                         f"SELECT {columns} FROM question_log")
    conn.exec_driver_sql("DROP TABLE question_log")
    conn.exec_driver_sql("ALTER TABLE question_log_new RENAME TO question_log")
    for statement in QUESTION_LOG_AFTER:
        conn.exec_driver_sql(statement)


def upgrade():
    conn = op.get_bind()
    _widen_question_log(conn)
    if not _has_column(conn, "api_keys", "client"):
        conn.exec_driver_sql("ALTER TABLE api_keys ADD COLUMN client TEXT")
    for statement in TABLES:
        conn.exec_driver_sql(statement)


def downgrade():
    conn = op.get_bind()
    for table in reversed(NEW_TABLES):
        conn.exec_driver_sql(f"DROP TABLE IF EXISTS {table}")
    if _has_column(conn, "api_keys", "client"):
        with op.batch_alter_table("api_keys") as batch:
            batch.drop_column("client")
