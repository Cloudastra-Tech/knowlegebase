"""
chunk_context.py  —  CONTEXTUAL RETRIEVAL with an LLM (pipeline.md P1-3), optional.

A chunk from the middle of a document often never names what it is about
("It takes 30 days."). ingest.py always starts each chunk with its document's
title, and with RAG_CHUNK_CONTEXT=headings also with the headings it sits under
(free). With RAG_CHUNK_SUMMARY=llm it also asks the FAST model (models.py) for one
sentence placing the chunk in its document, which is put on the chunk for search
(embedding and keyword) but never shown as part of a source: a citation shows
only the document's own text (rag.passage_text).

Off by default: it is one model call per chunk when an index is built. Each
answer is cached in config/app.db (chunk_contexts) by a hash of the model, the
document and the chunk, so a rebuild, or an update that leaves a chunk as it
was, doesn't pay again. A failed call gives no context line for that chunk
(and isn't cached): indexing never fails because of it.
"""

import hashlib
import logging
import time

import db
import models
from untrusted import RULE, spotlight

log = logging.getLogger("uvicorn.error")

PROMPT_VERSION = 1
DOC_CHARS = 24_000            # of the document given as context (about 6k tokens)
MAX_CHARS = 300               # of the context line kept

# The rules and the document come first and the chunk last, so the calls for one document
# share a long prefix that OpenAI's prompt cache serves at a discount.
# Both are spotlighted (untrusted.py): a document may hold planted instructions.
PROMPT = (
    RULE + "\n\nHere is a document:\n{document}\n\nHere is a chunk from it:\n{chunk}\n\n"
    "Write ONE short sentence that situates this chunk within the document, to improve "
    "search: what the document is and which part or topic this chunk covers. Do not "
    "repeat the chunk and do not add facts that aren't in the document. Reply with the "
    "sentence only.")


def _key(model, document, chunk):
    raw = "\0".join((str(PROMPT_VERSION), model, hashlib.sha256(document.encode()).hexdigest(),
                     hashlib.sha256(chunk.encode()).hexdigest()))
    return hashlib.sha256(raw.encode()).hexdigest()


def _cached(key):
    conn = db.connect()
    try:
        row = conn.execute("SELECT context FROM chunk_contexts WHERE hash = ?", (key,)).fetchone()
        return row["context"] if row else None
    finally:
        conn.close()


def _save(key, model, context):
    conn = db.connect()
    try:
        with conn:
            conn.execute("INSERT OR REPLACE INTO chunk_contexts (hash, model, context, created_at) "
                         "VALUES (?, ?, ?, ?)", (key, model, context, time.time()))
    finally:
        conn.close()


def _llm():
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(model=models.fast_model(), temperature=0, max_tokens=80)


def context_for(document, chunk, llm=None):
    """One sentence placing `chunk` in `document` ('' if the call failed)."""
    model = models.fast_model()
    document = document[:DOC_CHARS]
    key = _key(model, document, chunk)
    try:
        hit = _cached(key)
    except Exception:
        log.exception("Chunk context: couldn't read the cache")
        hit = None
    if hit is not None:
        return hit
    try:
        reply = (llm or _llm()).invoke(PROMPT.format(
            document=spotlight(document, "company_documents"),
            chunk=spotlight(chunk, "company_documents"))).content
    except Exception as e:
        log.warning("Chunk context: the model call failed (%s)", type(e).__name__)
        return ""
    context = " ".join(str(reply or "").split())[:MAX_CHARS]
    if context:
        try:
            _save(key, model, context)
        except Exception:
            log.exception("Chunk context: couldn't write the cache")
    return context
