"""model providers: a provider for chat and one for embeddings, and API keys

Revision ID: 0025
Revises: 0024
Create Date: 2026-10-28

    model_providers.chat_provider    the workspace's chat provider: default (the
                                     server's), or one of models.REGISTRY's
                                     (openai, bedrock, ...). NULL: what
                                     `provider` says, as every row before this
    model_providers.embed_provider   its embeddings provider, the same way. A
                                     provider without embeddings is refused by
                                     model_providers_store, not here
    model_providers.api_keys         {provider: API key}, sealed under the
                                     workspace's key (secret_box), for the
                                     providers it brings its own key for

`provider` (CHECK default / bedrock) is kept and still written: bedrock when
either side is on Bedrock, so older code would keep the workspace's text in its
AWS rather than send it elsewhere. Additive only: existing rows keep their
meaning (NULL columns read as `provider`).
"""

from migrations import dialect

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None

COLUMNS = (
    ("model_providers", "chat_provider", "TEXT"),
    ("model_providers", "embed_provider", "TEXT"),
    ("model_providers", "api_keys", "BLOB"),
)


def upgrade():
    conn = dialect.bind()
    for table, column, kind in COLUMNS:
        if not dialect.has_column(conn, table, column):
            conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")


def downgrade():
    # The columns stay (empty for older code, which reads `provider`): dropping
    # them would throw away the workspace's sealed API keys.
    pass
