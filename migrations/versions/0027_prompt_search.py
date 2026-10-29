"""saved prompts: whether running one searches the knowledge

Revision ID: 0027
Revises: 0026
Create Date: 2026-10-29

    prompt_templates.search_knowledge   1: running the prompt searches the
                                        workspace's knowledge first, as every
                                        question does (and every prompt did
                                        before this). 0: it is answered directly,
                                        nothing searched or cited ("just respond
                                        with OK", "rewrite this text"),
                                        kb/routers/chat.py

Additive only: every existing prompt keeps searching (DEFAULT 1).

Downgrade drops the column (ALTER TABLE ... DROP COLUMN): older code searches
for every prompt, as it always did.
"""

from migrations import dialect

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade():
    conn = dialect.bind()
    if not dialect.has_column(conn, "prompt_templates", "search_knowledge"):
        conn.exec_driver_sql("ALTER TABLE prompt_templates "
                             "ADD COLUMN search_knowledge INTEGER NOT NULL DEFAULT 1")


def downgrade():
    conn = dialect.bind()
    if dialect.has_column(conn, "prompt_templates", "search_knowledge"):
        conn.exec_driver_sql("ALTER TABLE prompt_templates DROP COLUMN search_knowledge")
