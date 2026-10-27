"""
python -m tools.migrate_sqlite_to_pg  —  copy a SQLite config/app.db into Postgres
(platform.md PL-04 step 4). The runbook is in deploy.md ("Moving to Postgres").

    python -m tools.migrate_sqlite_to_pg [--sqlite config/app.db]
        [--postgres postgresql://...] [--chroma-dir chroma_db] [--overwrite]
        [--verify-only]

--sqlite defaults to APP_DB (config/app.db), --postgres to DATABASE_URL.

1. The SQLite database must be at the newest migration (start the old version
   once, or `alembic upgrade head`): the two schemas are then the same.
2. Postgres is brought to the newest migration (its tables, row-level security).
3. Every table is copied, in an order that satisfies the foreign keys, with
   COPY. A table already in Postgres is:
       empty                       copied
       the same rows (checksum)    left as it is: running this again is safe
       different rows              refused (nothing is changed), unless
                                   --overwrite, which empties every table first
   SQLite's FTS5 chat index is copied as rows (Postgres indexes them itself);
   messages keep their rowid (the order of messages of the same moment).
4. Each identity sequence is moved past the highest id SQLite ever gave out
   (sqlite_sequence), so no id is handed out twice.
5. Verify: per table, the row count and a SHA-256 over every row (values in
   Postgres's types, rows sorted) must be the same on both sides; the exit
   status is 1 if any isn't.

--chroma-dir: also copy the Chroma search index (its vectors, text and tags,
no embedding calls) into pgvector, for VECTOR_STORE=pgvector.
--verify-only: compare only; changes nothing.

Writes must be frozen while it runs (deploy.md): it reads SQLite as it is.
"""

import argparse
import hashlib
import json
import math
import os
import sqlite3
import sys

import psycopg

# SQLite's own tables, FTS5's shadow tables and Alembic's: not copied.
SKIP_PREFIXES = ("sqlite_", "conversation_search_")
SKIP = {"alembic_version"}


def _head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    import db
    config = Config()
    config.set_main_option("script_location", db.MIGRATIONS)
    return ScriptDirectory.from_config(config).get_current_head()


