"""
The answer cache (pipeline.md P2-2): the same question asked again within a few
minutes is answered from the answer given before, in under 100 ms and with no
model call, but ONLY to someone with exactly the same access.

THE KEY. An answer depends on more than its question: on who asks (what their
principals let the search read, and their role: the staff-pay guards, the live
Outline wiki), the index, the workspace's settings, glossary, source rules and
personal data policy, the verified answers, the space, and the release (prompts,
models). All of it goes into the key:

    fingerprint   sha256 of the asker's principals (access.Viewer, sorted) and role
    partition     sha256 of: workspace, fingerprint, index (collection:version),
                  settings version, verified answers' stamp, space, release
    key           sha256 of: partition, the question normalised (Unicode, case,
                  spacing, end punctuation)

So a cached answer is found again only by an asker whose principal set is the
SAME (a member's set includes themselves, so in practice: the same person, or a
Slack channel answered as "public"), and only while the index, settings and
verified answers are unchanged: a re-index, a settings change or a verified answer
added, edited or removed changes the key, so every older answer becomes
unreachable at once (and is deleted on the next write). Nothing has to be
told: it holds across processes (a worker re-indexing in another one).

WHAT IS CACHED. Only a finished, fully checked answer (answered or partial) to a
question asked without history (never a follow-up: its meaning depends on the
conversation), written from the company's documents only (no live tool: the
Outline wiki, exchange rates and Wikipedia change by themselves), with no figure
the faithfulness check couldn't find, and not a verified answer (served fresh,
it is cheap anyway). It's kept ANSWER_CACHE_TTL seconds (default 600).

EXACT and SEMANTIC (assistant settings, answer_cache: off / exact / semantic).
Exact matches the normalised question. Semantic, off by default, also takes the
closest question asked in the same partition when its embedding is at least
ANSWER_CACHE_SIMILARITY (default 0.95) alike: one embedding call per question.

A hit is logged in the question log like any answer, with cache_hit set and no
tokens, and its steps start with an `answer_cache` step that admins see.
"""

import hashlib
import json
import logging
import os
import re
import time
import unicodedata
from dataclasses import dataclass

import numpy as np

import access
import answer_cache_store as store
import assistant_store
import ingest
import models
import verified_store
from kb.services import assistant, indexing
from kb.settings import env_int

log = logging.getLogger("uvicorn.error")

TTL = env_int("ANSWER_CACHE_TTL", 600)
KEEP = env_int("ANSWER_CACHE_MAX_ROWS", 2000)        # per workspace
STATES = ("answered", "partial")
# Tools whose results only change with the index (or never): an answer that used
# anything else (the live wiki, rates, Wikipedia) isn't kept.
TOOLS = {"search_docs", "calculator", "source_rules"}
FORMAT = "1"                                          # bump when what's stored changes


def _similarity():
    try:
        return float(os.getenv("ANSWER_CACHE_SIMILARITY", "") or 0.95)
    except ValueError:
        return 0.95


def _release():
    """What changes the answers of a release: its version, the models, the format."""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "version.txt")
    try:
        with open(path, encoding="utf-8") as f:
            version = f.read().strip()
    except OSError:
        version = "?"
    return f"{version}|{models.answer_model()}|{models.fast_model()}|{FORMAT}"


def _hash(*parts):
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()


def fingerprint(who):
    """A stable hash of an asker's principal set and role (a bare role: its own)."""
    viewer = access.of(who)
    return _hash("principals", viewer.role, sorted(viewer))


_END = re.compile(r"[\s?!.。？！¿¡,;:]+$")


def normalize(question):
    """The question as the key sees it: NFKC, case-folded, single spaces, no end
    punctuation ("What's the refund policy?" = "what's the refund  policy")."""
    text = " ".join(unicodedata.normalize("NFKC", question or "").casefold().split())
    return _END.sub("", text.lstrip("¿¡"))


def mode(ws):
    """off / exact / semantic: the workspace's setting, under the server's ceiling."""
    if not assistant.answer_cache_server():
        return "off"
    return assistant_store.settings(ws)["answer_cache"]


