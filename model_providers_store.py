"""
model_providers_store.py  —  a workspace's own model provider (config/app.db, the
table made by migrations/versions/0022_model_providers.py), what models.py uses for
it ("Bring your own AWS" on the Assistant screen):

    provider     default   the server's MODEL_PROVIDER (no row means the same)
                 bedrock   AWS Bedrock in the company's own account
    region       the AWS region its Bedrock runs in (us-east-1, eu-central-1, ...)
    auth         role      the server assumes role_arn, passing external_id (preferred:
                           no AWS secret is stored here)
                 keys      an IAM user's access keys, SEALED under the workspace's key
                           (secret_box); never shown again, only "saved"
    external_id  generated here for the workspace, once; the admin puts it in the
                 role's trust policy (AWS's guard against the "confused deputy":
                 another customer naming this role can't make the server assume it)
    chat_model, fast_model, embed_model, embed_dims   empty: the server's BEDROCK_*

Every change bumps the workspace's assistant settings version (assistant_store), so
its agents are built again with the new models and its cached answers aren't reused.
Only SQL and validation live here; the routes are kb/routers/assistant.py.
"""

import contextlib
import secrets
import time

import db
import models
import secret_box

secret_box.register("model_providers", "access_keys", ("workspace",))

CHOICES = ("default", "bedrock")
AUTHS = ("role", "keys")
FIELDS = ("provider", "region", "auth", "role_arn", "chat_model", "fast_model",
          "embed_model", "embed_dims", "access_key_id", "secret_access_key")
KEY_ID = r"^[A-Z0-9]{16,128}$"


class Invalid(ValueError):
    """A change was refused: the message says why, for the admin making it."""


@contextlib.contextmanager
def _db():
    conn = db.connect()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _row(conn, workspace):
    return conn.execute("SELECT * FROM model_providers WHERE workspace = ?",
                        (workspace,)).fetchone()


def new_external_id():
    """Random and unguessable, so it identifies this workspace to the customer's role."""
    return "kb-" + secrets.token_hex(16)


def ensure(workspace):
    """The workspace's row, made (with its ExternalId) the first time it's asked for."""
    with _db() as conn:
        row = _row(conn, workspace)
        if row:
            return dict(row)
        conn.execute("INSERT INTO model_providers (workspace, external_id, updated_at) "
                     "VALUES (?, ?, ?) ON CONFLICT(workspace) DO NOTHING",
                     (workspace, new_external_id(), time.time()))
        return dict(_row(conn, workspace))


def get(workspace):
    """The row as stored (access keys still sealed), or None."""
    with _db() as conn:
        row = _row(conn, workspace)
    return dict(row) if row else None


def view(workspace):
    """What the admin sees: everything but the secret (only whether keys are saved,
    and the access key id's last 4 characters)."""
    row = ensure(workspace)
    keys = _keys(workspace, row)
    return {"provider": row["provider"], "region": row["region"], "auth": row["auth"],
            "role_arn": row["role_arn"], "external_id": row["external_id"],
            "keys_saved": bool(keys), "access_key_hint": keys[0][-4:] if keys else None,
            "chat_model": row["chat_model"], "fast_model": row["fast_model"],
            "embed_model": row["embed_model"], "embed_dims": row["embed_dims"],
            "tested_at": row["tested_at"], "updated_by": row["updated_by"],
            "updated_at": row["updated_at"]}


def _keys(workspace, row):
    if not row or not row["access_keys"]:
        return None
    value = secret_box.unseal(row["access_keys"], workspace) or {}
    return (value.get("id"), value.get("secret")) if value.get("id") else None


def spec(workspace):
    """The workspace's models.Spec when it brings its own provider, else None (the
    server's). Empty model fields take the server's BEDROCK_* (or the defaults)."""
    row = get(workspace)
    if not row or row["provider"] != "bedrock":
        return None
    return _spec_of(row, _keys(workspace, row), workspace)


def _spec_of(row, keys, workspace):
    server = models.server_spec()
    base = server if server.provider == "bedrock" else models.Spec(
        chat_model=models.BEDROCK_CHAT, fast_model=models.BEDROCK_FAST,
        embed_model=models.BEDROCK_EMBED, embed_dims=models.BEDROCK_EMBED_DIMENSIONS)
    embed_model = row["embed_model"] or base.embed_model
    dims = row["embed_dims"] or (base.embed_dims if embed_model == base.embed_model
                                 else _default_dims(embed_model))
    return models.Spec(provider="bedrock", region=row["region"], auth=row["auth"],
                       role_arn=row["role_arn"], external_id=row["external_id"],
                       keys=keys if row["auth"] == "keys" else None,
                       chat_model=row["chat_model"] or base.chat_model,
                       fast_model=row["fast_model"] or base.fast_model,
                       embed_model=embed_model, embed_dims=dims, workspace=workspace)


def _default_dims(model):
    return (models.EMBED_DIMENSIONS.get(model) or (None,))[0]