def _sqlite(path):
    if not os.path.isfile(path):
        raise SystemExit(f"No SQLite database at {path}")
    import pathlib
    conn = sqlite3.connect(pathlib.Path(os.path.abspath(path)).as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _sqlite_tables(conn):
    names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
    return [n for n in names if n not in SKIP and not n.startswith(SKIP_PREFIXES)]


def _pg_columns(pg):
    """{table: {column: data type}} of Postgres's tables (generated columns left out)."""
    out = {}
    for table, column, kind in pg.execute(
            "SELECT table_name, column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND is_generated = 'NEVER' "
            "ORDER BY table_name, ordinal_position"):
        out.setdefault(table, {})[column] = kind
    return out


def _order(pg, tables):
    """`tables`, parents before the tables whose foreign keys point at them."""
    edges = {t: set() for t in tables}
    for child, parent in pg.execute(
            "SELECT c.conrelid::regclass::text, c.confrelid::regclass::text FROM pg_constraint c "
            "WHERE c.contype = 'f' AND c.connamespace = current_schema()::regnamespace"):
        if child in edges and parent in edges and child != parent:
            edges[child].add(parent)
    done, out = set(), []

    def visit(t):
        if t in done:
            return
        done.add(t)
        for p in sorted(edges[t]):
            visit(p)
        out.append(t)

    for t in sorted(tables):
        visit(t)
    return out


def _value(value, kind):
    """A SQLite value as Postgres stores it in a column of this type."""
    if value is None:
        return None
    if kind == "bytea":
        return bytes(value) if not isinstance(value, str) else value.encode()
    if kind in ("bigint", "integer", "smallint"):
        if isinstance(value, float) and not value.is_integer():
            raise ValueError(f"{value!r} isn't a whole number")
        return int(value)
    if kind == "double precision":
        return float(value)
    if kind == "text":
        return value if isinstance(value, str) else (
            value.decode() if isinstance(value, bytes) else str(value))
    return value


def _canonical(value):
    if isinstance(value, (bytes, memoryview)):
        return {"b": bytes(value).hex()}
    if isinstance(value, float):
        return {"f": "nan" if math.isnan(value) else repr(value)}
    return value


def _checksum(rows):
    """SHA-256 over every row's canonical JSON, the rows sorted."""
    lines = sorted(json.dumps([_canonical(v) for v in row], sort_keys=True,
                              ensure_ascii=False) for row in rows)
    h = hashlib.sha256()
    for line in lines:
        h.update(line.encode() + b"\n")
    return h.hexdigest()


def _source_rows(conn, table, columns, kinds):
    """The table's rows from SQLite, as Postgres will store them."""
    select = ", ".join("rowid" if c == "rowid" else f'"{c}"' for c in columns)
    rows = []
    for r in conn.execute(f'SELECT {select} FROM "{table}"'):
        rows.append(tuple(_value(r[i], kinds[c]) for i, c in enumerate(columns)))
    return rows


def _target_rows(pg, table, columns):
    select = ", ".join(f'"{c}"' for c in columns)
    return [tuple(r) for r in pg.execute(f'SELECT {select} FROM "{table}"')]


def _columns_for(sqlite_conn, table, pg_cols):
    have = [r[1] for r in sqlite_conn.execute(f'PRAGMA table_info("{table}")')]
    columns = [c for c in have if c in pg_cols]
    if table == "messages" and "rowid" in pg_cols:
        columns.append("rowid")                 # SQLite's rowid, kept (0003's Postgres branch)
    missing = [c for c in have if c not in pg_cols]
    if missing:
        raise SystemExit(f"{table}: Postgres has no column {', '.join(missing)}: are both "
                         "databases at the newest migration?")
    return columns


def _reset_sequences(sqlite_conn, pg, tables):
    seqs = {}
    try:
        seqs = {r[0]: r[1] for r in sqlite_conn.execute("SELECT name, seq FROM sqlite_sequence")}
    except sqlite3.OperationalError:
        pass
    for table, column in pg.execute(
            "SELECT table_name, column_name FROM information_schema.columns WHERE "
            "table_schema = current_schema() AND is_identity = 'YES'").fetchall():
        if table not in tables:
            continue
        top = pg.execute(f'SELECT MAX("{column}") FROM "{table}"').fetchone()[0] or 0
        top = max(top, seqs.get(table, 0) if column != "rowid" else 0)
        if top:
            pg.execute("SELECT setval(pg_get_serial_sequence(%s, %s), %s)",
                       (table, column, top))


def copy(sqlite_path, url, overwrite=False, verify_only=False, out=print):
    """Copy (or with verify_only, compare) every table. Returns {table: (source
    rows, target rows, same?)}; raises SystemExit when a table can't be copied."""
    import db

    src = _sqlite(sqlite_path)
    try:
        at = [r[0] for r in src.execute("SELECT version_num FROM alembic_version")]
    except sqlite3.OperationalError:
        at = []
    head = _head()
    if at != [head]:
        raise SystemExit(f"{sqlite_path} is at migration {', '.join(at) or 'none'}, not "
                         f"{head}: upgrade it first (start the app on it once, or "
                         "`alembic upgrade head` with APP_DB set to it).")
    if not verify_only:
        db.migrate(url=url)
    report = {}
    with psycopg.connect(url) as pg:
        kinds = _pg_columns(pg)
        tables = [t for t in _sqlite_tables(src) if t in kinds or t == "conversation_search"]
        unknown = [t for t in _sqlite_tables(src) if t not in kinds]
        if unknown:
            raise SystemExit(f"Postgres has no table {', '.join(unknown)}")
        ordered = _order(pg, tables)
        plan = {t: _columns_for(src, t, kinds[t]) for t in ordered}
        if overwrite and not verify_only:
            pg.execute("TRUNCATE " + ", ".join(f'"{t}"' for t in ordered) + " RESTART IDENTITY")
        for table in ordered:
            columns = plan[table]
            source = _source_rows(src, table, columns, kinds[table])
            target = _target_rows(pg, table, columns)
            want = _checksum(source)
            if verify_only or (target and _checksum(target) == want and
                               len(target) == len(source)):
                report[table] = (len(source), len(target), _checksum(target) == want and
                                 len(target) == len(source))
                continue
            if target:
                pg.rollback()
                raise SystemExit(f"{table}: Postgres already has {len(target)} different rows. "
                                 "Nothing was changed. Use --overwrite to replace every table "
                                 "with SQLite's rows.")
            names = ", ".join(f'"{c}"' for c in columns)
            with pg.cursor().copy(f'COPY "{table}" ({names}) FROM STDIN') as cp:
                for row in source:
                    cp.write_row(row)
            target = _target_rows(pg, table, columns)
            report[table] = (len(source), len(target),
                             _checksum(target) == want and len(target) == len(source))
        if not verify_only:
            _reset_sequences(src, pg, set(ordered))
            bad = [t for t, (_, _, same) in report.items() if not same]
            if bad:
                pg.rollback()
                raise SystemExit(f"Checksums differ after copying {', '.join(bad)}; "
                                 "nothing was changed.")
            pg.commit()
    src.close()
    for table, (n_src, n_dst, same) in sorted(report.items()):
        out(f"  {table:28} {n_src:>8} -> {n_dst:<8} {'ok' if same else 'DIFFERENT'}")
    return report


def copy_vectors(chroma_dir, out=print):
    """Every Chroma collection in `chroma_dir` into pgvector (as the same folder's
    namespace, vector_store.py). Returns {collection: chunks}."""
    import chromadb

    import vector_store
    os.environ["VECTOR_STORE"] = "pgvector"
    client = chromadb.PersistentClient(path=chroma_dir)
    done = {}
    for c in client.list_collections():
        name = getattr(c, "name", c)
        col = client.get_collection(name)
        target = vector_store.PgVectorStore(name, None, chroma_dir)._collection
        offset, n = 0, 0
        while True:
            got = col.get(include=["embeddings", "documents", "metadatas"], limit=500,
                          offset=offset)
            if not len(got["ids"]):
                break
            target.upsert(got["ids"], [list(e) for e in got["embeddings"]], got["documents"],
                          got["metadatas"])
            n += len(got["ids"])
            offset += len(got["ids"])
        same = target.count() == col.count()
        done[name] = n
        out(f"  {name:28} {n:>8} chunks {'ok' if same else 'DIFFERENT'}")
        if not same:
            raise SystemExit(f"{name}: {col.count()} chunks in Chroma, {target.count()} copied")
    return done


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m tools.migrate_sqlite_to_pg",
                                     description="Copy config/app.db into Postgres.")
    parser.add_argument("--sqlite", default=os.getenv("APP_DB", os.path.join("config", "app.db")))
    parser.add_argument("--postgres", default=os.getenv("DATABASE_URL", ""))
    parser.add_argument("--chroma-dir", help="also copy this Chroma folder into pgvector")
    parser.add_argument("--overwrite", action="store_true",
                        help="empty every Postgres table first")
    parser.add_argument("--verify-only", action="store_true", help="compare; change nothing")
    args = parser.parse_args(argv)
    if not args.postgres.startswith(("postgresql://", "postgres://")):
        parser.error("--postgres (or DATABASE_URL) must be a postgresql:// URL")
    import db
    db.DATABASE_URL = args.postgres           # what the app's modules connect to
    print(f"{'Verifying' if args.verify_only else 'Copying'} {args.sqlite} -> Postgres")
    report = copy(args.sqlite, args.postgres, args.overwrite, args.verify_only)
    if args.chroma_dir and not args.verify_only:
        print(f"Copying the search index in {args.chroma_dir} -> pgvector")
        copy_vectors(args.chroma_dir)
    bad = [t for t, (_, _, same) in report.items() if not same]
    if bad:
        print(f"DIFFERENT: {', '.join(bad)}")
        return 1
    print(f"All {len(report)} tables match.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
