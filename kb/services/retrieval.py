"""
The search index without the answer model: what the public API's /search and the
MCP server's search_knowledge and read_document return.

  search()         the passages a query finds, best first, by the same hybrid
                   search the chat's search_docs runs (rag.HybridRetriever),
                   without HyDE and without writing an answer: no LLM cost
                   beyond the question rules' own check.
  read_document()  a whole document as the index holds it, pieced together
                   from its chunks.

Both only ever see chunks the role may see: the retriever is built for the role
(rag.load_chain filters both searches), and every chunk is checked again here.
A document with any chunk the role may not see is not there for it at all.
"""

import ingest
import safety
from kb.services import agents, indexing
from rag import ROLE_ACCESS, passage_of, passage_text

MAX_K = 20
READ_MAX_CHARS = 60_000


class Refused(Exception):
    """The question rules refused the query (safety.check_question): the message says why."""


def search(ws, role, query, k=5):
    """[{doc_id, doc_key, chunk_id, title, provider, url, access, page,
    heading_path, modified, text, rank}] for `query`, at most k (1..MAX_K)."""
    allowed = ROLE_ACCESS.get(role, ["public"])
    ok, reason = safety.check_question(query, "restricted" in allowed)
    if not ok:
        raise Refused(reason)
    k = max(1, min(MAX_K, int(k)))
    agent = agents.get_agent(ws, role)
    expand = getattr(agent, "expand", None)          # the workspace's glossary
    found = []
    for doc in agent.retriever.invoke(expand(query) if expand else query, k=k):
        meta = doc.metadata
        if meta.get("access", "public") not in allowed:     # never, but never shown either
            continue
        passage = passage_of(meta)
        found.append({**{f: passage[f] for f in ("doc_id", "chunk_id", "title", "provider",
                                                  "url", "access", "page", "heading_path",
                                                  "modified")},
                      "doc_key": meta.get("doc_key"), "text": passage_text(doc.page_content, meta),
                      "rank": meta.get("rank")})
    return found


def read_document(ws, role, doc):
    """{doc_id, doc_key, title, provider, url, access, modified, text, truncated} of
    the document whose doc_id or doc_key is `doc`, or None when it isn't in the
    index or `role` may not see all of it."""
    allowed = ROLE_ACCESS.get(role, ["public"])
    collection = ingest._store(indexing.current(ws)[0])._collection
    got = {"ids": []}
    for field in ("doc_id", "doc_key"):
        got = collection.get(where={field: doc}, include=["documents", "metadatas"])
        if got["ids"]:
            break
    if not got["ids"]:
        return None
    chunks = list(zip(got["documents"], [m or {} for m in got["metadatas"]]))
    if any(m.get("access", "public") not in allowed for _, m in chunks):
        return None
    text, truncated = _piece_together(chunks)
    meta = chunks[0][1]
    passage = passage_of(meta)
    return {**{f: passage[f] for f in ("doc_id", "title", "provider", "url", "access",
                                       "modified")},
            "doc_key": meta.get("doc_key"), "text": text, "truncated": truncated}


def _piece_together(chunks):
    """A document's text from its chunks: page by page, in order, each chunk's
    overlap with the one before left out (they know where they sit, ingest.py).
    Chunks from an index without positions are joined as they are."""
    def where(meta):
        start = meta.get("char_start")
        return (meta.get("page") or 0, start if isinstance(start, int) else 0)
    parts, page, end = [], None, 0
    for text, meta in sorted(chunks, key=lambda c: where(c[1])):
        body = passage_text(text, meta)
        start, stop = meta.get("char_start"), meta.get("char_end")
        if meta.get("page") != page or not isinstance(start, int) or not isinstance(stop, int):
            page, end = meta.get("page"), 0
            parts.append(("\n\n" if parts else "") + body)
        elif start >= end:
            parts.append(("\n" if parts and start > end else "") + body)
        else:
            parts.append(body[end - start:])
        if isinstance(stop, int):
            end = max(end, stop)
    text = "".join(parts)
    if len(text) > READ_MAX_CHARS:
        return text[:READ_MAX_CHARS], True
    return text, False
