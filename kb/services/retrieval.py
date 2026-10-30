"""
The search index without the answer model: what the public API's /search and the
MCP server's search_knowledge and read_document return.

  search()         the passages a query finds, best first, by the same hybrid
                   search the chat's search_docs runs (rag.HybridRetriever),
                   without HyDE and without writing an answer: no LLM cost
                   beyond the question rules' own check.
  read_document()  a whole document as the index holds it, pieced together
                   from its chunks.

Both only ever see chunks the asker may see: the search filters on their
principals (rag.load_chain filters both searches), and every chunk is checked
again here. A document with any chunk they may not see is not there for them
at all. `who` is the asker: an access.Viewer, or a bare role (its own
principals).
"""

import access
import ingest
import safety
from kb.services import agents, indexing
from rag import passage_of, passage_text

MAX_K = 20
READ_MAX_CHARS = 60_000


class Refused(Exception):
    """The question rules refused the query (safety.check_asked, check_question): the
    message says why."""


def search(ws, who, query, k=5):
    """[{doc_id, doc_key, chunk_id, title, provider, url, access, page,
    heading_path, modified, text, rank}] for `query`, at most k (1..MAX_K). The
    guard, the search and any reranker use the workspace's models (models.scope)."""
    import models
    with models.scope_for(ws):
        return _search(ws, who, query, k)


def _search(ws, who, query, k):
    viewer = access.of(who)
    # The query is what someone typed: the rules for that (length, abuse), then
    # the search's own (staff pay), as a chat question gets.
    ok, reason, _ = safety.check_asked(query)
    if ok:
        ok, reason = safety.check_question(query, safety.may_see_restricted(viewer))
    if not ok:
        raise Refused(reason)
    k = max(1, min(MAX_K, int(k)))
    agent = agents.get_agent(ws, access.role_of(who))
    expand = getattr(agent, "expand", None)          # the workspace's glossary
    found = []
    for doc in agent.retriever.invoke(expand(query) if expand else query, k=k,
                                      principals=viewer):
        meta = doc.metadata
        if not access.can_see(viewer, access.chunk_acl(meta)):  # never, but never shown either
            continue
        passage = passage_of(meta)
        found.append({**{f: passage[f] for f in ("doc_id", "chunk_id", "title", "provider",
                                                  "url", "access", "page", "heading_path",
                                                  "modified")},
                      "doc_key": meta.get("doc_key"), "text": passage_text(doc.page_content, meta),
                      "rank": meta.get("rank")})
    return found


def read_document(ws, who, doc):
    """{doc_id, doc_key, title, provider, url, access, modified, text, truncated} of
    the document whose doc_id or doc_key is `doc`, or None when it isn't in the
    index or `who` may not see all of it."""
    viewer = access.of(who)
    collection = ingest._store(indexing.current(ws)[0])._collection
    got = {"ids": []}
    for field in ("doc_id", "doc_key"):
        got = collection.get(where={field: doc}, include=["documents", "metadatas"])
        if got["ids"]:
            break
    if not got["ids"]:
        return None
    chunks = list(zip(got["documents"], [m or {} for m in got["metadatas"]]))
    if any(not access.can_see(viewer, access.chunk_acl(m)) for _, m in chunks):
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
