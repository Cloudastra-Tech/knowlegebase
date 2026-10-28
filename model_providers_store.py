"""
model_providers_store.py  —  a workspace's own model providers (config/app.db, the
table made by migrations/versions/0022_model_providers.py and 0025), what models.py
uses for it ("Bring your own provider" on the Assistant screen):

    chat_provider    default   the server's chat provider (no row means the same)
                     openai / bedrock / ...   one of models.REGISTRY's chat providers
    embed_provider   default   the server's embeddings provider
                     openai / bedrock / ...   one that HAS embeddings (models.REGISTRY)
    provider         the column from before the split (default / bedrock): a row
                     whose chat_provider / embed_provider are NULL means it for both.
                     Still written, bedrock when either side is (0025)
    api_keys         {provider: API key} for the providers the workspace brings its
                     own key for, SEALED under the workspace's key (secret_box);
                     never shown again, only "saved". A provider chosen here that
                     takes a key needs one: never the server's key instead
    region           the AWS region its Bedrock runs in (us-east-1, eu-central-1, ...)
    auth             role      the server assumes role_arn, passing external_id (preferred:
                               no AWS secret is stored here)
                     keys      an IAM user's access keys, SEALED like the API keys
    external_id      generated here for the workspace, once; the admin puts it in the
                     role's trust policy (AWS's guard against the "confused deputy":
                     another customer naming this role can't make the server assume it)
    chat_model, fast_model   the chat provider's models; empty: its defaults (.env).
                     chat_provider + chat_model is the workspace's DEFAULT answer
                     model; fast_model (the guards, HyDE, titles) stays on it
    chat_models      the answer models members may pick from in the chat (JSON:
                     [{provider, model}], 0026), across the chat providers the
                     workspace has credentials for. Always holds the default (a
                     model of null: its provider's default). Empty while chat is
                     on the server's: the server's one model, no choice
    embed_model, embed_dims  the embeddings provider's; empty: its defaults

A side on `default` takes the server's provider AND its models. A workspace that
brings its own AWS uses it for all of its Bedrock calls (one AWS connection per
workspace). Switching a side's provider clears that side's models (another
provider's model ids mean nothing to it) unless the change names new ones: for
chat, its enabled models too (the new default alone, unless chat_models is given).

Every change bumps the workspace's assistant settings version (assistant_store), so
its agents are built again with the new models and its cached answers aren't reused.
Only SQL and validation live here; the routes are kb/routers/assistant.py.
"""

import contextlib
import json
import re
import secrets
import time

import db
import models
import secret_box

secret_box.register("model_providers", "access_keys", ("workspace",))
secret_box.register("model_providers", "api_keys", ("workspace",))

AUTHS = ("role", "keys")
FIELDS = ("provider", "chat_provider", "embed_provider", "region", "auth", "role_arn",
          "chat_model", "fast_model", "embed_model", "embed_dims", "access_key_id",
          "secret_access_key", "api_keys", "chat_models", "default_model")
MAX_MODELS = 24                 # enabled chat models per workspace
KEY_ID = r"^[A-Z0-9]{16,128}$"
API_KEY = re.compile(r"^[\x21-\x7e]{16,512}$")      # printable, no spaces


class Invalid(ValueError):
    """A change was refused: the message says why, for the admin making it."""


def chat_choices():
    return ("default",) + models.chat_providers()


def embed_choices():
    return ("default",) + models.embedding_providers()


def _label(name):
    return models.REGISTRY[name].label if name in models.REGISTRY else name


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
    """The row as stored (keys still sealed), or None."""
    with _db() as conn:
        row = _row(conn, workspace)
    return dict(row) if row else None


def sides(row):
    """(chat provider, embeddings provider) as the row chooses them: default or a
    provider's name. A row from before the split: its `provider` for both."""
    legacy = (row or {}).get("provider") or "default"
    return ((row or {}).get("chat_provider") or legacy,
            (row or {}).get("embed_provider") or legacy)


def _default_chat_model(provider):
    p = models.REGISTRY.get(provider)
    return p.defaults().get("chat_model") if p else None


def default_model(row):
    """(provider, model) the workspace answers with unless a chat picks another,
    or None while chat is on the server's."""
    chat = sides(row)[0]
    if chat == "default":
        return None
    return chat, (row or {}).get("chat_model") or _default_chat_model(chat)


def _stored_models(row):
    try:
        listed = json.loads((row or {}).get("chat_models") or "[]")
    except ValueError:
        listed = []
    return [(e.get("provider"), e.get("model")) for e in listed if isinstance(e, dict)]


