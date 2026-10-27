"""
migrations/dialect.py  —  the migrations' raw SQL on either backend (db.py).

The migrations are written in SQLite's dialect, and on SQLite they run exactly
as written. On Postgres, bind() wraps the connection so each statement goes
through db_pg.translate (types, AUTOINCREMENT, `user`, INSERT OR IGNORE, ?
parameters, ...); the few steps with no translation (PRAGMA, FTS5, triggers, a
table copied to change a CHECK) have a Postgres branch in their migration,
which runs its own SQL through native().
"""

from alembic import op


def postgres(conn=None):
    conn = conn if conn is not None else op.get_bind()
    conn = getattr(conn, "native", conn)
    return conn.dialect.name == "postgresql"


class _Translating:
    """A SQLAlchemy connection whose exec_driver_sql takes SQLite SQL."""

    def __init__(self, conn):
        self.native = conn

    def exec_driver_sql(self, sql, params=None):
        import db_pg
        t = db_pg.translate(sql, placeholders=params is not None)
        if params is None:
            return self.native.exec_driver_sql(t.sql)
        return self.native.exec_driver_sql(t.sql, params)

    def __getattr__(self, name):
        return getattr(self.native, name)


def bind():
    """op.get_bind(), taking SQLite SQL on either backend."""
    conn = op.get_bind()
    return _Translating(conn) if postgres(conn) else conn


def native(conn):
    """The connection itself, for SQL in Postgres's own dialect."""
    return getattr(conn, "native", conn)


def columns(conn, table):
    """The table's column names, in order."""
    if postgres(conn):
        return [r[0] for r in native(conn).exec_driver_sql(
            "SELECT column_name FROM information_schema.columns WHERE table_schema = "
            "current_schema() AND table_name = %(t)s ORDER BY ordinal_position", {"t": table})]
    return [r[1] for r in conn.exec_driver_sql(f"PRAGMA table_info({table})")]


def has_column(conn, table, column):
    return column in columns(conn, table)


def replace_check(conn, table, column, check):
    """Postgres: give `column` this CHECK instead of the one it has (SQLite copies
    the table for that; Postgres changes it in place, rows untouched)."""
    name = f"{table}_{column}_check"
    native(conn).exec_driver_sql(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}")
    native(conn).exec_driver_sql(f"ALTER TABLE {table} ADD CONSTRAINT {name} CHECK ({check})")
