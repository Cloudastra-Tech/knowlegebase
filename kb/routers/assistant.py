"""The workspace's assistant (kb/services/assistant.py): its settings and glossary
(admins, the Assistant screen) and its source rules (HR and admins, the Knowledge
screen's row actions). Members read the assistant's name and whether they see
its steps.

Every change is audited, and makes the workspace's agents load the settings
again on the next question (assistant_store.version). A rule that blocks, or
stops blocking, queues an incremental re-index: blocked documents' chunks are
removed from the index, unblocked ones are added back.

The model providers (admins; models.py, model_providers_store.py): the server's,
or the workspace's own, for chat and for embeddings apart: a provider with the
workspace's API key, or AWS Bedrock in the company's own account ("Bring your
own AWS"), with Test connection (free: each provider lists its models). Chat
can hold several providers' credentials at once, with the answer models members
may pick from in the chat (chat_models) and the default; GET /api/models is what
the picker offers (kb/services/model_choice.py). Switching the embedding provider
or model rebuilds the index (a shadow rebuild: questions keep the old one
meanwhile), so it must be confirmed."""

from dataclasses import replace

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel

import assistant_store as store
import model_providers_store
import models
import pii
import question_log_store
from kb.audit import audited
from kb.deps import current_user, require_admin, require_hr, ws_of
from kb.services import assistant, indexing, model_choice

router = APIRouter()


class SettingsIn(BaseModel):
    assistant_name: str | None = None
    tone: str | None = None
    instructions: str | None = None
    language: str | None = None
    allow_wikipedia: bool | None = None
    allow_currency: bool | None = None
    show_steps: bool | None = None
    adapt_verified: bool | None = None
    answer_cache: str | None = None          # off / exact / semantic
    cross_lingual: bool | None = None
    pii_policy: dict | None = None           # pii.py; null: back to the defaults


class ProviderIn(BaseModel):
    provider: str | None = None              # default / bedrock: both sides (as before)
    chat_provider: str | None = None         # default / openai / bedrock / ...
    embed_provider: str | None = None        # default / a provider with embeddings
    api_keys: dict[str, str | None] | None = None   # {provider: key}, write-only, sealed
    # {provider: {base_url, api_version}}: Azure's endpoint, a compatible server's URL
    endpoints: dict[str, dict[str, str | None] | None] | None = None
    region: str | None = None
    auth: str | None = None                  # role / keys
    role_arn: str | None = None
    access_key_id: str | None = None         # write-only, sealed
    secret_access_key: str | None = None     # write-only, sealed
    chat_model: str | None = None            # empty: the server's / the default
    fast_model: str | None = None
    embed_model: str | None = None
    embed_dims: int | None = None
    chat_models: list[dict[str, str]] | None = None   # [{provider, model}]: enabled for chats
    default_model: dict[str, str] | None = None       # {provider, model}: the default
    confirm_reindex: bool = False            # the admin agreed to rebuild the index


class TermIn(BaseModel):
    term: str
    definition: str
    synonyms: list[str] = []


class RuleIn(BaseModel):
    match_type: str
    match_value: str
    action: str
    weight: float | None = None


class RuleChange(BaseModel):
    action: str
    weight: float | None = None


def _settings_view(ws):
    s = assistant.effective(ws)
    return {**s, "choices": {"tones": list(store.TONES), "languages": list(store.LANGUAGES),
                             "answer_cache": list(store.ANSWER_CACHE),
                             "pii_entities": [{"id": k, "label": many[0].upper() + many[1:]}
                                              for k, (_, many) in pii.ENTITIES.items()],
                             "pii_packs": {k: list(v) for k, v in pii.PACKS.items()}},
            "limits": {"name": store.NAME_CHARS, "instructions": store.INSTRUCTIONS_CHARS,
                       "term": store.TERM_CHARS, "definition": store.DEFINITION_CHARS,
                       "synonyms": store.MAX_SYNONYMS, "pii_domains": pii.MAX_DOMAINS}}


# ---- Everyone: what the chat shows --------------------------------------------------------
@router.get("/api/assistant")
def assistant_info(user=Depends(current_user)):
    """{assistant_name, show_steps}: the chat's greeting, and whether members see the
    steps panel (HR and admins always do)."""
    s = store.settings(ws_of(user))
    return {"assistant_name": s["assistant_name"], "show_steps": s["show_steps"]}


