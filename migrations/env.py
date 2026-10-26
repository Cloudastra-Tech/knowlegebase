"""
Alembic's entry point. db.migrate() runs it with a connection already inside a
BEGIN IMMEDIATE transaction; `alembic upgrade head` (from the repo root) runs it
on db.DB_PATH (APP_DB) the same way. Either way the whole upgrade is one
transaction: a database from before Alembic is brought to the baseline and
marked as at it, then every newer migration runs.
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
    context.configure(connection=connection, transactional_ddl=True, render_as_batch=True)
    with context.begin_transaction():
        dangling = _dangling_references(connection)
        if _upgrading() and legacy.adopt(connection.connection.driver_connection):
            context.get_context().stamp(context.script, legacy.BASELINE)
        was = context.get_context().get_current_heads()
        context.run_migrations()
        now = context.get_context().get_current_heads()
        if now != was:
            log.warning("Database upgraded from %s to %s", ", ".join(was) or "empty",
                        ", ".join(now))
        # Foreign keys are off while migrating (see db.engine); a migration that
        # left rows pointing at nothing is a bug, so undo the whole upgrade.
        if _dangling_references(connection) > dangling:
            raise RuntimeError("a migration left rows pointing at missing rows "
                               "(PRAGMA foreign_key_check); nothing was changed")


if context.is_offline_mode():
    raise SystemExit("--sql isn't supported: migrations run against the database itself")
connection = config.attributes.get("connection")
if connection is not None:
    run(connection)
else:                                   # the alembic command line
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    db.use_wal()
    with db.engine().begin() as connection:
        run(connection)
