"""The workspace's assistant (kb/services/assistant.py): its settings and glossary
(admins, the Assistant screen) and its source rules (HR and admins, the Knowledge
screen's row actions). Members read the assistant's name and whether they see
its steps.

Every change is audited, and makes the workspace's agents load the settings
again on the next question (assistant_store.version). A rule that blocks, or
stops blocking, queues an incremental re-index: blocked documents' chunks are
removed from the index, unblocked ones are added back."""

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel

import assistant_store as store
import pii
from kb.audit import audited
from kb.deps import current_user, require_admin, require_hr, ws_of
from kb.services import assistant, indexing

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