@router.get("/api/models")
def chat_models(user=Depends(current_user)):
    """The answer models the chat's model picker offers: {models: [{provider,
    provider_label, model, label, tier, vision, tool_calls}], default: {provider,
    model}, last: the asker's last pick while it's enabled, or null}. Never a key."""
    return model_choice.available(ws_of(user), user["username"])


# ---- Settings (admins) ----------------------------------------------------------------------
@router.get("/api/admin/assistant")
def get_settings(user=Depends(require_admin)):
    """The settings as the agent applies them (the outbound tools capped by what the
    server allows), the choices, the limits and the glossary."""
    ws = ws_of(user)
    return {**_settings_view(ws), "glossary": store.glossary(ws)}


@router.put("/api/admin/assistant")
def save_settings(body: SettingsIn, user=Depends(require_admin),
                  audit=audited("assistant.settings")):
    ws = ws_of(user)
    change = body.model_dump(exclude_unset=True)
    for key in ("tone", "language", "show_steps", "adapt_verified", "answer_cache",
                "cross_lingual"):
        if key in change and change[key] is None:
            del change[key]
    try:
        store.save_settings(ws, change, user["username"])
    except store.Invalid as e:
        raise HTTPException(400, str(e))
    # What changed, without the instructions' words: their length is enough.
    details = {k: v for k, v in change.items() if k != "instructions"}
    if "instructions" in change:
        details["instructions_chars"] = len(store.settings(ws)["instructions"])
    audit.record("workspace", ws, details, user=user)
    return _settings_view(ws)


# ---- Model provider (admins) ----------------------------------------------------------------
def _provider_view(ws):
    """The provider settings (never a secret), what the server uses, and the IAM
    documents to copy: the role's trust policy (with this workspace's ExternalId)
    and the permissions its models need."""
    view = model_providers_store.view(ws)
    on = models.for_workspace(ws)
    try:
        server = models.server_spec()
    except models.ProviderError:
        server = None
    row = model_providers_store.ensure(ws)
    spec = model_providers_store.bedrock_preview(ws, row)
    on_bedrock = [m for p, m in model_providers_store.enabled(row) if p == "bedrock"]
    return {**view,
            "effective": {"provider": on.provider, "embed_provider": on.embed_provider,
                          "chat_model": on.answer_name, "fast_model": on.fast_name,
                          "embed_model": on.embed_name},
            "server": {"provider": server.provider if server else None,
                       "chat_provider": server.provider if server else None,
                       "embed_provider": server.embeddings_provider if server else None,
                       "region": models.server_region()},
            "providers": [p.describe() for p in models.REGISTRY.values()],
            # The curated chat models per provider (an admin can add any other id).
            "catalogue": {name: [m.describe() for m in models.CATALOGUE.get(name, ())]
                          for name in models.chat_providers()},
            "max_models": model_providers_store.MAX_MODELS,
            "provider_defaults": {name: p.defaults() for name, p in models.REGISTRY.items()},
            "defaults": {"chat_model": spec.chat_model, "fast_model": spec.fast_model,
                         "embed_model": spec.embed_model, "embed_dims": spec.embed_dims},
            "embed_dimensions": {k: list(v) for k, v in models.EMBED_DIMENSIONS.items()},
            "index_embeddings": indexing.embeddings_of(ws),
            "trust_policy": models.trust_policy(view["external_id"]),
            "permissions_policy": models.permissions_policy(spec, on_bedrock)}


def _provider_details(change, view):
    """What the audit log keeps of a change: never a key, only that keys changed."""
    out = {k: v for k, v in change.items()
           if k not in ("access_key_id", "secret_access_key", "confirm_reindex", "api_keys")}
    if change.get("access_key_id") or change.get("secret_access_key"):
        out["access_keys"] = "replaced"
        out["access_key_hint"] = view.get("access_key_hint")
    if change.get("api_keys"):
        out["api_keys"] = {p: "replaced" if k else "removed"
                           for p, k in change["api_keys"].items()}
    if "chat_models" in change:
        out["chat_models"] = [m["model"] for m in view.get("chat_models") or []]
    return out