@dataclass
class Scope:
    """Everything a cached answer depends on but its question, for one asker."""
    ws: str
    mode: str
    fingerprint: str
    index_version: str
    settings_version: int
    verified_stamp: str
    partition: str

    def key(self, question):
        return _hash(self.partition, normalize(question))


def scope(ws, who, space=None):
    """The cache as `who` may use it in `ws`, or None when it's off there."""
    how = mode(ws)
    if how == "off":
        return None
    collection, version = indexing.current(ws)
    index_version = f"{collection}:{version}"
    settings_version = assistant_store.version(ws)
    stamp = json.dumps(list(verified_store.stamp(ws)))
    fp = fingerprint(who)
    part = _hash("partition", ws, fp, index_version, settings_version, stamp, space or "",
                 _release())
    return Scope(ws, how, fp, index_version, settings_version, stamp, part)


def _embed(text):
    from langchain_openai import OpenAIEmbeddings
    vector = np.asarray(OpenAIEmbeddings(model=ingest.EMBED_MODEL).embed_query(text),
                        dtype=np.float32)
    return vector / max(float(np.linalg.norm(vector)), 1e-12)


@dataclass
class Hit:
    kind: str                     # exact / semantic
    row: dict
    score: float = 1.0


def lookup(sc, question, now=None):
    """The cached answer for the question in this scope, or None. Never raises."""
    if sc is None:
        return None
    try:
        row = store.get(sc.ws, sc.key(question), now)
        if row:
            store.hit(row["id"])
            return Hit("exact", row)
        if sc.mode != "semantic":
            return None
        rows = store.partition(sc.ws, sc.partition, now)
        if not rows:
            return None
        q = _embed(normalize(question))
        best, score = None, -1.0
        for r in rows:
            s = float(np.frombuffer(r["embedding"], dtype=np.float32) @ q)
            if s > score:
                best, score = r, s
        if best is not None and score >= _similarity():
            store.hit(best["id"])
            return Hit("semantic", best, score)
    except Exception:
        log.exception("Answer cache: lookup failed in workspace %s; answering as usual", sc.ws)
    return None


def cacheable(done, out):
    """Whether a finished answer (answering.Answer) may be kept (see the top)."""
    tools = {t.get("tool") for t in done.trace or []}
    return (done.state in STATES and not done.verified and tools <= TOOLS
            and not (out or {}).get("unsupported") and not (out or {}).get("blocked"))


def save(sc, question, done, out, username=None, now=None):
    """Keep a finished answer for its scope, if it may be kept. Never raises."""
    if sc is None or not cacheable(done, out):
        return False
    try:
        embedding = _embed(normalize(question)).tobytes() if sc.mode == "semantic" else None
        store.put(sc.ws, {
            "key": sc.key(question), "partition": sc.partition, "fingerprint": sc.fingerprint,
            "username": username, "index_version": sc.index_version,
            "settings_version": sc.settings_version, "verified_stamp": sc.verified_stamp,
            "question": normalize(question), "embedding": embedding, "answer": done.answer,
            "sources": done.sources, "state": done.state, "max_access": done.max_access,
            "acl": done.acl, "trace": done.trace,
            "expires_at": (time.time() if now is None else now) + TTL}, keep=KEEP, now=now)
        return True
    except Exception:
        log.exception("Answer cache: couldn't keep an answer in workspace %s", sc.ws)
        return False


def step(hit, now=None):
    """The answer_cache step admins see first in a cached answer's steps."""
    now = time.time() if now is None else now
    age = max(0, int(now - hit.row["created_at"]))
    how = "the same question" if hit.kind == "exact" else \
        f"a very similar question (similarity {hit.score:.2f})"
    return {"tool": "answer_cache", "input": {"kind": hit.kind},
            "output": f"Served from the answer cache: {how} was answered {age} s ago for "
                      "the same access, with the index and settings unchanged."}


def clear(ws):
    return store.clear(ws)