def enabled(row):
    """[(provider, model), ...]: the answer models the workspace's members may pick
    from, the default first. A stored model of null is its provider's default.
    Nothing while chat is on the server's (its one model: no choice)."""
    default = default_model(row)
    if default is None:
        return []
    out = [default]
    for provider, model in _stored_models(row):
        if provider not in models.chat_providers():
            continue                  # a provider since removed from the registry
        pair = (provider, model or _default_chat_model(provider))
        if pair[1] and pair not in out:
            out.append(pair)
    return out


def _providers_of(row):
    """Every provider the row uses: chat, embeddings and the enabled models'."""
    chat, embed = sides(row)
    return [p for p in dict.fromkeys((chat, embed, *(p for p, _ in enabled(row))))
            if p != "default"]


def legacy_provider(chat, embed):
    """What the old `provider` column says for these sides (0025)."""
    return "bedrock" if "bedrock" in (chat, embed) else "default"


def view(workspace):
    """What the admin sees: everything but the secrets (only whether keys are saved,
    and the AWS access key id's last 4 characters; nothing of an API key)."""
    row = ensure(workspace)
    keys = _keys(workspace, row)
    chat, embed = sides(row)
    return {"provider": legacy_provider(chat, embed), "chat_provider": chat,
            "embed_provider": embed, "region": row["region"], "auth": row["auth"],
            "role_arn": row["role_arn"], "external_id": row["external_id"],
            "keys_saved": bool(keys), "access_key_hint": keys[0][-4:] if keys else None,
            "api_keys_saved": {p: True for p, k in _api_keys(workspace, row).items() if k},
            # Its last 4 characters, as the provider's own console shows them.
            "api_key_hints": {p: k[-4:] for p, k in _api_keys(workspace, row).items() if k},
            "chat_models": [{**describe_model(p, m), "default": i == 0}
                            for i, (p, m) in enumerate(enabled(row))],
            "default_model": ({"provider": default_model(row)[0], "model": default_model(row)[1]}
                              if default_model(row) else None),
            "chat_model": row["chat_model"], "fast_model": row["fast_model"],
            "embed_model": row["embed_model"], "embed_dims": row["embed_dims"],
            "tested_at": row["tested_at"], "updated_by": row["updated_by"],
            "updated_at": row["updated_at"]}


def describe_model(provider, model):
    """A model as the screens show it: its provider and id, the catalogue's label,
    tier and abilities (models.CATALOGUE; a custom id's own)."""
    info = models.model_info(provider, model).describe()
    del info["id"]
    return {"provider": provider, "provider_label": _label(provider), "model": model, **info}


def _keys(workspace, row):
    if not row or not row["access_keys"]:
        return None
    value = secret_box.unseal(row["access_keys"], workspace) or {}
    return (value.get("id"), value.get("secret")) if value.get("id") else None


def _api_keys(workspace, row):
    if not row or not row.get("api_keys"):
        return {}
    return dict(secret_box.unseal(row["api_keys"], workspace) or {})


def spec(workspace):
    """The workspace's models.Spec when it brings its own provider, else None (the
    server's)."""
    row = get(workspace)
    if not row or sides(row) == ("default", "default"):
        return None
    return _spec_of(row, _keys(workspace, row), _api_keys(workspace, row), workspace)


def _spec_of(row, keys, api_keys, workspace):
    """The Spec the row makes. A side on default takes the server's provider and
    models; a chosen one takes the row's models, or that provider's defaults."""
    chat, embed = sides(row)
    server = models.server_spec() if "default" in (chat, embed) else None
    if chat == "default":
        chat_p, chat_model, fast_model = server.provider, server.chat_model, server.fast_model
    else:
        d = models.REGISTRY[chat].defaults()
        chat_p = chat
        chat_model = row["chat_model"] or d.get("chat_model")
        fast_model = row["fast_model"] or d.get("fast_model")
    if embed == "default":
        embed_p, embed_model, dims = (server.embeddings_provider, server.embed_model,
                                      server.embed_dims)
    else:
        d = models.REGISTRY[embed].defaults()
        embed_p = embed
        embed_model = row["embed_model"] or d.get("embed_model")
        dims = row["embed_dims"] or (d.get("embed_dims") if embed_model == d.get("embed_model")
                                     else _default_dims(embed_model))
    # Every provider it uses (chat, embeddings, the enabled models') has the
    # workspace's own credentials: its AWS, its API keys. Never the server's.
    used = _providers_of(row)
    aws = (dict(region=row["region"], auth=row["auth"], role_arn=row["role_arn"],
                external_id=row["external_id"], keys=keys if row["auth"] == "keys" else None)
           if "bedrock" in used else dict(region=server.region, auth=server.auth)
           if "bedrock" in (chat_p, embed_p) else {})
    own = tuple((p, api_keys.get(p)) for p in used if models.REGISTRY[p].api_key_env)
    return models.Spec(provider=chat_p, embed_provider=None if embed_p == chat_p else embed_p,
                       chat_model=chat_model, fast_model=fast_model, embed_model=embed_model,
                       embed_dims=dims, api_keys=own or None, workspace=workspace, **aws)


