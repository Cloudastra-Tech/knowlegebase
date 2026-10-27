"""group ACLs in insights, per-workspace caches, webhook upkeep

Revision ID: 0023
Revises: 0022
Create Date: 2026-10-27

    acl columns         question_log, eval_candidates, content_gaps, insights_docs
                        and source_stats: who may read the row, as access.py
                        clauses (JSON), so Insights, the question log's samples
                        and the test set are gated by the reader's groups, not
                        only by an access level. NULL on rows from before: they
                        keep being gated by their max_access / access, as now
    chunk_contexts      keyed by (workspace, hash): each workspace's own, so it is
                        under row-level security and a workspace's purge deletes
                        it. It is a cache of model-written lines (chunk_context.py):
                        the old rows, which no workspace can be told from, are
                        dropped, and an index built with RAG_CHUNK_SUMMARY=llm
                        asks for them again on its next rebuild only
    embedding_cache     a chunk's vector by the hash of its text and the embedding
                        model (ingest.py): re-chunking text that didn't change, or a
                        rebuild, doesn't embed it again. Per workspace, like the rest
    webhooks            failures (consecutive failed attempts; turned off after
                        WEBHOOK_DISABLE_AFTER), disabled_reason / disabled_at, and the
                        previous signing secret while a rotation's grace period runs
                        (previous_secret, sealed like `secret`, until
                        previous_secret_until)

Additive except chunk_contexts (a cache). Every new table has a `workspace` column.
"""

from alembic import op

from migrations import dialect

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None

COLUMNS = (
    ("question_log", "acl", "TEXT"),
    ("eval_candidates", "acl", "TEXT"),
    ("content_gaps", "acl", "TEXT"),
    ("insights_docs", "acl", "TEXT"),
    ("source_stats", "acl", "TEXT"),
    ("webhooks", "failures", "INTEGER NOT NULL DEFAULT 0"),
    ("webhooks", "disabled_reason", "TEXT"),
    ("webhooks", "disabled_at", "REAL"),
    ("webhooks", "previous_secret", "BLOB"),
    ("webhooks", "previous_secret_until", "REAL"),
)

CHUNK_CONTEXTS = """CREATE TABLE IF NOT EXISTS chunk_contexts (
        workspace   TEXT NOT NULL,
        hash        TEXT NOT NULL,
        model       TEXT NOT NULL,
        context     TEXT NOT NULL,
        created_at  REAL NOT NULL,
        PRIMARY KEY (workspace, hash))"""

TABLES = (
    """CREATE TABLE IF NOT EXISTS embedding_cache (
        workspace   TEXT NOT NULL,
        model       TEXT NOT NULL,       -- provider and model (and dimensions)
        hash        TEXT NOT NULL,       -- sha256 of the exact text embedded
        vector      BLOB NOT NULL,       -- float32
        created_at  REAL NOT NULL,
        used_at     REAL NOT NULL,
        PRIMARY KEY (workspace, model, hash))""",
    "CREATE INDEX IF NOT EXISTS embedding_cache_by_use ON embedding_cache(workspace, used_at)",
)


def upgrade():
    conn = dialect.bind()
    for table, column, kind in COLUMNS:
        if not dialect.has_column(conn, table, column):
            conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
    if not dialect.has_column(conn, "chunk_contexts", "workspace"):
        conn.exec_driver_sql("DROP TABLE IF EXISTS chunk_contexts")
    conn.exec_driver_sql(CHUNK_CONTEXTS)
    for statement in TABLES:
        conn.exec_driver_sql(statement)


def downgrade():
    conn = dialect.bind()
    conn.exec_driver_sql("DROP TABLE IF EXISTS embedding_cache")
    conn.exec_driver_sql("DROP TABLE IF EXISTS chunk_contexts")
    conn.exec_driver_sql("""CREATE TABLE IF NOT EXISTS chunk_contexts (
        hash        TEXT PRIMARY KEY,
        model       TEXT NOT NULL,
        context     TEXT NOT NULL,
        created_at  REAL NOT NULL)""")
    # question_log keeps its new column (empty for older code): copying it again
    # would drop the trigger that clears a deleted chat's words (0007, 0015).
    for table in ("eval_candidates", "content_gaps", "insights_docs", "source_stats"):
        with op.batch_alter_table(table) as batch:
            batch.drop_column("acl")
    with op.batch_alter_table("webhooks") as batch:
        for column in ("failures", "disabled_reason", "disabled_at", "previous_secret",
                       "previous_secret_until"):
            batch.drop_column(column)
