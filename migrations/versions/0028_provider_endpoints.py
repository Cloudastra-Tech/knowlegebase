"""model providers: the typed addresses in a column of their own

Revision ID: 0028
Revises: 0027
Create Date: 2026-10-30

    model_providers.endpoints   {provider: {base_url, api_version}}, JSON: the
                                addresses an admin typed for the providers that
                                take one (Azure OpenAI's endpoint and api-version,
                                an OpenAI-compatible server's base URL). Not
                                secret: shown back on the screen
                                (model_providers_store)

Until now they rode inside the SEALED `api_keys` JSON, under a reserved
"_endpoints" key, to avoid a migration. Each row's are moved out: `api_keys` is
opened (the same way secret_box does, through this migration's connection: the
workspace's data keys from workspace_keys, unwrapped by the server's key),
"_endpoints" goes to the new column, and the keys are sealed again without it,
under the same data key version (NULL when no key is left). A row without
"_endpoints" isn't rewritten.

A row that can't be opened (no server key in this process, its workspace's data
keys shredded, a damaged value) is left as it is and logged: the upgrade goes on.
Its addresses stay in the sealed JSON, which the new code doesn't read (the
admin is asked for them again). Run `alembic downgrade 0027` and upgrade again
with the key to move them later: downgrade leaves such a row alone.

Downgrade puts each row's addresses back into its sealed `api_keys` (made under
the workspace's newest data key, one made if it has none, when the row had no
keys) and drops the column. It needs the server's key whenever a row has
addresses, and stops with nothing changed without it; a row whose workspace's
data keys are gone (shredded) loses its addresses, logged.
"""

import json
import logging
import os
import time

from cryptography.fernet import Fernet, MultiFernet

from migrations import dialect

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None

OLD_KEY = "_endpoints"                 # where the addresses were in the sealed JSON
PREFIX = b"kbw1:"                      # secret_box.PREFIX: sealed under a data key
log = logging.getLogger("migrations")


class NoServerKey(RuntimeError):
    """Nothing to unwrap the data keys with in this process."""


class _Box:
    """secret_box's seal / unseal on the migration's own connection (secret_box
    itself opens connections of its own, which would wait on this transaction's
    lock, and reads the app's database rather than the one being migrated). The
    format is secret_box's: b"kbw1:<version>:" + a Fernet token, or a bare
    Fernet token under the server's key itself (from before data keys)."""

    def __init__(self, conn):
        self.conn = conn
        self._kek = None
        self._deks = {}

    def kek(self):
        import secret_box
        if self._kek is None:
            if not _server_key_available(secret_box):
                raise NoServerKey("no INTEGRATIONS_KEY, key file or KMS in this process")
            self._kek = secret_box.kek()
        return self._kek

    def deks(self, workspace):
        """{version: Fernet}, newest first."""
        if workspace not in self._deks:
            kek = self.kek()
            rows = self.conn.exec_driver_sql(
                "SELECT version, wrapped_dek FROM workspace_keys WHERE workspace = ? "
                "ORDER BY version DESC", (workspace,)).fetchall()
            self._deks[workspace] = {v: Fernet(kek.unwrap(_bytes(w))) for v, w in rows}
        return self._deks[workspace]

    def unseal(self, blob, workspace):
        """-> (value, version): version None for the old format (the server's key)."""
        import secret_box
        self.kek()                     # (never make a key file only to fail with it)
        if not blob.startswith(PREFIX):
            return json.loads(secret_box._legacy().decrypt(blob)), None
        version, token = blob[len(PREFIX):].split(b":", 1)
        keys = self.deks(workspace)
        if not keys:
            raise secret_box.Shredded(f"workspace {workspace} has no keys")
        return json.loads(MultiFernet(list(keys.values())).decrypt(token)), int(version)

    def seal(self, value, workspace, version):
        """Sealed under data key `version` (None: the old format, as it was)."""
        import secret_box
        self.kek()
        data = json.dumps(value).encode()
        if version is None:
            return secret_box._legacy().encrypt(data)
        return PREFIX + str(version).encode() + b":" + self.deks(workspace)[version].encrypt(data)

    def newest(self, workspace):
        """The workspace's newest data key version, made (version 1) if it has none."""
        keys = self.deks(workspace)
        if keys:
            return next(iter(keys))
        dek = Fernet.generate_key()
        self.conn.exec_driver_sql(
            "INSERT INTO workspace_keys (workspace, version, wrapped_dek, created_at) "
            "VALUES (?, 1, ?, ?)", (workspace, self.kek().wrap(dek), time.time()))
        self._deks[workspace] = {1: Fernet(dek)}
        return 1


