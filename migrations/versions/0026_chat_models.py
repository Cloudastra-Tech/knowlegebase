"""chat models: several per workspace, and each chat's pick

Revision ID: 0026
Revises: 0025
Create Date: 2026-10-28

    model_providers.chat_models   the answer models the workspace's members may
                                  pick from in the chat: JSON [{provider, model}]
                                  (model_providers_store.enabled). The default
                                  stays chat_provider + chat_model, and is always
                                  one of them. A model of null: the provider's
                                  default (.env), as chat_model NULL always meant
    conversations.model_provider  the chat's pick (kb/services/model_choice.py):
    conversations.model_id        provider and model id. NULL: the workspace's
                                  default, as every chat before this

Existing rows keep their meaning: a workspace on its own chat provider gets that
provider's model as its one enabled model (and the default); one on the server's
gets none (the server's model, no choice). Additive only.

Downgrade drops the three columns (ALTER TABLE ... DROP COLUMN, which keeps the
tables in place: copying `conversations` would cascade-delete its messages). The
default (chat_provider + chat_model) is left as it is, so older code answers with
it; the other enabled models and each chat's pick are forgotten.
"""

import json

from migrations import dialect

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None

COLUMNS = (
    ("model_providers", "chat_models", "TEXT"),
    ("conversations", "model_provider", "TEXT"),
    ("conversations", "model_id", "TEXT"),
)


def upgrade():
    conn = dialect.bind()
    for table, column, kind in COLUMNS:
        if not dialect.has_column(conn, table, column):
            conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
    # A row on its own chat provider (chat_provider, or `provider` from before
    # 0025): that provider's model is its one enabled model.
    rows = conn.exec_driver_sql(
        "SELECT workspace, provider, chat_provider, chat_model FROM model_providers "
        "WHERE chat_models IS NULL").fetchall()
    for workspace, provider, chat_provider, chat_model in rows:
        chat = chat_provider or provider or "default"
        if chat == "default":
            continue
        conn.exec_driver_sql("UPDATE model_providers SET chat_models = ? WHERE workspace = ?",
                             (json.dumps([{"provider": chat, "model": chat_model}]), workspace))


def downgrade():
    conn = dialect.bind()
    for table, column, _ in reversed(COLUMNS):
        if dialect.has_column(conn, table, column):
            conn.exec_driver_sql(f"ALTER TABLE {table} DROP COLUMN {column}")
