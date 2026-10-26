"""cached tokens and chunk contexts

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-27

Additive only: one new column (NULL on existing rows) and one new table.

    question_log.tokens_cached   input tokens OpenAI served from its prompt cache
                                 for the question (pipeline.md P1-5); NULL when
                                 the model calls didn't report it
    chunk_contexts               the optional one-line context an LLM writes for
                                 a chunk at indexing (pipeline.md P1-3,
                                 RAG_CHUNK_SUMMARY=llm), cached by a hash of the
                                 model, the document and the chunk, so a rebuild
                                 or an unchanged chunk never pays for it twice
"""

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS chunk_contexts (
        hash        TEXT PRIMARY KEY,
        model       TEXT NOT NULL,
        context     TEXT NOT NULL,
        created_at  REAL NOT NULL)""",
)


def _has_column(conn, table, column):
    return any(row[1] == column for row in conn.exec_driver_sql(f"PRAGMA table_info({table})"))


def upgrade():
    conn = op.get_bind()
    if not _has_column(conn, "question_log", "tokens_cached"):
        conn.exec_driver_sql("ALTER TABLE question_log ADD COLUMN tokens_cached INTEGER")
    for statement in SCHEMA:
        conn.exec_driver_sql(statement)


def downgrade():
    conn = op.get_bind()
    conn.exec_driver_sql("DROP TABLE IF EXISTS chunk_contexts")
    with op.batch_alter_table("question_log") as batch:
        batch.drop_column("tokens_cached")