def bedrock_preview(workspace, row):
    """The Spec the row would make on Bedrock for both chat and embeddings, keeping
    only the model ids it holds for a side already on Bedrock: the Bedrock form's
    defaults and the IAM permissions its models need."""
    chat, embed = sides(row)
    preview = {**row, "chat_provider": "bedrock", "embed_provider": "bedrock",
               "region": row["region"] or models.server_region()}
    if chat != "bedrock":
        preview.update(chat_model=None, fast_model=None)
    if embed != "bedrock":
        preview.update(embed_model=None, embed_dims=None)
    return _spec_of(preview, None, {}, workspace)


def _default_dims(model):
    return (models.EMBED_DIMENSIONS.get(model) or (None,))[0]


# ---- Changing it ----------------------------------------------------------------------
def _text(value, limit=512):
    return " ".join(str(value or "").split())[:limit] or None


def clean(change, current):
    """`change` checked and normalised against the row as it is. Raises Invalid."""
    out = {}
    for key, value in change.items():
        if key not in FIELDS:
            raise Invalid(f"Unknown setting: {key}.")
        if key == "provider":
            if value not in ("default", "bedrock"):
                raise Invalid("Choose the server's provider or AWS Bedrock.")
        elif key == "chat_provider":
            if value not in chat_choices():
                raise Invalid(f"Choose a chat provider: {', '.join(chat_choices())}.")
        elif key == "embed_provider":
            if value not in embed_choices():
                known = models.REGISTRY.get(value)
                raise Invalid((f"{known.label} has no embeddings API. " if known else "")
                              + "Choose an embeddings provider: "
                              + ", ".join(_label(c) if c != "default" else "the server's"
                                          for c in embed_choices()) + ".")
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
                raise Invalid("That isn't a Bedrock model id, inference profile id or ARN."
                              if "bedrock" in sides({**current, **change})
                              else "That isn't a model id.")
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
        elif key == "api_keys":
            value = _clean_api_keys(value)
        elif key == "chat_models":
            if not isinstance(value, list):
                raise Invalid("The enabled models are a list.")
            value = list(dict.fromkeys(_model_ref(v) for v in value))
            if len(value) > MAX_MODELS:
                raise Invalid(f"Enable at most {MAX_MODELS} models.")
        elif key == "default_model":
            value = _model_ref(value) if value is not None else None
        out[key] = value
    # Known embedding models only make the sizes they make.
    model = out.get("embed_model", current.get("embed_model"))
    dims = out.get("embed_dims", current.get("embed_dims"))
    if model in models.EMBED_DIMENSIONS and dims and dims not in models.EMBED_DIMENSIONS[model]:
        sizes = ", ".join(map(str, models.EMBED_DIMENSIONS[model]))
        raise Invalid(f"{model} makes embeddings of {sizes} numbers.")
    return out


def _model_ref(value):
    """{provider, model} -> (provider, model id), checked. Raises Invalid."""
    if not isinstance(value, dict):
        raise Invalid("A model is given as its provider and id.")
    provider = value.get("provider")
    if provider not in models.chat_providers():
        raise Invalid(f"Choose a chat provider: {', '.join(models.chat_providers())}.")
    model = _text(value.get("model"), 256)
    if not model or not models.MODEL_ID.match(model):
        raise Invalid(f"That isn't a {_label(provider)} model id.")
    return provider, model


def _clean_api_keys(value):
    """{provider: key or empty (remove it)}: only providers that take a key."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise Invalid("API keys are given per provider.")
    out = {}
    for provider, key in value.items():
        info = models.REGISTRY.get(provider)
        if info is None or not info.api_key_env:
            raise Invalid(f"{_label(provider)} doesn't take an API key here.")
        key = str(key or "").strip() or None
        if key and not API_KEY.match(key):
            raise Invalid(f"That isn't a {info.label} API key.")
        out[provider] = key
    return out


def complete(row, keys, api_keys=None):
    """Why the row can't be used yet (None: it can)."""
    chat, embed = sides(row)
    used = _providers_of(row)
    if "bedrock" in used:
        if not row["region"]:
            return "Choose the AWS region your Bedrock models are in."
        if row["auth"] == "role" and not row["role_arn"]:
            return "Paste the ARN of the role to assume."
        if row["auth"] == "keys" and not keys:
            return "Paste the access key id and secret access key."
    for p in used:
        if models.REGISTRY[p].api_key_env and not (api_keys or {}).get(p):
            return f"Paste your {models.REGISTRY[p].label} API key."
    if embed not in ("default", "bedrock") and row.get("embed_dims"):
        return "The embedding size can only be chosen for Bedrock's models."
    return None


