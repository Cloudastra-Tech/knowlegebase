"""spaces, chat attachments and saved prompts

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-27

Additive only: new tables. Existing rows and columns stay as they are
(conversations.space_id has been there, unused, since 0003).

    spaces              a named collection of sources (chat.md C31): a person's own
                        space (kind personal, one each, holding their files) or a
                        team's, with owners, members and optional instructions
                        (platform.md PL-32, team assistants "lite")
    space_members       who is in a team space, as owner or member. Being in one
                        narrows what a question searches; it never lets anyone see
                        a document their role can't (kb/services/spaces.py)
    space_sources       what a space searches: documents, folders or providers of
                        the workspace's index, matched as source rules match them
    attachments         files attached to one chat (C23), or kept in a personal
                        space. Only the extracted text is kept, never the file;
                        a chat's go with it (ON DELETE CASCADE) or when they expire.
                        Their passages carry the uploader's user principal as
                        their ACL (access.py), so an answer built from one is
                        for its uploader alone, a share link included
    attachment_chunks   that text, split for searching: NOT the workspace index,
                        so nobody else's search, the passage viewer or a rebuild
                        ever sees it
    prompt_templates    saved prompts (C22): a person's own, or the workspace's
                        (owner NULL, made by its admins), with {{variables}}

Every table has a `workspace` column, so deleting a workspace deletes them
(kb/services/lifecycle.py deletes by that column).
"""

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS spaces (
        id          TEXT PRIMARY KEY,            -- uuid
        workspace   TEXT NOT NULL,
        kind        TEXT NOT NULL CHECK (kind IN ('personal', 'team')),
        name        TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        instructions TEXT NOT NULL DEFAULT '',   -- added to the system prompt, delimited
        owner       TEXT,                        -- personal: whose it is (members.username)
        created_by  TEXT NOT NULL,
        created_at  REAL NOT NULL,
        updated_at  REAL NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS spaces_by_workspace ON spaces(workspace)",
    "CREATE UNIQUE INDEX IF NOT EXISTS one_personal_space ON spaces(workspace, owner) "
    "WHERE kind = 'personal'",
    """CREATE TABLE IF NOT EXISTS space_members (
        space_id    TEXT NOT NULL REFERENCES spaces(id) ON DELETE CASCADE,
        workspace   TEXT NOT NULL,
        username    TEXT NOT NULL,
        role        TEXT NOT NULL CHECK (role IN ('owner', 'member')),
        added_by    TEXT NOT NULL,
        added_at    REAL NOT NULL,
        PRIMARY KEY (space_id, username))""",
    "CREATE INDEX IF NOT EXISTS space_members_by_user ON space_members(workspace, username)",
    """CREATE TABLE IF NOT EXISTS space_sources (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        space_id    TEXT NOT NULL REFERENCES spaces(id) ON DELETE CASCADE,
        workspace   TEXT NOT NULL,
        match_type  TEXT NOT NULL CHECK (match_type IN ('doc_key', 'folder', 'provider')),
        match_value TEXT NOT NULL,
        added_by    TEXT NOT NULL,
        added_at    REAL NOT NULL,
        UNIQUE (space_id, match_type, match_value))""",
    """CREATE TABLE IF NOT EXISTS attachments (
        id          TEXT PRIMARY KEY,            -- uuid hex
        workspace   TEXT NOT NULL,
        owner       TEXT NOT NULL,               -- who attached it: the only reader
        conversation_id TEXT REFERENCES conversations(id) ON DELETE CASCADE,
        space_id    TEXT REFERENCES spaces(id) ON DELETE CASCADE,   -- a personal space's file
        filename    TEXT NOT NULL,
        mime        TEXT NOT NULL,
        kind        TEXT NOT NULL,               -- pdf, docx, xlsx, pptx, txt, md, csv, html, image
        size        INTEGER NOT NULL,            -- bytes of the file as sent
        text_chars  INTEGER NOT NULL DEFAULT 0,
        chunks      INTEGER NOT NULL DEFAULT 0,
        tables      INTEGER NOT NULL DEFAULT 0,  -- markdown tables found in its text
        flagged     INTEGER NOT NULL DEFAULT 0,  -- its text reads like instructions to a model
        created_at  REAL NOT NULL,
        expires_at  REAL,                        -- NULL: kept as long as its chat / space
        CHECK ((conversation_id IS NULL) != (space_id IS NULL)))""",
    "CREATE INDEX IF NOT EXISTS attachments_by_conversation ON attachments(conversation_id)",
    "CREATE INDEX IF NOT EXISTS attachments_by_space ON attachments(space_id)",
    """CREATE TABLE IF NOT EXISTS attachment_chunks (
        attachment_id TEXT NOT NULL REFERENCES attachments(id) ON DELETE CASCADE,
        workspace   TEXT NOT NULL,
        n           INTEGER NOT NULL,            -- 0, 1, ... in the text's order
        text        TEXT NOT NULL,
        char_start  INTEGER NOT NULL,
        char_end    INTEGER NOT NULL,
        heading_path TEXT,
        kind        TEXT NOT NULL DEFAULT 'text',  -- text | table | code (chunking.py)
        PRIMARY KEY (attachment_id, n))""",
    """CREATE TABLE IF NOT EXISTS prompt_templates (
        id          TEXT PRIMARY KEY,            -- uuid
        workspace   TEXT NOT NULL,
        owner       TEXT,                        -- NULL: the workspace's (its admins keep them)
        title       TEXT NOT NULL,
        body        TEXT NOT NULL,
        variables   TEXT NOT NULL DEFAULT '[]',  -- JSON: the {{names}} in body, in order
        created_by  TEXT NOT NULL,
        created_at  REAL NOT NULL,
        updated_at  REAL NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS prompt_templates_by_owner ON prompt_templates(workspace, owner)",
)

TABLES = ("prompt_templates", "attachment_chunks", "attachments", "space_sources",
          "space_members", "spaces")


def upgrade():
    conn = op.get_bind()
    for statement in SCHEMA:
        conn.exec_driver_sql(statement)


def downgrade():
    conn = op.get_bind()
    for table in TABLES:
        conn.exec_driver_sql(f"DROP TABLE IF EXISTS {table}")
