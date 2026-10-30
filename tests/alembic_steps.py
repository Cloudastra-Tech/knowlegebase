"""Step a SQLite file through the migrations one revision at a time, on its own
connection: db.migrate to a revision.

Not through db.DB_PATH and alembic.ini: a thread an earlier test left running can
call db.connect() at any moment, and with DB_PATH pointing at the file it brings it
to head between two steps (the flaky "DID NOT RAISE IntegrityError" at 0013)."""

import db


def upgrade(path, revision):
    db.migrate(path, revision=revision)


def downgrade(path, revision):
    db.migrate(path, revision=revision, downgrade=True)