def _merge(workspace, current, change):
    """The row with `change` applied (not saved) -> (row, AWS keys, API keys,
    the access keys as typed, the API keys' change)."""
    change = clean(change, current)
    key_id = change.pop("access_key_id", None)
    secret = change.pop("secret_access_key", None)
    api_change = change.pop("api_keys", None) or {}
    listed = change.pop("chat_models", None)
    default = change.pop("default_model", None)
    chat, embed = sides(current)
    legacy = change.pop("provider", None)
    if legacy:
        chat = embed = legacy
    if default:
        change["chat_provider"], change["chat_model"] = default
    chat = change.pop("chat_provider", None) or chat
    embed = change.pop("embed_provider", None) or embed
    before = sides(current)
    # Another provider's model ids mean nothing to this one: back to its defaults.
    if chat != before[0]:
        change.setdefault("chat_model", None)
        change.setdefault("fast_model", None)
    if embed != before[1]:
        change.setdefault("embed_model", None)
        change.setdefault("embed_dims", None)
    merged = {**current, **change, "chat_provider": chat, "embed_provider": embed,
              "provider": legacy_provider(chat, embed)}
    # The enabled models: as given, else as they were. A provider whose key is
    # removed takes its models with it; with chat on the server's, there's no choice.
    if chat == "default" and listed:
        raise Invalid("Choose a provider of your own for answers to let people pick a "
                      "model: the server's has one.")
    # Kept as stored (a null model stays its provider's default, as .env says);
    # the default is always among them when read (enabled()). Switching the chat
    # provider without naming them starts again from its default alone, as a
    # switch did before there were several: the old provider's models (and the
    # key they'd need) go with it.
    if listed is None:
        listed = [] if chat != before[0] else _stored_models(current)
    removed = {p for p, k in api_change.items() if not k}
    listed = [] if chat == "default" else [(p, m) for p, m in listed if p not in removed]
    merged["chat_models"] = _dump_models(listed)
    api_keys = {**_api_keys(workspace, current), **api_change}
    # Only the providers in use keep a key: one left behind is a secret for nothing.
    used = _providers_of(merged)
    api_keys = {p: k for p, k in api_keys.items() if k and p in used}
    aws = (key_id, secret) if key_id and secret else _keys(workspace, current)
    return merged, aws, api_keys, (key_id, secret), api_change


def _dump_models(listed):
    return json.dumps([{"provider": p, "model": m} for p, m in listed]) if listed else None


def draft(workspace, change):
    """The models.Spec the row would make with `change` applied, without saving it
    (Test connection before switching, the re-index question), or None for the
    server's. Raises Invalid."""
    current = ensure(workspace)
    merged, aws, api_keys, _, _ = _merge(workspace, current, change)
    problem = complete(merged, aws, api_keys)
    if problem:
        raise Invalid(problem)
    if sides(merged) == ("default", "default"):
        return None
    return _spec_of(merged, aws, api_keys, workspace)


def draft_enabled(workspace, change):
    """The enabled chat models the row would have with `change` applied, without
    saving it (Test connection looks for each). Raises Invalid."""
    return enabled(_merge(workspace, ensure(workspace), change)[0])


def save(workspace, change, by):
    """Apply a checked change -> (the view now, the embed name before, after).
    A provider needs what it needs (complete()): the admin is told what's missing
    instead of a workspace that can't answer."""
    import assistant_store
    current = ensure(workspace)
    merged, _, api_keys, (key_id, secret), _ = _merge(workspace, current, change)
    before = models.for_workspace(workspace).embed_name
    sealed = current["access_keys"]
    if key_id or secret:
        if not (key_id and secret):
            raise Invalid("Paste both the access key id and the secret access key.")
        sealed = secret_box.seal({"id": key_id, "secret": secret}, workspace)
    merged["access_keys"] = sealed
    if merged["auth"] == "role":
        sealed = merged["access_keys"] = None      # a role needs no stored secret
    problem = complete(merged, _keys(workspace, merged), api_keys)
    if problem:
        raise Invalid(problem)
    merged["api_keys"] = secret_box.seal(api_keys, workspace) if api_keys else None
    cols = [k for k in merged if k in FIELDS and k not in (
        "access_key_id", "secret_access_key", "api_keys", "default_model")] + [
        "access_keys", "api_keys"]
    values = [merged[k] for k in cols]
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
