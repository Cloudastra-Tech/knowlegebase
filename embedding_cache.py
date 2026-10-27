"""
embedding_cache.py  —  a chunk's vector, kept by the hash of its text and the model
that made it (pipeline.md P0-6), so indexing never pays twice for the same text.

A full rebuild (new chunking settings, a shadow rebuild, a re-tag that re-splits)
and an edit that leaves most of a document as it was produce chunks whose text
was embedded before: their vectors come from here, and only text never seen with
this model is sent to it. Texts repeated within one batch are sent once.

    the key     (workspace, model, sha256 of the exact text embedded). `model`
                names the embedding object itself (its class, model id and
                dimensions, model_key()): another provider, model or size is
                another key, never a wrong-sized or foreign vector
    the rows    the workspace's own (embedding_cache, migration 0023): under
                row-level security like the rest, and deleted with it
    upkeep      a hit refreshes used_at; rows unused for EMBEDDING_CACHE_DAYS
                (default 30) are deleted by a daily job (kb/services/indexing.py)

EMBEDDING_CACHE=off embeds everything every time. A cache that can't be read or
written only costs the embedding calls: indexing never fails because of it.
"""

import hashlib
import logging
import os
import time

import numpy as np

import db
from kb.settings import env_int

log = logging.getLogger("uvicorn.error")

KEEP_DAYS = 30
_CHUNK = 500                      # hashes per SELECT


def enabled():
    return os.getenv("EMBEDDING_CACHE", "on").strip().lower() not in ("off", "0", "false",
                                                                       "no")


def model_key(embeddings):
    """Which model makes the vectors: the embedding object's class, its model id
    and its dimensions (models.Lazy is looked through)."""
    made = getattr(embeddings, "_get", None)
    e = made() if callable(made) else embeddings
    name = getattr(e, "model", None) or getattr(e, "model_id", None) or ""
    dims = getattr(e, "dimensions", None) or getattr(e, "dim", None) or ""
    return f"{type(e).__module__}.{type(e).__qualname__}:{name}:{dims}"


def text_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _get(workspace, model, hashes):
    """{hash: vector} of these hashes already embedded by `model`."""
    found = {}
    hashes = list(hashes)
    conn = db.connect()
    try:
        for start in range(0, len(hashes), _CHUNK):
            part = hashes[start:start + _CHUNK]
            for r in conn.execute(
                    f"SELECT hash, vector FROM embedding_cache WHERE workspace = ? AND model = ? "
                    f"AND hash IN ({','.join('?' * len(part))})", (workspace, model, *part)):
                found[r["hash"]] = np.frombuffer(bytes(r["vector"]), dtype=np.float32).tolist()
    finally:
        conn.close()
    return found


def _put(workspace, model, vectors, used, now):
    """Keep new vectors ({hash: vector}) and note the use of `used` (hashes)."""
    conn = db.connect()
    try:
        with conn:
            conn.executemany(
                "INSERT OR REPLACE INTO embedding_cache (workspace, model, hash, vector, "
                "created_at, used_at) VALUES (?, ?, ?, ?, ?, ?)",
                [(workspace, model, h, np.asarray(v, dtype=np.float32).tobytes(), now, now)
                 for h, v in vectors.items()])
            used = list(used)
            for start in range(0, len(used), _CHUNK):
                part = used[start:start + _CHUNK]
                conn.execute(f"UPDATE embedding_cache SET used_at = ? WHERE workspace = ? AND "
                             f"model = ? AND hash IN ({','.join('?' * len(part))})",
                             (now, workspace, model, *part))
    finally:
        conn.close()


def embed_documents(embeddings, texts, workspace, now=None):
    """embeddings.embed_documents(texts), with every text this model embedded
    before for the workspace taken from the cache: only the others (each once)
    are sent. Returns one vector per text, in order."""
    texts = list(texts)
    if not texts or not enabled():
        return embeddings.embed_documents(texts)
    now = time.time() if now is None else now
    workspace = workspace or ""
    model = model_key(embeddings)
    hashes = [text_hash(t) for t in texts]
    try:
        found = _get(workspace, model, set(hashes))
    except Exception:
        log.exception("Embedding cache: couldn't read it for workspace %s", workspace)
        found = {}
    todo = {}                                  # hash -> its text, each once, in order
    for h, t in zip(hashes, texts):
        if h not in found:
            todo.setdefault(h, t)
    fresh = dict(zip(todo, embeddings.embed_documents(list(todo.values())))) if todo else {}
    try:
        _put(workspace, model, fresh, set(found), now)
    except Exception:
        log.exception("Embedding cache: couldn't write it for workspace %s", workspace)
    return [found[h] if h in found else list(fresh[h]) for h in hashes]


def prune(days=None, now=None):
    """Delete vectors no index has used for `days` (EMBEDDING_CACHE_DAYS). Returns
    how many went. Every workspace's: run as the platform."""
    days = env_int("EMBEDDING_CACHE_DAYS", KEEP_DAYS) if days is None else days
    now = time.time() if now is None else now
    conn = db.connect()
    try:
        with conn:
            return conn.execute("DELETE FROM embedding_cache WHERE used_at < ?",
                                (now - days * 86400,)).rowcount
    finally:
        conn.close()
