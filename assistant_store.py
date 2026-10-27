"""
assistant_store.py  —  how a workspace's assistant answers (config/app.db, tables
made by migrations/versions/0012_verified_answers_and_assistant_settings.py):

    assistant_settings  its name, tone, extra instructions, answer language, the
                        outbound tools, the steps panel and whether a verified
                        answer may be reworded (platform.md PL-29). No row: today's
                        behaviour (DEFAULTS)
    glossary            the workspace's own terms: definition and synonyms
    doc_rules           boost, demote or block documents by provider, folder or
                        document key (PL-28)

Every change bumps the workspace's `version`, which the cached agents compare
(kb/services/agents.py), so the next question is answered with the new settings,
the way a re-index makes them load the index again.

Only SQL and validation live here; what the settings do is kb/services/assistant.py.
"""

import contextlib
import json
import re
import time

import db

TONES = ("default", "friendly", "formal", "concise", "detailed")
LANGUAGES = ("auto", "English", "Hindi", "Spanish", "French", "German", "Portuguese",
             "Italian", "Dutch", "Japanese", "Chinese", "Arabic")
NAME_CHARS = 40
INSTRUCTIONS_CHARS = 1500
DEFAULTS = {"assistant_name": None, "tone": "default", "instructions": "", "language": "auto",
            "allow_wikipedia": None, "allow_currency": None, "show_steps": True,
            "adapt_verified": False}
SETTINGS = tuple(DEFAULTS)

TERM_CHARS = 80
DEFINITION_CHARS = 500
MAX_SYNONYMS = 10
MAX_TERMS = 500

MATCH_TYPES = ("provider", "folder", "doc_key")
ACTIONS = ("boost", "demote", "block")
# What a weight may be: boost multiplies the score by more than 1, demote by less.
WEIGHTS = {"boost": (1.05, 5.0, 1.5), "demote": (0.05, 0.95, 0.5)}   # (min, max, default)
MAX_RULES = 200
VALUE_CHARS = 512


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


def _bump(conn, workspace, by=None):
    """The workspace's settings, glossary or rules changed: agents load them again."""
    conn.execute("INSERT INTO assistant_settings (workspace, version, updated_by, updated_at) "
                 "VALUES (?, 1, ?, ?) ON CONFLICT(workspace) DO UPDATE SET "
                 "version = version + 1", (workspace, by, time.time()))


def version(workspace):
    with _db() as conn:
        row = conn.execute("SELECT version FROM assistant_settings WHERE workspace = ?",
                           (workspace,)).fetchone()
    return row[0] if row else 0


# ---- Settings ---------------------------------------------------------------------------
def _tristate(value):
    return None if value is None else bool(value)


def settings(workspace):
    """The workspace's settings as stored (DEFAULTS where nothing was set), with
    updated_by and updated_at."""
    with _db() as conn:
        row = conn.execute("SELECT * FROM assistant_settings WHERE workspace = ?",
                           (workspace,)).fetchone()
    if not row:
        return {**DEFAULTS, "updated_by": None, "updated_at": None}
    return {"assistant_name": row["assistant_name"] or None, "tone": row["tone"],
            "instructions": row["instructions"] or "", "language": row["language"],
            "allow_wikipedia": _tristate(row["allow_wikipedia"]),
            "allow_currency": _tristate(row["allow_currency"]),
            "show_steps": bool(row["show_steps"]), "adapt_verified": bool(row["adapt_verified"]),
            "updated_by": row["updated_by"], "updated_at": row["updated_at"]}


def clean_settings(change):
    """`change` (any of SETTINGS) checked and normalised. Raises Invalid."""
    out = {}
    for key, value in change.items():
        if key not in SETTINGS:
            raise Invalid(f"Unknown setting: {key}.")
        if key == "assistant_name":
            value = " ".join(str(value or "").split())
            if len(value) > NAME_CHARS:
                raise Invalid(f"The assistant's name is {NAME_CHARS} characters at most.")
            value = value or None
        elif key == "tone":
            if value not in TONES:
                raise Invalid(f"The tone is one of: {', '.join(TONES)}.")
        elif key == "language":
            if value not in LANGUAGES:
                raise Invalid("Choose the answer language from the list.")
        elif key == "instructions":
            value = str(value or "").replace("\r\n", "\n").strip()
            if len(value) > INSTRUCTIONS_CHARS:
                raise Invalid(f"Extra instructions are {INSTRUCTIONS_CHARS:,} characters at most.")
        elif key in ("allow_wikipedia", "allow_currency"):
            value = _tristate(value)
        else:
            value = bool(value)
        out[key] = value
    return out


