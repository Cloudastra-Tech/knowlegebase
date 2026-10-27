"""
sqlite_tool.py  —  the SQLite steps of backup.sh / restore.sh (standard library only,
so it runs on the host's python3 or inside the app image).

    python3 sqlite_tool.py backup SRC DST   consistent online copy (SQLite's backup API)
    python3 sqlite_tool.py report DB        "integrity ok", then "<table> <rows>" per table
    python3 sqlite_tool.py check-key DB KEYFILE
                                            does the key decrypt a stored secret? (needs
                                            the cryptography package; exit 3 if missing)
    python3 sqlite_tool.py revision DB      the database's migration (Alembic) revision,
                                            or "none" (deploy.sh: did a deploy migrate it?)
"""

import pathlib
import sqlite3
import sys


def _open_ro(path):
    return sqlite3.connect(pathlib.Path(path).resolve().as_uri() + "?mode=ro", uri=True,
                           timeout=30)


def backup(src, dst):
    source = _open_ro(src)
    target = sqlite3.connect(dst)
    with target:
        source.backup(target)
    source.close()
    target.close()


def report(db):
    conn = _open_ro(db)
    result = conn.execute("PRAGMA integrity_check").fetchone()[0]
    if result != "ok":
        print(f"integrity FAILED {result}")
        return 1
    print("integrity ok")
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
        "ORDER BY name")]
    for table in tables:
        count = conn.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
        print(f"{table} {count}")
    conn.close()
    return 0


def check_key(db, keyfile):
    try:
        from cryptography.fernet import Fernet, InvalidToken
    except ImportError:
        print("cryptography isn't installed here: key not checked")
        return 3
    with open(keyfile, "rb") as f:
        fernet = Fernet(f.read().strip())
    conn = _open_ro(db)
    for table in ("sso_connections", "integrations"):
        try:
            row = conn.execute(f"SELECT secret FROM {table} WHERE secret IS NOT NULL "
                               "LIMIT 1").fetchone()
        except sqlite3.OperationalError:
            continue
        if row:
            try:
                fernet.decrypt(row[0])
            except InvalidToken:
                print("the key does NOT decrypt the stored secrets")
                return 1
            print("the key decrypts the stored secrets")
            return 0
    print("no stored secrets to check the key against")
    return 0


def revision(db):
    conn = _open_ro(db)
    try:
        row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
    except sqlite3.OperationalError:          # from before migrations
        row = None
    conn.close()
    print(row[0] if row else "none")
    return 0


if __name__ == "__main__":
    cmd, args = sys.argv[1], sys.argv[2:]
    if cmd == "backup":
        backup(*args)
    elif cmd == "report":
        sys.exit(report(*args))
    elif cmd == "check-key":
        sys.exit(check_key(*args))
    elif cmd == "revision":
        sys.exit(revision(*args))
    else:
        sys.exit(f"unknown command {cmd}")
