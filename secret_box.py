"""
secret_box.py  —  encrypts secrets before they go into the database.

Integration tokens (Google Drive, Notion, Outline, ...), single sign-on client
secrets and SAML keys, and the Slack app's secrets and bot token are stored
ENCRYPTED, each under ITS WORKSPACE'S OWN KEY (envelope encryption, platform.md
PL-05):

    the server's key (KEK)   INTEGRATIONS_KEY, or (not in hosted mode) a key
                             generated once into config/integrations.key, or a
                             KMS (KEK_PROVIDER=kms, use_kek()). It only wraps
                             the data keys below.
    a data key (DEK)         one per workspace and version, random, kept WRAPPED
                             by the KEK in workspace_keys. seal(value, workspace)
                             encrypts with the workspace's newest one; unseal()
                             opens anything any of its versions sealed
                             (MultiFernet), so rotating never breaks a secret.

A sealed value is b"kbw1:<version>:" + a Fernet token. One from before this
(the whole server's key used directly) still opens: reseal() moves every
stored secret under its workspace's data key (the "secrets_reseal" job,
kb/services/keys.py; resumable, and each value stays readable at every step).
Deleting a workspace's data keys (shred) makes everything sealed under them
unreadable for good, backups included: crypto-shredding, the last step of
deleting a workspace.

KEY ROTATION
    rotate(workspace)   a new data key version; its secrets are sealed again
                        under it; the old version is retired (it still opens
                        anything a busy moment sealed under it, and the next
                        rotation moves that too)
    rewrap()            after a new server key: INTEGRATIONS_KEY="<new>,<old>"
                        (the first seals, all of them open), then every data key
                        is wrapped again, and old-format values re-sealed, under
                        the new one; then drop the old key from the list

Losing the server's key means losing every secret: back it up — but never in
the same place as the database, or one leaked backup decrypts everything
(scripts/backup.sh keeps them apart; deploy.md, "The encryption key"). In
HOSTED mode (APP_ENV=production and HOSTED=1) the key must come from
INTEGRATIONS_KEY or a KMS: no key file is made or read next to the database,
and the server refuses to start without one (require_key).
"""

import hashlib
import json
import logging
import os
import threading
import time

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

import db

KEY_PATH = os.path.join(os.path.dirname(db.DB_PATH) or ".", "integrations.key")
PREFIX = b"kbw1:"                  # sealed under a workspace's data key
CACHE_SECONDS = 60                 # unwrapped data keys are kept in memory this long

log = logging.getLogger("uvicorn.error")
_cache = {}                        # (kek id, workspace) -> (loaded at, keys)
_cache_lock = threading.Lock()
_kek_override = None               # use_kek(): a KMS (or a test's stand-in)

# Every column holding sealed values: (table, column, its row's key columns). Each
# such table has a `workspace` column, whose data key seals the value. A store
# that adds one registers it (register()), so reseal() and rotate() reach it.
SEALED = [("integrations", "secret", ("workspace", "provider")),
          ("sso_connections", "secret", ("id",)),
          ("slack_installs", "app_secrets", ("workspace",)),
          ("slack_installs", "bot_token", ("workspace",))]


class KeyMissing(RuntimeError):
    """No server key: in hosted mode it must be set (INTEGRATIONS_KEY or a KMS)."""


class Shredded(InvalidToken):
    """The workspace's data keys are gone (it was deleted): its secrets are, too."""


def register(table, column, keys):
    """A store keeps sealed values in table.column (rows found by `keys`)."""
    if (table, column, tuple(keys)) not in SEALED:
        SEALED.append((table, column, tuple(keys)))


# ---- The server's key (KEK) -----------------------------------------------------------
def hosted():
    """APP_ENV=production and HOSTED=1: the multi-tenant service Cloudastra runs."""
    return (os.getenv("APP_ENV", "").strip().lower() in ("production", "prod")
            and os.getenv("HOSTED", "").strip().lower() in ("1", "on", "true", "yes"))