def save_settings(workspace, change, by):
    """Apply a checked change (clean_settings) -> the settings now."""
    change = clean_settings(change)
    if change:
        cols = ", ".join(change)
        values = [int(v) if isinstance(v, bool) else v for v in change.values()]
        updates = ", ".join(f"{k} = excluded.{k}" for k in change)
        with _db() as conn:
            conn.execute(f"""INSERT INTO assistant_settings (workspace, {cols}, version,
                                 updated_by, updated_at)
                             VALUES (?, {', '.join('?' * len(change))}, 1, ?, ?)
                             ON CONFLICT(workspace) DO UPDATE SET {updates},
                                 version = version + 1, updated_by = excluded.updated_by,
                                 updated_at = excluded.updated_at""",
                         (workspace, *values, by, time.time()))
    return settings(workspace)


# ---- Glossary ---------------------------------------------------------------------------
def _entry(row):
    return {"id": row["id"], "term": row["term"], "definition": row["definition"],
            "synonyms": json.loads(row["synonyms"] or "[]"), "created_by": row["created_by"],
            "updated_at": row["updated_at"]}


def glossary(workspace):
    with _db() as conn:
        return [_entry(r) for r in conn.execute(
            "SELECT * FROM glossary WHERE workspace = ? ORDER BY term COLLATE NOCASE",
            (workspace,))]


def _clean_term(term, definition, synonyms):
    term = " ".join(str(term or "").split())
    definition = " ".join(str(definition or "").split())
    if not term:
        raise Invalid("Type the term.")
    if len(term) > TERM_CHARS:
        raise Invalid(f"A term is {TERM_CHARS} characters at most.")
    if not definition:
        raise Invalid("Say what the term means.")
    if len(definition) > DEFINITION_CHARS:
        raise Invalid(f"A definition is {DEFINITION_CHARS} characters at most.")
    seen, clean = {term.casefold()}, []
    for s in synonyms or []:
        s = " ".join(str(s or "").split())
        if s and s.casefold() not in seen:
            if len(s) > TERM_CHARS:
                raise Invalid(f"A synonym is {TERM_CHARS} characters at most.")
            seen.add(s.casefold())
            clean.append(s)
    if len(clean) > MAX_SYNONYMS:
        raise Invalid(f"A term has {MAX_SYNONYMS} synonyms at most.")
    return term, definition, clean