@router.get("/api/admin/assistant/models")
def get_provider(user=Depends(require_admin)):
    return _provider_view(ws_of(user))


@router.put("/api/admin/assistant/models")
def save_provider(body: ProviderIn, user=Depends(require_admin),
                  audit=audited("model_provider.update")):
    """Save the provider. A change of embedding model (another provider's, or
    another Bedrock model or size) needs confirm_reindex: the workspace's index is
    rebuilt with it, while questions are answered from the current one."""
    ws = ws_of(user)
    change = body.model_dump(exclude_unset=True)
    confirmed = change.pop("confirm_reindex", False)
    try:
        if not confirmed:
            # Would this switch the embeddings? Then the admin is asked first.
            before = models.for_workspace(ws).embed_name
            draft = model_providers_store.draft(ws, change)
            after = models.Models(draft or models.server_spec()).embed_name
            if indexing.embeddings_of(ws) is not None and after != before:
                raise HTTPException(409, "This changes the embedding model: the workspace's "
                                         "index will be rebuilt. Confirm to go ahead.")
        view, before, after = model_providers_store.save(ws, change, user["username"])
    except (model_providers_store.Invalid, models.ProviderError) as e:
        raise HTTPException(400, str(e))
    reindex = before != after and indexing.embeddings_of(ws) is not None
    if reindex:
        question_log_store.clear_embeddings(ws)    # Insights' vectors: the old model's
        indexing.schedule_index(ws)               # another FINGERPRINT: a shadow rebuild
    audit.record("workspace", ws, {**_provider_details(change, view),
                                   **({"reindex": True, "embeddings": after}
                                      if reindex else {})}, user=user)
    return {**_provider_view(ws), "reindex": reindex}


@router.post("/api/admin/assistant/models/test")
def test_provider(body: ProviderIn, user=Depends(require_admin),
                  audit=audited("model_provider.test")):
    """Test connection, with the settings as typed (saved or not), so a role or a
    key can be checked before switching to it. Each provider in use (chat,
    embeddings, the enabled models') lists its models for free (OpenAI,
    Anthropic, Gemini, OpenAI-compatible), or Bedrock / Azure OpenAI answers one
    short reply and makes one short embedding
    (models.test_connection). {"ok", "providers": [{provider, label, ok, models,
    missing, checked?, error?}]}, or {"ok": false, "error"} when the
    settings can't be tried. Without chat_provider / embed_provider / default_model
    it tests the providers saved, or Bedrock for both when the workspace is on the server's
    (the Bring your own AWS form, as before)."""
    ws = ws_of(user)
    change = body.model_dump(exclude_unset=True)
    change.pop("confirm_reindex", None)
    legacy = change.pop("provider", None)
    if not {"chat_provider", "embed_provider", "default_model"} & change.keys():
        saved = model_providers_store.sides(model_providers_store.ensure(ws))
        if legacy == "bedrock" or saved == ("default", "default"):
            change.update(chat_provider="bedrock", embed_provider="bedrock")
    try:
        spec = (model_providers_store.draft(ws, change)
                or replace(models.server_spec(), workspace=ws))
        wanted = model_providers_store.draft_enabled(ws, change)
    except (model_providers_store.Invalid, models.ProviderError) as e:
        raise HTTPException(400, str(e))
    on = models.Models(spec)
    wanted = wanted or [(on.provider, on.answer_name)]
    try:
        result = models.test_connection(spec, wanted)
        if result["ok"]:
            model_providers_store.mark_tested(ws)
    except models.ProviderError as e:
        result = {"ok": False, "error": str(e)[:500], "providers": []}
    finally:
        models.forget(ws)                  # nothing typed here stays in a cached client
    failed = "; ".join(f"{r['label']}: {r['error']}" for r in result["providers"]
                       if not r["ok"])
    audit.record("workspace", ws, {"ok": result["ok"], "provider": spec.provider,
                                   "embed_provider": spec.embeddings_provider,
                                   "region": spec.region,
                                   "auth": spec.auth, "chat_model": on.answer_name,
                                   "embed_model": spec.embed_model,
                                   "providers": [r["provider"] for r in result["providers"]],
                                   **({} if result["ok"] else
                                      {"error": (result.get("error") or failed)[:200]})},
                 user=user)
    return result