# ---- Changing it ----------------------------------------------------------------------
def _text(value, limit=512):
    return " ".join(str(value or "").split())[:limit] or None


def clean(change, current):
    """`change` checked and normalised against the row as it is. Raises Invalid."""
    import re
    out = {}
    for key, value in change.items():
        if key not in FIELDS:
            raise Invalid(f"Unknown setting: {key}.")
        if key == "provider":
            if value not in CHOICES:
                raise Invalid("Choose the server's provider or AWS Bedrock.")
        elif key == "auth":
            if value not in AUTHS:
                raise Invalid("Choose a role to assume or access keys.")
        elif key == "region":
            value = _text(value, 32)
            if value and not models.REGION.match(value):
                raise Invalid("That isn't an AWS region (like us-east-1 or eu-central-1).")
        elif key == "role_arn":
            value = _text(value)
            if value and not models.ROLE_ARN.match(value):
                raise Invalid("That isn't an IAM role ARN (arn:aws:iam::123456789012:role/name).")
        elif key in ("chat_model", "fast_model", "embed_model"):
            value = _text(value, 256)
            if value and not models.MODEL_ID.match(value):
                raise Invalid("That isn't a Bedrock model id, inference profile id or ARN.")
        elif key == "embed_dims":
            if value in (None, ""):
                value = None
            else:
                try:
                    value = int(value)
                except (TypeError, ValueError):
                    raise Invalid("The embedding size is a whole number.")
                if not 64 <= value <= 8192:
                    raise Invalid("The embedding size is between 64 and 8,192.")
        elif key == "access_key_id":
            value = _text(value, 128)
            if value and not re.match(KEY_ID, value):
                raise Invalid("That isn't an AWS access key id.")
        elif key == "secret_access_key":
            value = str(value or "").strip() or None
            if value and not 16 <= len(value) <= 256:
                raise Invalid("That isn't an AWS secret access key.")
        out[key] = value
    # Known embedding models only make the sizes they make.
    model = out.get("embed_model", current.get("embed_model"))
    dims = out.get("embed_dims", current.get("embed_dims"))
    if model in models.EMBED_DIMENSIONS and dims and dims not in models.EMBED_DIMENSIONS[model]:
        sizes = ", ".join(map(str, models.EMBED_DIMENSIONS[model]))
        raise Invalid(f"{model} makes embeddings of {sizes} numbers.")
    return out


def complete(row, keys):
    """Why the row can't be used for Bedrock yet (None: it can)."""
    if row["provider"] != "bedrock":
        return None
    if not row["region"]:
        return "Choose the AWS region your Bedrock models are in."
    if row["auth"] == "role" and not row["role_arn"]:
        return "Paste the ARN of the role to assume."
    if row["auth"] == "keys" and not keys:
        return "Paste the access key id and secret access key."
    return None


def draft(workspace, change):
    """The models.Spec the row would make with `change` applied, without saving it
    (Test connection before switching). Raises Invalid."""
    current = ensure(workspace)
    change = clean(change, current)
    key_id = change.pop("access_key_id", None)
    secret = change.pop("secret_access_key", None)
    merged = {**current, **change, "provider": "bedrock"}
    keys = (key_id, secret) if key_id and secret else _keys(workspace, current)
    problem = complete(merged, keys)
    if problem:
        raise Invalid(problem)
    return _spec_of(merged, keys, workspace)


def save(workspace, change, by):
    """Apply a checked change -> (the view now, the embed name before, after).
    Switching to Bedrock needs what it needs (complete()): the admin is told what's
    missing instead of a workspace that can't answer."""
    import assistant_store
    current = ensure(workspace)
    change = clean(change, current)
    key_id = change.pop("access_key_id", None)
    secret = change.pop("secret_access_key", None)
    before = models.for_workspace(workspace).embed_name
    sealed = current["access_keys"]
    if key_id or secret:
        if not (key_id and secret):
            raise Invalid("Paste both the access key id and the secret access key.")
        sealed = secret_box.seal({"id": key_id, "secret": secret}, workspace)
    merged = {**current, **change, "access_keys": sealed}
    if merged["auth"] == "role":
        sealed = merged["access_keys"] = None      # a role needs no stored secret
    problem = complete(merged, _keys(workspace, merged))
    if problem:
        raise Invalid(problem)
    cols = [k for k in change] + ["access_keys"]
    values = [change[k] for k in change] + [sealed]
    with _db() as conn:
        conn.execute(f"UPDATE model_providers SET {', '.join(f'{c} = ?' for c in cols)}, "
                     "updated_by = ?, updated_at = ? WHERE workspace = ?",
                     (*values, by, time.time(), workspace))
        assistant_store._bump(conn, workspace, by)
    models.forget(workspace)
    after = models.for_workspace(workspace).embed_name
    return view(workspace), before, after


def mark_tested(workspace):
    with _db() as conn:
        conn.execute("UPDATE model_providers SET tested_at = ? WHERE workspace = ?",
                     (time.time(), workspace))
