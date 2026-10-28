"""
Alembic's entry point. db.migrate() runs it with a connection already inside a
transaction (on SQLite, BEGIN IMMEDIATE); `alembic upgrade head` (from the repo
root) runs it on the app's database (DATABASE_URL, else db.DB_PATH / APP_DB) the
same way. Either way the whole upgrade is one transaction: a SQLite database from
before Alembic is brought to the baseline and marked as at it, then every newer
migration runs. On Postgres an advisory lock makes a second process wait for
the first, and row-level security is (re)applied to every tenant table after
the upgrade (migrations/postgres.py).
"""

import logging

from alembic import context

import db
from migrations import legacy

config = context.config
log = logging.getLogger("migrations")


def _dangling_references(conn):
    return len(conn.exec_driver_sql("PRAGMA foreign_key_check").fetchall())


def _upgrading():
    """True for an upgrade: db.migrate(), or `alembic upgrade`. Other commands
    (`alembic current`, `stamp`, ...) leave a pre-Alembic database as it is."""
    fn = context.get_context().opts.get("fn")
    return getattr(fn, "__name__", None) == "upgrade"


def run(connection):
    postgres = connection.dialect.name == "postgresql"
    context.configure(connection=connection, transactional_ddl=True,
                      render_as_batch=not postgres)
    with context.begin_transaction():
        if postgres:
            import db_pg
            # Foreign keys always hold on Postgres: nothing to check afterwards.
            connection.exec_driver_sql(f"SELECT pg_advisory_xact_lock({db_pg.MIGRATE_LOCK})")
            dangling = 0
        else:
            dangling = _dangling_references(connection)
            if _upgrading() and legacy.adopt(connection.connection.driver_connection):
                context.get_context().stamp(context.script, legacy.BASELINE)
        was = context.get_context().get_current_heads()
        context.run_migrations()
        now = context.get_context().get_current_heads()
        if now != was:
            log.warning("Database upgraded from %s to %s", ", ".join(was) or "empty",
                        ", ".join(now))
        if postgres:
            from migrations import postgres as pg
            if _upgrading():
                pg.compat(connection)
                if db.RLS:
                    pg.secure(connection)
        # Foreign keys are off while migrating (see db.engine); a migration that
        # left rows pointing at nothing is a bug, so undo the whole upgrade.
        elif _dangling_references(connection) > dangling:
            raise RuntimeError("a migration left rows pointing at missing rows "
                               "(PRAGMA foreign_key_check); nothing was changed")


if context.is_offline_mode():
    raise SystemExit("--sql isn't supported: migrations run against the database itself")
connection = config.attributes.get("connection")
if connection is not None:
    run(connection)
else:                                   # the alembic command line
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if not db.is_postgres():
        db.use_wal()
    with db.engine().begin() as connection:
        run(connection)
