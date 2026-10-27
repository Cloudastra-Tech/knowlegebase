"""
What a workspace's admins set about its assistant (assistant_store.py), as the
agent uses it:

    effective(ws)   the settings, with the outbound tools as they really are: the
                    server's ALLOW_WIKIPEDIA / ALLOW_CURRENCY are the default AND
                    the ceiling (a workspace can turn a tool off, never on when
                    the server has it off)
    Glossary        the workspace's terms: a question that uses one is searched
                    with its synonyms too (keyword and meaning search), and the
                    model is given the definitions of the terms it uses
    Rules           source rules (PL-28): boost or demote a document's chunks
                    (a multiplier on the fused score, rag.HybridRetriever), or
                    block it (left out of the index by ingest, and of results)

The agent is built with all three (kb/services/agents.py) and cached until the
workspace's settings version changes (assistant_store.version).
"""

import os
import re

import assistant_store as store
import db

UPLOADS = "uploads"                 # the provider a rule names uploaded files by


def _server_allows(name):
    """ALLOW_WIKIPEDIA / ALLOW_CURRENCY: on (the default) or off (agent._tool_allowed)."""
    return os.getenv(name, "on").strip().lower() not in ("0", "off", "false", "no")


def effective(ws):
    """The workspace's settings as the agent applies them, with what the server allows
    ({"allow_wikipedia_server", "allow_currency_server"}) and the version."""
    s = store.settings(ws)
    out = dict(s)
    for name, env in (("allow_wikipedia", "ALLOW_WIKIPEDIA"), ("allow_currency", "ALLOW_CURRENCY")):
        server = _server_allows(env)
        out[f"{name}_server"] = server
        out[name] = server and (s[name] if s[name] is not None else True)
    out["version"] = store.version(ws)
    return out


def prefs(settings):
    """What build_agent needs of the settings (effective())."""
    return {k: settings[k] for k in ("assistant_name", "tone", "instructions", "language",
                                     "allow_wikipedia", "allow_currency")}


# ---- Glossary -------------------------------------------------------------------------------
def _pattern(word):
    """`word` as a whole word or phrase, any case ("PTO" in "pto?", not in "ptosis")."""
    return re.compile(r"(?<![\w])" + r"\s+".join(map(re.escape, word.split())) + r"(?![\w])",
                      re.IGNORECASE)


class Glossary:
    """The workspace's terms, ready to match questions against."""

    def __init__(self, entries):
        self.entries = [{**e, "_names": [_pattern(n) for n in [e["term"], *e["synonyms"]]]}
                        for e in entries]

    def __bool__(self):
        return bool(self.entries)

    def matched(self, text):
        """The entries whose term or a synonym the text uses, in glossary order."""
        return [e for e in self.entries if any(p.search(text or "") for p in e["_names"])]

    def expand(self, question):
        """The question with the other names of every term it uses, for the search
        (keyword and meaning): "PTO rules" -> "PTO rules (paid time off; vacation)"."""
        extra = []
        for e in self.matched(question):
            for name in [e["term"], *e["synonyms"]]:
                if not _pattern(name).search(question) and name not in extra:
                    extra.append(name)
        return f"{question} ({'; '.join(extra)})" if extra else question

    def note(self, *texts):
        """The definitions of the terms the texts use, for the model (None: none)."""
        seen, lines = set(), []
        for text in texts:
            for e in self.matched(text):
                if e["id"] not in seen:
                    seen.add(e["id"])
                    also = f" (also called: {', '.join(e['synonyms'])})" if e["synonyms"] else ""
                    lines.append(f"- {e['term']}{also}: {e['definition']}")
        return "\n".join(lines) or None


def glossary(ws):
    return Glossary(store.glossary(ws))


# ---- Source rules ---------------------------------------------------------------------------
def provider_of(meta):
    """The provider a rule names a chunk's document by: its connector, or "uploads"."""
    return (meta or {}).get("connector") or UPLOADS


def folders_of(doc_key, parent=None):
    """The folders a document is in: every folder of its key ("public/hr/a.txt" ->
    "public", "public/hr") and, for a synced file, the folder it sits in at its
    source (source_items.parent_id)."""
    parts = (doc_key or "").split("/")[:-1]
    out = ["/".join(parts[:n]) for n in range(1, len(parts) + 1)]
    return out + ([parent] if parent else [])


def _parents(ws):
    """{doc key: the folder it sits in at its source} of a workspace's synced files."""
    conn = db.connect()
    try:
        return {f"connectors/{r['provider']}/{r['file_name']}": r["parent_id"]
                for r in conn.execute("SELECT provider, file_name, parent_id FROM source_items "
                                      "WHERE workspace = ? AND file_name IS NOT NULL AND "
                                      "parent_id IS NOT NULL", (ws,))}
    finally:
        conn.close()


SPECIFICITY = {"doc_key": 0, "folder": 1, "provider": 2}


class Rules:
    """A workspace's source rules. A document is BLOCKED if any rule that matches it
    blocks; otherwise the most specific matching rule (its own key, then its
    deepest folder, then its provider) sets its weight."""

    def __init__(self, rules, parents=None):
        self.rules = list(rules)
        self.parents = parents or {}

    def __bool__(self):
        return bool(self.rules)

    def matching(self, doc_key, provider):
        folders = folders_of(doc_key, self.parents.get(doc_key))
        found = [r for r in self.rules
                 if (r["match_type"] == "doc_key" and r["match_value"] == doc_key)
                 or (r["match_type"] == "folder" and r["match_value"] in folders)
                 or (r["match_type"] == "provider" and r["match_value"] == provider)]
        # Most specific first; among folders, the deepest.
        return sorted(found, key=lambda r: (SPECIFICITY[r["match_type"]],
                                            -len(r["match_value"])))

    def rule_for(self, doc_key, provider):
        """The rule that decides a document, or None."""
        found = self.matching(doc_key, provider)
        return next((r for r in found if r["action"] == "block"), None) or \
            (found[0] if found else None)

    def blocked(self, doc_key, meta=None):
        rule = self.rule_for(doc_key, provider_of(meta))
        return bool(rule and rule["action"] == "block")

    def weigh(self, meta):
        """(multiplier, the rule) for a chunk: 0 blocked, 1 when no rule matches."""
        rule = self.rule_for(meta.get("doc_key") or "", provider_of(meta))
        if not rule:
            return 1.0, None
        return (0.0 if rule["action"] == "block" else rule["weight"]), rule


def rules(ws):
    found = store.rules(ws)
    folders = any(r["match_type"] == "folder" for r in found)
    return Rules(found, _parents(ws) if folders else None)


def blocker(ws):
    """ingest's skip(key, meta) for a workspace: True for a blocked document. None
    when it has no block rule."""
    r = rules(ws)
    if not any(rule["action"] == "block" for rule in r.rules):
        return None
    return r.blocked


def describe(rule):
    """A rule in a few words, for the answer's steps: "boost ×1.5 (folder public/hr)"."""
    what = {"provider": "provider", "folder": "folder", "doc_key": "document"}[rule["match_type"]]
    how = "block" if rule["action"] == "block" else f"{rule['action']} ×{rule['weight']:g}"
    return f"{how} ({what} {rule['match_value']})"