def add_term(workspace, term, definition, synonyms, by):
    term, definition, synonyms = _clean_term(term, definition, synonyms)
    now = time.time()
    with _db() as conn:
        if conn.execute("SELECT COUNT(*) FROM glossary WHERE workspace = ?",
                        (workspace,)).fetchone()[0] >= MAX_TERMS:
            raise Invalid(f"A glossary holds {MAX_TERMS} terms at most.")
        if conn.execute("SELECT 1 FROM glossary WHERE workspace = ? AND term = ? COLLATE NOCASE",
                        (workspace, term)).fetchone():
            raise Invalid(f"“{term}” is in the glossary already.")
        cur = conn.execute("INSERT INTO glossary (workspace, term, definition, synonyms, "
                           "created_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                           (workspace, term, definition, json.dumps(synonyms), by, now, now))
        _bump(conn, workspace, by)
        return _entry(conn.execute("SELECT * FROM glossary WHERE id = ?",
                                   (cur.lastrowid,)).fetchone())


def update_term(workspace, term_id, term, definition, synonyms, by):
    """The entry changed, or None when it isn't this workspace's."""
    term, definition, synonyms = _clean_term(term, definition, synonyms)
    with _db() as conn:
        clash = conn.execute("SELECT id FROM glossary WHERE workspace = ? AND term = ? "
                             "COLLATE NOCASE AND id != ?", (workspace, term, term_id)).fetchone()
        if clash:
            raise Invalid(f"“{term}” is in the glossary already.")
        cur = conn.execute("UPDATE glossary SET term = ?, definition = ?, synonyms = ?, "
                           "updated_at = ? WHERE id = ? AND workspace = ?",
                           (term, definition, json.dumps(synonyms), time.time(), term_id,
                            workspace))
        if not cur.rowcount:
            return None
        _bump(conn, workspace, by)
        return _entry(conn.execute("SELECT * FROM glossary WHERE id = ?", (term_id,)).fetchone())


def delete_term(workspace, term_id, by):
    """The deleted entry, or None."""
    with _db() as conn:
        row = conn.execute("SELECT * FROM glossary WHERE id = ? AND workspace = ?",
                           (term_id, workspace)).fetchone()
        if not row:
            return None
        conn.execute("DELETE FROM glossary WHERE id = ?", (term_id,))
        _bump(conn, workspace, by)
        return _entry(row)


# ---- Source rules -----------------------------------------------------------------------
def _rule(row):
    return {"id": row["id"], "match_type": row["match_type"], "match_value": row["match_value"],
            "action": row["action"], "weight": row["weight"], "created_by": row["created_by"],
            "created_at": row["created_at"], "updated_at": row["updated_at"]}


def rules(workspace):
    with _db() as conn:
        return [_rule(r) for r in conn.execute(
            "SELECT * FROM doc_rules WHERE workspace = ? ORDER BY match_type, match_value",
            (workspace,))]


def get_rule(workspace, rule_id):
    with _db() as conn:
        row = conn.execute("SELECT * FROM doc_rules WHERE id = ? AND workspace = ?",
                           (rule_id, workspace)).fetchone()
    return _rule(row) if row else None


_FOLDER = re.compile(r"^[^\x00-\x1f]+$")


def clean_rule(match_type, match_value, action, weight=None):
    """(match_type, match_value, action, weight) checked. Raises Invalid."""
    if match_type not in MATCH_TYPES:
        raise Invalid(f"A rule matches one of: {', '.join(MATCH_TYPES)}.")
    if action not in ACTIONS:
        raise Invalid(f"A rule's action is one of: {', '.join(ACTIONS)}.")
    value = str(match_value or "").strip()
    if match_type == "folder":
        value = value.strip("/")
    if not value or len(value) > VALUE_CHARS or not _FOLDER.match(value):
        raise Invalid("Say which provider, folder or document the rule is for.")
    if action == "block":
        weight = 0.0
    else:
        low, high, default = WEIGHTS[action]
        weight = default if weight is None else float(weight)
        if not low <= weight <= high:
            raise Invalid(f"A {action} weight is between {low:g} and {high:g}.")
    return match_type, value, action, weight


def add_rule(workspace, match_type, match_value, action, weight, by):
    match_type, value, action, weight = clean_rule(match_type, match_value, action, weight)
    now = time.time()
    with _db() as conn:
        if conn.execute("SELECT COUNT(*) FROM doc_rules WHERE workspace = ?",
                        (workspace,)).fetchone()[0] >= MAX_RULES:
            raise Invalid(f"A workspace has {MAX_RULES} source rules at most.")
        if conn.execute("SELECT 1 FROM doc_rules WHERE workspace = ? AND match_type = ? AND "
                        "match_value = ?", (workspace, match_type, value)).fetchone():
            raise Invalid("There's a rule for that already: change it instead.")
        cur = conn.execute("INSERT INTO doc_rules (workspace, match_type, match_value, action, "
                           "weight, created_by, created_at, updated_at) "
                           "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                           (workspace, match_type, value, action, weight, by, now, now))
        _bump(conn, workspace, by)
        return _rule(conn.execute("SELECT * FROM doc_rules WHERE id = ?",
                                  (cur.lastrowid,)).fetchone())


def update_rule(workspace, rule_id, action, weight, by):
    """The rule with a new action and weight, or None when it isn't this workspace's."""
    old = get_rule(workspace, rule_id)
    if not old:
        return None
    _, _, action, weight = clean_rule(old["match_type"], old["match_value"], action, weight)
    with _db() as conn:
        conn.execute("UPDATE doc_rules SET action = ?, weight = ?, updated_at = ? "
                     "WHERE id = ? AND workspace = ?",
                     (action, weight, time.time(), rule_id, workspace))
        _bump(conn, workspace, by)
    return get_rule(workspace, rule_id)


def delete_rule(workspace, rule_id, by):
    """The deleted rule, or None."""
    old = get_rule(workspace, rule_id)
    if not old:
        return None
    with _db() as conn:
        conn.execute("DELETE FROM doc_rules WHERE id = ? AND workspace = ?", (rule_id, workspace))
        _bump(conn, workspace, by)
    return old
