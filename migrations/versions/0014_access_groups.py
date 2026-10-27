"""access groups, document ACLs and per-connection permission policies

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-27

Who may see what moves from two levels (public / restricted) to ACLs of
principals (platform.md PL-09, PL-10 step 2; access.py). Additive; existing rows
keep their meaning:

    access_groups      groups that gate content: made by hand (manual), or
                       copies of the identity provider's groups (idp: sign-in
                       claims, scim: pushed through /scim/v2), kept current by
                       groups_store.refresh_external. The built-in group "hr"
                       (HR and Admin, the old "restricted" level) has no row.
    group_members      who is in a group
    doc_acl            who may see an uploaded document, when picked on the
                       Documents screen. No rows: its folder decides, as before
                       (public/ everyone, restricted/ the hr group).
    messages.acl       the audience of an answer: a JSON list of ACLs, all of
                       which a reader must meet. Filled in from max_access for
                       answers already saved: public [], restricted [["group:hr"]].
A connection's permission policy (source, workspace or restricted) is kept in
integrations.settings["policy"] when one is set. Rows are left as they are: one
without it has its access level's (integrations_store.policy_of: everyone is
workspace, anything else restricted).
"""

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS access_groups (
        workspace   TEXT NOT NULL,
        id          TEXT NOT NULL,                  -- g_<hex> (manual), idp_<hash>, scim_<id>
        name        TEXT NOT NULL,
        source      TEXT NOT NULL CHECK (source IN ('manual', 'idp', 'scim')),
        email       TEXT,                           -- matched to a source's group (Drive)
        external_id TEXT,                           -- the identity provider's id for it
        description TEXT NOT NULL DEFAULT '',
        created_by  TEXT,
        created_at  REAL NOT NULL,
        updated_at  REAL NOT NULL,
        PRIMARY KEY (workspace, id))""",
    """CREATE UNIQUE INDEX IF NOT EXISTS access_groups_by_name
        ON access_groups(workspace, source, name COLLATE NOCASE)""",
    """CREATE TABLE IF NOT EXISTS group_members (
        workspace   TEXT NOT NULL,
        group_id    TEXT NOT NULL,
        username    TEXT NOT NULL,                  -- members.username
        added_by    TEXT,
        added_at    REAL NOT NULL,
        PRIMARY KEY (workspace, group_id, username),
        FOREIGN KEY (workspace, group_id) REFERENCES access_groups(workspace, id)
            ON DELETE CASCADE)""",
    "CREATE INDEX IF NOT EXISTS group_members_by_user ON group_members(workspace, username)",
    """CREATE TABLE IF NOT EXISTS doc_acl (
        workspace      TEXT NOT NULL,
        doc_key        TEXT NOT NULL,               -- ingest.doc_files: public/handbook.txt
        principal_type TEXT NOT NULL CHECK (principal_type IN ('group', 'user', 'everyone')),
        principal      TEXT NOT NULL,               -- a group id, a username, or ''
        created_by     TEXT,
        created_at     REAL NOT NULL,
        PRIMARY KEY (workspace, doc_key, principal_type, principal))""",
)

def _has_column(conn, table, column):
    return any(row[1] == column for row in conn.exec_driver_sql(f"PRAGMA table_info({table})"))


def upgrade():
    conn = op.get_bind()
    for statement in SCHEMA:
        conn.exec_driver_sql(statement)
    if not _has_column(conn, "messages", "acl"):
        conn.exec_driver_sql("ALTER TABLE messages ADD COLUMN acl TEXT")
    conn.exec_driver_sql("""UPDATE messages SET acl = CASE max_access WHEN 'public' THEN '[]'
                            ELSE '[["group:hr"]]' END
                            WHERE author = 'assistant' AND acl IS NULL""")


def downgrade():
    conn = op.get_bind()
    for table in ("doc_acl", "group_members", "access_groups"):
        conn.exec_driver_sql(f"DROP TABLE IF EXISTS {table}")
    with op.batch_alter_table("messages") as batch:
        batch.drop_column("acl")