def _env_keys():
    """The server's keys, newest first: INTEGRATIONS_KEY (comma-separated while a
    new one replaces an old one), else the key file, made the first time (never
    in hosted mode)."""
    raw = os.getenv("INTEGRATIONS_KEY", "").strip()
    if raw:
        return [k.strip().encode() for k in raw.split(",") if k.strip()]
    if hosted():
        raise KeyMissing("Hosted mode needs INTEGRATIONS_KEY (or KEK_PROVIDER=kms): "
                         "the key isn't kept in a file next to the database there.")
    if not os.path.exists(KEY_PATH):
        os.makedirs(os.path.dirname(KEY_PATH) or ".", exist_ok=True)
        with open(KEY_PATH, "wb") as f:
            f.write(Fernet.generate_key())
        try:
            os.chmod(KEY_PATH, 0o600)
        except OSError:
            pass
    with open(KEY_PATH, "rb") as f:
        return [f.read().strip()]


class EnvKek:
    """The KEK from INTEGRATIONS_KEY / the key file: Fernet, the first key wraps."""

    def __init__(self, keys):
        self._keys = MultiFernet([Fernet(k) for k in keys])
        self.id = hashlib.sha256(keys[0]).hexdigest()[:16]

    def wrap(self, dek):
        return self._keys.encrypt(dek)

    def unwrap(self, wrapped):
        return self._keys.decrypt(wrapped)

    def rewrap(self, wrapped):
        """Wrapped by the first key (unchanged if it already is)."""
        return self._keys.rotate(wrapped)


class KmsKek:
    """The interface a cloud KMS or Vault implements (KEK_PROVIDER=kms): wrap /
    unwrap a data key with a key that never leaves the KMS, and rewrap under its
    current version. Not wired to a provider yet: give use_kek() an object with
    these methods and an `id` (e.g. calling AWS KMS Encrypt / Decrypt /
    ReEncrypt with the key ARN in KMS_KEY_ID)."""

    id = "kms"

    def wrap(self, dek):
        raise NotImplementedError("KEK_PROVIDER=kms: no KMS client is configured "
                                  "(secret_box.use_kek())")

    unwrap = rewrap = wrap


def use_kek(kek):
    """Wrap data keys with `kek` (a KMS client, see KmsKek) instead; None: back to
    INTEGRATIONS_KEY. Forgets the data keys unwrapped so far."""
    global _kek_override
    _kek_override = kek
    forget()


def kek():
    if _kek_override is not None:
        return _kek_override
    if os.getenv("KEK_PROVIDER", "env").strip().lower() == "kms":
        return KmsKek()
    return EnvKek(_env_keys())


def _legacy():
    """Opens values sealed before data keys: with the server's key itself."""
    return MultiFernet([Fernet(k) for k in _env_keys()])


def require_key():
    """At start: in hosted mode, refuse to run without a server key (not a file)."""
    if not hosted() or _kek_override is not None:
        return
    if os.getenv("KEK_PROVIDER", "env").strip().lower() == "kms":
        return
    _env_keys()                    # raises KeyMissing without INTEGRATIONS_KEY
    for k in _env_keys():
        Fernet(k)                  # and a ValueError for one that isn't a key


# ---- Each workspace's data keys (DEK) ------------------------------------------------
def forget(workspace=None):
    """Drop unwrapped data keys from memory (one workspace's, or all)."""
    with _cache_lock:
        for k in [k for k in _cache if workspace is None or k[1] == workspace]:
            del _cache[k]


def _load(workspace):
    """{version: Fernet} of the workspace's data keys, newest first, from the
    database (not cached)."""
    k = kek()
    conn = db.connect()
    try:
        rows = conn.execute("SELECT version, wrapped_dek FROM workspace_keys WHERE workspace = ? "
                            "ORDER BY version DESC", (workspace,)).fetchall()
    finally:
        conn.close()
    return {r["version"]: Fernet(k.unwrap(r["wrapped_dek"])) for r in rows}


