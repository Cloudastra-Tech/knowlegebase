"""
The ingest-time personal data scan (platform.md PL-13): after each index run, the
documents that are new or changed are scanned for personal data (pii.py's rule
packs), and what was found is kept per document as COUNTS of distinct values per
type (doc_pii; never the values). The Knowledge screen then flags, for HR and
admins, the documents with dense personal data ("contains 40 phone numbers"),
so they can restrict who sees them.

Which types are flagged, and from how many values, is the workspace's policy
(pii.Policy.flagged): the scan counts every type, so changing the policy needs
no re-scan. The text scanned is the index's chunks (already extracted from
PDFs, Word files, ...): chunks may overlap, which counting distinct values
makes harmless.
"""

import json
import logging
import time

import db
import ingest
import pii

log = logging.getLogger("uvicorn.error")


def stored(ws):
    """{doc_key: {"doc_hash", "counts"}} of a workspace's scans."""
    conn = db.connect()
    try:
        return {r["doc_key"]: {"doc_hash": r["doc_hash"], "counts": json.loads(r["counts"])}
                for r in conn.execute("SELECT * FROM doc_pii WHERE workspace = ?", (ws,))}
    finally:
        conn.close()


def scan(ws, collection, now=None):
    """Scan the documents of `collection` whose content changed since their last scan,
    and forget the ones gone. Returns how many were scanned. Never raises."""
    now = time.time() if now is None else now
    try:
        col = ingest._store(collection)._collection
        metas = col.get(include=["metadatas"])["metadatas"]
        hashes = {}
        for m in metas:
            if m.get("doc_key"):
                hashes[m["doc_key"]] = m.get("doc_hash")
        have = stored(ws)
        todo = [k for k, h in hashes.items() if k not in have or have[k]["doc_hash"] != h]
        found = {}
        for start in range(0, len(todo), 50):
            batch = todo[start:start + 50]
            got = col.get(where={"doc_key": {"$in": batch}}, include=["documents", "metadatas"])
            texts = {}
            for text, meta in zip(got["documents"], got["metadatas"]):
                texts.setdefault(meta.get("doc_key"), []).append(text or "")
            for key in batch:
                found[key] = pii.count("\n".join(texts.get(key, [])))
        conn = db.connect()
        try:
            with conn:
                conn.executemany(
                    "INSERT OR REPLACE INTO doc_pii (workspace, doc_key, doc_hash, counts, "
                    "scanned_at) VALUES (?, ?, ?, ?, ?)",
                    [(ws, k, hashes[k], json.dumps(c, sort_keys=True), now)
                     for k, c in found.items()])
                gone = [k for k in have if k not in hashes]
                conn.executemany("DELETE FROM doc_pii WHERE workspace = ? AND doc_key = ?",
                                 [(ws, k) for k in gone])
        finally:
            conn.close()
        return len(found)
    except Exception:
        log.exception("PII scan: couldn't scan workspace %s", ws)
        return 0


def flags(ws, policy):
    """{doc_key: {"counts", "text"}} of the documents the policy flags."""
    out = {}
    for key, row in stored(ws).items():
        kept = policy.flagged(row["counts"])
        if kept:
            out[key] = {"counts": kept, "text": f"contains {pii.describe(kept)}"}
    return out
