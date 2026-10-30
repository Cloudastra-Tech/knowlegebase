"""Step a SQLite file through the migrations one revision at a time, on its own
connection, as db.migrate does.

Not through db.DB_PATH and alembic.ini: a thread an earlier test left running can
call db.connect() at any moment, and with DB_PATH pointing at the file it brings it
to head between two steps (the flaky "DID NOT RAISE IntegrityError" at 0013)."""

from alembic import command
from alembic.config import Config

import db


def _run(path, fn, revision):
    config = Config()
    config.set_main_option("script_location", db.MIGRATIONS)
    db.use_wal(path)
    with db.alembic_lock, db.engine(path).begin() as connection:
        config.attributes["connection"] = connection
        fn(config, revision)


def upgrade(path, revision):
    _run(path, command.upgrade, revision)


def downgrade(path, revision):
    _run(path, command.downgrade, revision)