def _keys(workspace, fresh=False):
    k = kek()
    key = (k.id, workspace)
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
    if hit and not fresh and now - hit[0] < CACHE_SECONDS:
        return hit[1]
    keys = _load(workspace)
    with _cache_lock:
        _cache[key] = (now, keys)
    return keys


def ensure_key(workspace):
    """The workspace's newest data key version, made if it has none. Takes the
    write lock in its own connection: call it (or seal()) BEFORE a store opens its
    own write transaction, never inside one."""
    keys = _keys(workspace)
    if keys:
        return next(iter(keys))
    return _add_version(workspace, only_if_none=True)


def _add_version(workspace, only_if_none=False):
    wrapped = kek().wrap(Fernet.generate_key())
    conn = db.connect()
    conn.isolation_level = None
    try:
        conn.execute("BEGIN IMMEDIATE")
        top = conn.execute("SELECT MAX(version) FROM workspace_keys WHERE workspace = ?",
                           (workspace,)).fetchone()[0]
        if not (only_if_none and top):
            top = (top or 0) + 1
            conn.execute("INSERT INTO workspace_keys (workspace, version, wrapped_dek, "
                         "created_at) VALUES (?, ?, ?, ?)", (workspace, top, wrapped, time.time()))
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()
    forget(workspace)
    return top


# ---- Sealing ---------------------------------------------------------------------------
def _bytes(blob):
    return bytes(blob) if isinstance(blob, (memoryview, bytearray)) else blob


def version_of(blob):
    """The data key version a value was sealed with; None for the old format."""
    blob = _bytes(blob)
    if not blob or not blob.startswith(PREFIX):
        return None
    return int(blob[len(PREFIX):].split(b":", 1)[0])


def seal(value, workspace=None):
    """Anything JSON can hold -> encrypted bytes, under `workspace`'s newest data
    key. Without a workspace: the old format (the server's key), which reseal()
    moves later."""
    data = json.dumps(value).encode()
    if workspace is None:
        return _legacy().encrypt(data)
    version = ensure_key(workspace)
    keys = _keys(workspace)
    if version not in keys:                     # made a moment ago in another process
        keys = _keys(workspace, fresh=True)
    return PREFIX + str(version).encode() + b":" + keys[version].encrypt(data)


def unseal(blob, workspace=None):
    """Encrypted bytes from seal() -> the value, or None for no blob. A value
    sealed under a data key needs its workspace."""
    blob = _bytes(blob)
    if not blob:
        return None
    if not blob.startswith(PREFIX):
        return json.loads(_legacy().decrypt(blob))
    if workspace is None:
        raise ValueError("This secret is sealed under a workspace's key: give the workspace.")
    version, token = blob[len(PREFIX):].split(b":", 1)
    keys = _keys(workspace)
    if int(version) not in keys:
        keys = _keys(workspace, fresh=True)
    if not keys:
        raise Shredded(f"workspace {workspace} has no keys")
    return json.loads(MultiFernet(list(keys.values())).decrypt(token))


# ---- Moving secrets: re-sealing, rotation, a new server key, shredding -----------------
def _rows(table, column, keys, workspace=None):
    sql = f"SELECT workspace, {', '.join(keys)}, {column} AS blob FROM {table} " \
          f"WHERE {column} IS NOT NULL"
    args = []
    if workspace is not None:
        sql, args = sql + " AND workspace = ?", [workspace]
    conn = db.connect()
    try:
        try:
            return conn.execute(sql, args).fetchall()
        except Exception as e:                 # a registered table this database lacks
            if "no such" in str(e):
                return []
            raise
    finally:
        conn.close()