def _server_key_available(secret_box):
    """A key to open the data keys with, without making one: secret_box.kek()
    would write a new key file next to the database, which opens nothing."""
    return (secret_box._kek_override is not None
            or os.getenv("KEK_PROVIDER", "env").strip().lower() == "kms"
            or bool(os.getenv("INTEGRATIONS_KEY", "").strip())
            or (not secret_box.hosted() and os.path.exists(secret_box.KEY_PATH)))


def _bytes(blob):
    return bytes(blob) if isinstance(blob, (memoryview, bytearray)) else blob


def _forget_cached_keys():
    import secret_box
    secret_box.forget()


def upgrade():
    conn = dialect.bind()
    if not dialect.has_column(conn, "model_providers", "endpoints"):
        conn.exec_driver_sql("ALTER TABLE model_providers ADD COLUMN endpoints TEXT")
    rows = conn.exec_driver_sql("SELECT workspace, api_keys FROM model_providers "
                                "WHERE api_keys IS NOT NULL AND endpoints IS NULL").fetchall()
    box = _Box(conn)
    moved = 0
    for workspace, blob in rows:
        blob = _bytes(blob)
        try:
            value, version = box.unseal(blob, workspace)
            if not isinstance(value, dict) or OLD_KEY not in value:
                continue
            endpoints = value.pop(OLD_KEY) or {}
            sealed = box.seal(value, workspace, version) if value else None
        except Exception as e:
            log.warning("0028: left workspace %s's model provider addresses in its sealed "
                        "API keys: they couldn't be opened (%s: %s)", workspace,
                        type(e).__name__, e)
            continue
        conn.exec_driver_sql(
            "UPDATE model_providers SET endpoints = ?, api_keys = ? WHERE workspace = ?",
            (json.dumps(endpoints) if endpoints else None, sealed, workspace))
        moved += 1
    if moved:
        log.warning("0028: moved %d workspace(s)' model provider addresses to "
                    "model_providers.endpoints", moved)
    _forget_cached_keys()


def downgrade():
    conn = dialect.bind()
    if not dialect.has_column(conn, "model_providers", "endpoints"):
        return
    rows = conn.exec_driver_sql("SELECT workspace, api_keys, endpoints FROM model_providers "
                                "WHERE endpoints IS NOT NULL").fetchall()
    box = _Box(conn)
    import secret_box
    for workspace, blob, endpoints in rows:
        try:
            endpoints = json.loads(endpoints)
        except ValueError:
            endpoints = None
        if not endpoints:
            continue
        blob = _bytes(blob)
        try:
            if blob:
                value, version = box.unseal(blob, workspace)
            else:
                value, version = {}, box.newest(workspace)
        except secret_box.Shredded:
            log.warning("0028 downgrade: workspace %s has no data keys (shredded): its "
                        "model provider addresses are dropped", workspace)
            continue
        value[OLD_KEY] = endpoints
        conn.exec_driver_sql("UPDATE model_providers SET api_keys = ? WHERE workspace = ?",
                             (box.seal(value, workspace, version), workspace))
    conn.exec_driver_sql("ALTER TABLE model_providers DROP COLUMN endpoints")
    _forget_cached_keys()
