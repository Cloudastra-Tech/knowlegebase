"""model providers: a workspace's own AWS Bedrock, and what the index was built with

Revision ID: 0022
Revises: 0021
Create Date: 2026-10-27

    model_providers   a workspace's model provider (models.py, model_providers_store.py):
                      'default' (the server's MODEL_PROVIDER) or 'bedrock' in the
                      company's own AWS account: its region, how the server gets in
                      (a role it assumes with the ExternalId generated here, or access
                      keys sealed under the workspace's key) and the model ids. No
                      row: the server's provider, as before
    search_indexes.embeddings   the embedding model the collection was built with
                      (models.Models.embed_name); NULL: OpenAI's
                      text-embedding-3-small, what every index before this has
    question_log.provider       openai / bedrock: who answered (NULL before this)

Additive only: existing rows keep their meaning. The new table has a `workspace`
column, so deleting a workspace deletes its row (kb/services/lifecycle.py).
"""

from alembic import op

from migrations import dialect

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None

COLUMNS = (
    ("search_indexes", "embeddings", "TEXT"),
    ("question_log", "provider", "TEXT"),
)

TABLES = (
    """CREATE TABLE IF NOT EXISTS model_providers (
        workspace    TEXT PRIMARY KEY,
        provider     TEXT NOT NULL DEFAULT 'default'
                     CHECK (provider IN ('default', 'bedrock')),
        region       TEXT,
        auth         TEXT NOT NULL DEFAULT 'role' CHECK (auth IN ('role', 'keys')),
        role_arn     TEXT,
        external_id  TEXT NOT NULL,              -- generated here, never chosen by the admin
        access_keys  BLOB,                       -- secret_box.seal({id, secret}, workspace)
        chat_model   TEXT,
        fast_model   TEXT,
        embed_model  TEXT,
        embed_dims   INTEGER,
        tested_at    REAL,                       -- the last Test connection that worked
        updated_by   TEXT,
        updated_at   REAL NOT NULL)""",
)


def upgrade():
    conn = dialect.bind()
    for table, column, kind in COLUMNS:
        if not dialect.has_column(conn, table, column):
            conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
    for statement in TABLES:
        conn.exec_driver_sql(statement)


def downgrade():
    conn = dialect.bind()
    conn.exec_driver_sql("DROP TABLE IF EXISTS model_providers")
    # question_log keeps its new column (empty for older code): copying it again
    # would drop the trigger that clears a deleted chat's words (0007, 0015).
    with op.batch_alter_table("search_indexes") as batch:
        batch.drop_column("embeddings")