def reseal(workspace=None, below=None, progress=None):
    """Seal every stored secret (of `workspace`, or all) again under its
    workspace's newest data key, if it isn't already: the old format, or a
    version below `below` (default: the newest). One row at a time, each only if
    it still holds what was read (a change made meanwhile wins), so it can stop
    anywhere and run again. Returns {"resealed", "kept", "failed"}."""
    done = {"resealed": 0, "kept": 0, "failed": 0}
    for table, column, keys in list(SEALED):
        for row in _rows(table, column, keys, workspace):
            ws, old = row["workspace"], _bytes(row["blob"])
            try:
                current = ensure_key(ws)
                v = version_of(old)
                if v is not None and v >= (below or current):
                    done["kept"] += 1
                    continue
                new = seal(unseal(old, ws), ws)
            except Exception:
                log.exception("Keys: couldn't re-seal %s.%s of workspace %s", table, column, ws)
                done["failed"] += 1
                continue
            where = " AND ".join(f"{k} = ?" for k in keys)
            conn = db.connect()
            try:
                with conn:
                    n = conn.execute(f"UPDATE {table} SET {column} = ? WHERE {where} "
                                     f"AND {column} = ?",
                                     (new, *(row[k] for k in keys), old)).rowcount
            finally:
                conn.close()
            done["resealed" if n else "kept"] += 1
            if progress:
                progress(done)
    return done


def rotate(workspace):
    """A new data key version for the workspace; its secrets are sealed again under
    it, and the versions before it are retired. Returns {"version", ...reseal()}."""
    version = _add_version(workspace)
    result = reseal(workspace, below=version)
    conn = db.connect()
    try:
        with conn:
            conn.execute("UPDATE workspace_keys SET retired_at = ? WHERE workspace = ? "
                         "AND version < ? AND retired_at IS NULL",
                         (time.time(), workspace, version))
    finally:
        conn.close()
    return {"version": version, **result}


def rewrap():
    """Wrap every data key again under the server's first key, then re-seal the
    old-format values (sealed by the old server key itself). After this the old
    server key can be dropped. Returns {"rewrapped", ...reseal()}."""
    k = kek()
    conn = db.connect()
    try:
        rows = conn.execute("SELECT workspace, version, wrapped_dek FROM workspace_keys").fetchall()
    finally:
        conn.close()
    n = 0
    for r in rows:
        new = k.rewrap(_bytes(r["wrapped_dek"]))
        if new == _bytes(r["wrapped_dek"]):
            continue
        conn = db.connect()
        try:
            with conn:
                n += conn.execute("UPDATE workspace_keys SET wrapped_dek = ? WHERE workspace = ? "
                                  "AND version = ? AND wrapped_dek = ?",
                                  (new, r["workspace"], r["version"],
                                   r["wrapped_dek"])).rowcount
        finally:
            conn.close()
    forget()
    return {"rewrapped": n, **reseal()}


def shred(workspace):
    """Delete the workspace's data keys: whatever they sealed can never be opened
    again. Returns how many versions there were."""
    conn = db.connect()
    try:
        with conn:
            n = conn.execute("DELETE FROM workspace_keys WHERE workspace = ?",
                             (workspace,)).rowcount
    finally:
        conn.close()
    forget(workspace)
    return n


def status():
    """How the stored secrets are sealed: {"old_format", "by_workspace": {ws:
    {"version", "created_at", "sealed", "behind"}}} (no secrets)."""
    conn = db.connect()
    try:
        keys = {r["workspace"]: dict(r) for r in conn.execute(
            "SELECT workspace, MAX(version) AS version, MAX(created_at) AS created_at "
            "FROM workspace_keys GROUP BY workspace")}
    finally:
        conn.close()
    out = {"old_format": 0, "by_workspace": {}}
    for table, column, keys_ in list(SEALED):
        for row in _rows(table, column, keys_):
            v = version_of(row["blob"])
            if v is None:
                out["old_format"] += 1
                continue
            ws = out["by_workspace"].setdefault(row["workspace"], {
                **{k: keys.get(row["workspace"], {}).get(k) for k in ("version", "created_at")},
                "sealed": 0, "behind": 0})
            ws["sealed"] += 1
            ws["behind"] += int(ws["version"] is not None and v < ws["version"])
    return out


def old_format_count():
    return status()["old_format"]