# ---- Glossary (admins) ----------------------------------------------------------------------
@router.post("/api/admin/glossary")
def add_term(body: TermIn, user=Depends(require_admin), audit=audited("glossary.create")):
    ws = ws_of(user)
    try:
        entry = store.add_term(ws, body.term, body.definition, body.synonyms, user["username"])
    except store.Invalid as e:
        raise HTTPException(400, str(e))
    audit.record("glossary", entry["id"], {"term": entry["term"],
                                           "synonyms": len(entry["synonyms"])}, user=user)
    return entry


@router.patch("/api/admin/glossary/{term_id}")
def update_term(body: TermIn, term_id: int = Path(ge=1), user=Depends(require_admin),
                audit=audited("glossary.update")):
    ws = ws_of(user)
    try:
        entry = store.update_term(ws, term_id, body.term, body.definition, body.synonyms,
                                  user["username"])
    except store.Invalid as e:
        raise HTTPException(400, str(e))
    if not entry:
        raise HTTPException(404, "That term isn't in the glossary any more.")
    audit.record("glossary", term_id, {"term": entry["term"],
                                       "synonyms": len(entry["synonyms"])}, user=user)
    return entry


@router.delete("/api/admin/glossary/{term_id}")
def delete_term(term_id: int = Path(ge=1), user=Depends(require_admin),
                audit=audited("glossary.delete")):
    ws = ws_of(user)
    entry = store.delete_term(ws, term_id, user["username"])
    if not entry:
        raise HTTPException(404, "That term isn't in the glossary any more.")
    audit.record("glossary", term_id, {"term": entry["term"]}, user=user)
    return {"ok": True}


# ---- Source rules (HR and admins) -----------------------------------------------------------
def _reindex(ws, rule):
    """A block rule was added, removed or changed: bring the index in line with it
    (incrementally: blocked documents' chunks go, unblocked ones come back)."""
    docs = [rule["match_value"]] if rule["match_type"] == "doc_key" else None
    indexing.schedule_index(ws, docs=docs)


def _rule_details(rule, before=None):
    out = {k: rule[k] for k in ("match_type", "match_value", "action", "weight")}
    if before:
        out.update(action_was=before["action"], weight_was=before["weight"])
    return out


@router.get("/api/admin/doc-rules")
def list_rules(user=Depends(require_hr)):
    return {"rules": store.rules(ws_of(user)),
            "weights": {a: {"min": lo, "max": hi, "default": d}
                        for a, (lo, hi, d) in store.WEIGHTS.items()}}


@router.post("/api/admin/doc-rules")
def add_rule(body: RuleIn, user=Depends(require_hr), audit=audited("doc_rule.create")):
    ws = ws_of(user)
    try:
        rule = store.add_rule(ws, body.match_type, body.match_value, body.action, body.weight,
                              user["username"])
    except store.Invalid as e:
        raise HTTPException(400, str(e))
    if rule["action"] == "block":
        _reindex(ws, rule)
    audit.record("doc_rule", rule["id"], _rule_details(rule), user=user)
    return rule


@router.patch("/api/admin/doc-rules/{rule_id}")
def change_rule(body: RuleChange, rule_id: int = Path(ge=1), user=Depends(require_hr),
                audit=audited("doc_rule.update")):
    ws = ws_of(user)
    before = store.get_rule(ws, rule_id)
    try:
        rule = store.update_rule(ws, rule_id, body.action, body.weight, user["username"])
    except store.Invalid as e:
        raise HTTPException(400, str(e))
    if not rule:
        raise HTTPException(404, "That rule isn't there any more.")
    if "block" in (before["action"], rule["action"]) and before["action"] != rule["action"]:
        _reindex(ws, rule)
    audit.record("doc_rule", rule_id, _rule_details(rule, before), user=user)
    return rule


@router.delete("/api/admin/doc-rules/{rule_id}")
def delete_rule(rule_id: int = Path(ge=1), user=Depends(require_hr),
                audit=audited("doc_rule.delete")):
    ws = ws_of(user)
    rule = store.delete_rule(ws, rule_id, user["username"])
    if not rule:
        raise HTTPException(404, "That rule isn't there any more.")
    if rule["action"] == "block":
        _reindex(ws, rule)
    audit.record("doc_rule", rule_id, _rule_details(rule), user=user)
    return {"ok": True}
