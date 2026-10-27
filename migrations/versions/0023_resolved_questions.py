"""unanswered questions marked resolved

Revision ID: 0023
Revises: 0022
Create Date: 2026-10-27

Insights' "Not found" tab lists the questions the assistant couldn't answer;
HR marks one resolved once the documents cover it (or it needs no answer):

    question_log.resolved_at   when, or NULL: still open
    question_log.resolved_by   the member who did

Additive only: existing rows stay open.
"""

from alembic import op

from migrations import dialect

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None

COLUMNS = (
    ("question_log", "resolved_at", "REAL"),
    ("question_log", "resolved_by", "TEXT"),
)


def upgrade():
    conn = dialect.bind()
    for table, name, kind in COLUMNS:
        if not dialect.has_column(conn, table, name):
            conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")


def downgrade():
    for table, name, _ in COLUMNS:
        with op.batch_alter_table(table) as batch:
            batch.drop_column(name)
