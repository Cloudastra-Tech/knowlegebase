"""
rag.py  —  STEP 2 of RAG: ANSWER a question using your documents.

This is the "open-book exam" part:
    when a user asks a question, we DON'T just ask the AI from memory.
    Instead we:
        1. RETRIEVE — find the most relevant chunks from the vector database
        2. AUGMENT  — paste those chunks into the prompt as "context"
        3. GENERATE — ask the AI to answer USING ONLY that context
        4. GUARD    — check the answer is safe + grounded before returning it

    R-A-G = Retrieval-Augmented Generation. That's literally these steps.

This file is imported by agent.py. You can also test it directly:
    python rag.py
"""

import os
import re
import unicodedata
from dotenv import load_dotenv

import regex      # Unicode-aware regular expressions (\p{L} = any letter, any script)

from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.prompts import ChatPromptTemplate
from langchain_community.retrievers import BM25Retriever   # keyword search

from ingest import CHROMA_LOCK
from safety import check_question, check_answer  # our Guardrails-AI safety layer
from untrusted import RULE, spotlight

load_dotenv()
DB_FOLDER = "chroma_db"
INTRO_CHARS = 1200      # chunks starting this early count as a document's intro
INTRO_CHUNKS = 3        # ...at most this many per document (title + summary)

# ----------------------------------------------------------------------------
# Phase 3 retrieval upgrades — ON/OFF switches.
# HyDE adds ONE extra gpt-4o-mini call per question (more tokens, ~1 s).
# Turn it off with RAG_HYDE=off in .env. Measure with evaluate.py before
# changing the default.
# ----------------------------------------------------------------------------
USE_HYDE = os.getenv("RAG_HYDE", "on").strip().lower() not in ("0", "off", "false", "no")

# RERANK: a slot for a REAL reranker (a cross-encoder or a rerank API) —
# a callable (question, docs) -> docs, best first. None = keep the fused order.
# It only pays off over a LARGER candidate pool than the context keeps (e.g.
# fuse 30-50 chunks, rerank, keep the top 4), so plugging one in also means
# raising the k of both searches and HybridRetriever's top_k in load_chain.
# (The old LLM rerank reordered the same 4 chunks it was given: one extra
# call that changed nothing about WHICH chunks the model read.)
RERANKER = None


# This is the INSTRUCTION we give the AI. Notice the strict rules:
# "use ONLY the context" and "say you don't know if it's not there".
# That single instruction is what reduces hallucination (made-up answers).
# Every piece of context is a spotlighted block (untrusted.py): a document
# anyone can edit may hold planted instructions, and RULE says to ignore them.
PROMPT = ChatPromptTemplate.from_template(
    """You are a helpful, friendly assistant answering questions about our documents.

How to respond:
- If the user just greets you or makes small talk (e.g. "hi"), reply warmly and
  invite them to ask about the documents. Do NOT say "I don't know" to a greeting.
- Answer questions using the context below. It was retrieved from the documents.
- Read the context carefully — the answer may be phrased differently than the question.
- Each piece of context starts with its document's title in [brackets]. Use it:
  for "what is X?" / "tell me about X", summarise what the documents about X say
  (their purpose, key facts, decisions, plans) instead of saying you don't know.
- If the answer truly isn't in the context, say you don't know based on the
  documents, and suggest a topic they could ask about instead.
- Never invent facts that aren't supported by the context. Never guess: don't
  use words like "likely", "probably" or "might" to fill gaps — say only what
  the context states, and if it's partial, say what it does cover.
- """ + RULE + """

Context:
{context}

Question: {question}

Answer:"""
)


# Which access levels each ROLE is allowed to see (permission control).
ROLE_ACCESS = {
    "public": ["public"],                  # a normal user: public chunks only
    "hr":     ["public", "restricted"],    # HR / admin: everything
}


def load_chain(role="public", collection=None):
    """
    collection: which workspace's search index to use (None = the default one).

    Build the RAG pipeline, SCOPED to what this role may see.
    A 'public' user can never retrieve 'restricted' chunks — so confidential
    text never reaches the model for them. This is permission-filtered retrieval.
    """
    allowed = ROLE_ACCESS.get(role, ["public"])

    # Re-open the vector database we built in ingest.py.
    embeddings = OpenAIEmbeddings(model="text-embedding-3-small")
    with CHROMA_LOCK:
        vectorstore = Chroma(persist_directory=DB_FOLDER, embedding_function=embeddings,
                             collection_name=collection or "langchain")

    # ---- SEARCH 1: semantic (dense) — matches by MEANING ----
    # The 'filter' makes Chroma only search chunks whose access is allowed.
    dense_retriever = vectorstore.as_retriever(
        search_kwargs={"k": 4, "filter": {"access": {"$in": allowed}}}
    )

    # ---- SEARCH 2: keyword (BM25, sparse) — matches by exact WORDS ----
    # BM25 needs the raw chunks, so we pull them from Chroma and keep only the
    # ones this role is allowed to see (so BM25 also respects permissions).
    stored = vectorstore.get()                         # all chunks + metadata
    texts, metas = [], []
    for cid, text, meta in zip(stored["ids"], stored["documents"], stored["metadatas"]):
        if meta.get("access", "public") in allowed:
            texts.append(text)
            # An index from before passage metadata (ingest.CHUNK_META): the
            # chunk's id is its Chroma id, until the next update re-tags it.
            metas.append(meta if meta.get("chunk_id") else {**meta, "chunk_id": cid})

    if texts:
        bm25_retriever = BM25Retriever.from_texts(texts=texts, metadatas=metas,
                                                  preprocess_func=tokenize)
        bm25_retriever.k = 4
    else:                       # an empty workspace: nothing to search yet
        bm25_retriever = _NoHits()

    # ---- HYBRID: fuse both searches with Reciprocal Rank Fusion (RRF) ----
    # Our own little retriever runs BOTH searches and merges their rankings.
    retriever = HybridRetriever(dense_retriever, bm25_retriever, top_k=4)

    # DOCUMENT INTROS: the first chunks of every document (title, summary).
    # Matching chunks are often from the middle of a document; for "what is X?"
    # the model also needs how that document begins. See intros_for().
    intros = {}
    for text, meta in zip(texts, metas):
        if meta.get("start_index", 10**9) < INTRO_CHARS:
            # PDFs are loaded page by page: order by page first, then position.
            key = (meta.get("page", 0), meta["start_index"])
            intros.setdefault(meta.get("source", ""), []).append((key, text, meta))
    retriever.intros = {src: [Document(page_content=t, metadata=m)
                              for _, t, m in sorted(chunks, key=lambda c: c[0])[:INTRO_CHUNKS]]
                        for src, chunks in intros.items()}

    # The chat model that writes the final answer.
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)

    return retriever, llm


_DIGIT_GROUPS = re.compile(r"(?<=\d),(?=\d)")
_WORD = regex.compile(r"[\p{L}\p{N}\p{M}]+")    # letters, digits, accents/vowel signs


def tokenize(text):
    """How BM25 splits text into words (for the chunks AND the question).
    The default is a plain str.split(): case-sensitive, with punctuation stuck
    to words, so "policy?" never matched "policy" and "Refund" never matched
    "refund". Here: normalise Unicode (NFKC: full-width "ＰＲＯ" -> "PRO"),
    case-fold, join digit groups ("2,000" -> "2000"), then keep runs of
    letters/digits in ANY script. Accents and vowel signs stay part of the
    word (dropping them would break e.g. Hindi). One-letter words are dropped,
    single digits kept ("plan 2")."""
    text = _DIGIT_GROUPS.sub("", unicodedata.normalize("NFKC", text or "").casefold())
    return [w for w in _WORD.findall(text) if len(w) > 1 or w.isdigit()]


class _NoHits:
    """Stand-in retriever for an empty index (BM25 can't be built from nothing)."""
    def invoke(self, query):
        return []


class HybridRetriever:
    """
    Combines keyword search (BM25) + semantic search (Chroma) using
    Reciprocal Rank Fusion (RRF) — a Phase 3 roadmap concept.

    RRF idea (simple and powerful): each search ranks the chunks. A chunk's
    final score = sum of 1 / (K + rank) across BOTH searches. A chunk that
    ranks high in EITHER search bubbles to the top; a chunk that ranks high
    in BOTH wins easily. K (=60, the standard value) softens the difference
    between rank 1 and rank 2 so no single search dominates.
    """

    def __init__(self, dense_retriever, bm25_retriever, top_k=4, rrf_k=60):
        self.dense = dense_retriever
        self.bm25 = bm25_retriever
        self.top_k = top_k
        self.rrf_k = rrf_k
        self.intros = {}      # source -> its first chunks (set by load_chain)

    @staticmethod
    def _hits(retriever, query, k):
        """retriever's chunks for query: as many as it returns by itself, or k of
        them when k is given (each retriever is shared by every question at
        once, so its own setting is left as it is)."""
        if k and hasattr(retriever, "vectorstore"):           # Chroma, with its filter
            return retriever.vectorstore.similarity_search(
                query, k=k, filter=retriever.search_kwargs.get("filter"))
        if k and hasattr(retriever, "vectorizer"):            # BM25
            return retriever.vectorizer.get_top_n(retriever.preprocess_func(query),
                                                  retriever.docs, n=k)
        return retriever.invoke(query)

    def intros_for(self, docs, max_docs=2):
        """The opening chunks of the top documents among `docs` that aren't
        already included — added to the context, not used for ranking."""
        have = {d.page_content for d in docs}
        sources = list(dict.fromkeys(d.metadata.get("source", "") for d in docs))[:max_docs]
        return [i for src in sources for i in self.intros.get(src, [])
                if i.page_content not in have]

    def invoke(self, query, dense_query=None, k=None):
        """query: what the user asked — the KEYWORD search uses exactly this.
        dense_query: an enriched version for the MEANING search only (e.g.
        the question + a HyDE guess); defaults to query. A guessed answer must
        not feed keyword search: its invented words would match real chunks.
        k: how many chunks (default top_k), each search asked for as many.
        Each chunk comes back as a copy whose metadata also says how it was
        found ("why this source"): rank (1 = best, after fusion), dense_rank and
        bm25_rank (None when that search didn't return it), and its chunk_id."""
        # Run both searches. Each returns an ORDERED list of chunks (best first).
        dense_hits = self._hits(self.dense, dense_query or query, k)
        bm25_hits = self._hits(self.bm25, query, k)

        # Score every chunk by its rank in each list (RRF).
        scores = {}        # chunk text -> fused score
        docs = {}          # chunk text -> the Document object
        ranks = {}         # chunk text -> {"dense_rank": n, "bm25_rank": n}
        for name, hits in (("dense_rank", dense_hits), ("bm25_rank", bm25_hits)):
            for rank, doc in enumerate(hits):
                key = doc.page_content
                scores[key] = scores.get(key, 0) + 1 / (self.rrf_k + rank)
                docs[key] = doc
                ranks.setdefault(key, {}).setdefault(name, rank + 1)

        # Sort by fused score (highest first) and return the top chunks.
        best = sorted(scores, key=scores.get, reverse=True)[: k or self.top_k]
        return [Document(page_content=k, id=docs[k].id, metadata={
                    **docs[k].metadata,
                    "chunk_id": docs[k].metadata.get("chunk_id") or docs[k].id,
                    "rank": n, "dense_rank": ranks[k].get("dense_rank"),
                    "bm25_rank": ranks[k].get("bm25_rank")})
                for n, k in enumerate(best, 1)]


def hyde_expand(question, llm):
    """
    HyDE = Hypothetical Document Embeddings.
    Vague questions ("the policy") are short and hard to match. So we ask the AI
    to write a SHORT made-up answer first. That hypothetical answer is richer text
    that matches the real documents better. We search with question + hypothetical.
    (The hypothetical is NEVER shown to the user — it's only used for searching.)
    """
    prompt = (
        "Write a short, factual 1-2 sentence answer to the question below, "
        "guessing if you must. It is only used to improve search, never shown.\n\n"
        f"Question: {question}\n\nHypothetical answer:"
    )
    return llm.invoke(prompt).content


def _document_name(doc):
    """What a chunk's block names as its document: its title, or its file name."""
    return doc.metadata.get("title") or os.path.basename(doc.metadata.get("source", ""))


def passage_of(meta):
    """What a citation shows of a chunk, from its metadata (ingest.py's PASSAGE
    METADATA; an index from before it has fewer fields): its document's title,
    provider and link, who may see it, and where in the document it sits. The
    page is 1-based, as people count pages."""
    page = meta.get("page")
    is_pdf = str(meta.get("source") or meta.get("doc_key") or "").lower().endswith(".pdf")
    return {
        "doc_id": meta.get("doc_id") or meta.get("doc_key") or None,
        "chunk_id": meta.get("chunk_id") or None,
        "title": _document_name(Document(page_content="", metadata=meta)) or "Untitled",
        "provider": meta.get("provider") or ("Connected source" if meta.get("connector")
                                             else "Company documents"),
        "url": meta.get("url") or None,
        "access": meta.get("access", "public"),
        "page": page + 1 if is_pdf and isinstance(page, int) else None,
        "heading_path": meta.get("heading_path") or None,
        "char_start": meta.get("char_start"),
        "char_end": meta.get("char_end"),
        "modified": meta.get("modified") or None,
    }


def passage_text(text, meta):
    """A chunk's own text, without the "[title]" line ingest puts first."""
    start, end = meta.get("char_start"), meta.get("char_end")
    if isinstance(start, int) and isinstance(end, int) and 0 <= end - start <= len(text):
        return text[len(text) - (end - start):]
    return re.sub(r"\A\[[^\]\n]*\]\n", "", text)


def retrieve(question, retriever, llm, role="public", guard=None):
    """Retrieve: the chunks a question is answered from -> (docs, None), or
    (None, reason) when an input guard refused it. Nothing is generated here:
    the agent writes the one answer, citing these chunks (agent.py).
    guard: optional function -> (ok, message) that replaces the input check
    below. The caller runs its guards WHILE this retrieves; guard() is waited
    on before anything found is returned."""

    # Does this role get to see restricted info? (HR yes, public no.)
    allow_restricted = "restricted" in ROLE_ACCESS.get(role, ["public"])

    # ---- GUARD (input) -------------------------------------------------
    # Check the question BEFORE doing any work (e.g. block empty/abusive input).
    if guard is None:
        ok, reason = check_question(question, allow_restricted)
        if not ok:
            return None, reason      # None = blocked by a guard (not "no results")

    # ---- 0. HyDE (optional) — enrich the MEANING search only -----------
    dense_query = question
    if USE_HYDE:
        dense_query = question + "\n" + hyde_expand(question, llm)

    # ---- 1. RETRIEVE ---------------------------------------------------
    # Keyword search gets the question as asked; meaning search the enriched one.
    docs = retriever.invoke(question, dense_query=dense_query)

    # ---- 1b. RERANK (only if a real reranker is plugged in, see RERANKER)
    if RERANKER:
        docs = RERANKER(question, docs)

    # ---- 1c. Add how the top documents BEGIN (title / summary), so a chunk
    # from the middle of a document is read in context. They come after the
    # matched chunks and have no rank (they weren't matched, see HybridRetriever).
    if hasattr(retriever, "intros_for"):
        docs = docs + retriever.intros_for(docs)

    # The caller's guards ran alongside the retrieval above: nothing retrieved
    # goes into a prompt until they pass.
    if guard is not None:
        ok, reason = guard()
        if not ok:
            return None, reason
    return docs, None


def answer_question(question, retriever, llm, role="public", guard=None):
    """Run the full Retrieve -> Augment -> Generate -> Guard flow -> (answer,
    docs), or (reason, None) when a guard refused the question. The chat agent
    doesn't call this (it retrieves, then writes ONE cited answer itself); it
    stays the plain RAG flow for scripts and the retrieval tests."""
    allow_restricted = "restricted" in ROLE_ACCESS.get(role, ["public"])
    docs, refused = retrieve(question, retriever, llm, role, guard)
    if docs is None:
        return refused, None

    # ---- 2. AUGMENT ----------------------------------------------------
    # Glue the retrieved chunks together into one block of "context" text.
    # Each chunk already starts with "[Document title]" (see ingest.py), so the
    # model knows which document every piece of context comes from. Each one
    # is its own delimited block too (untrusted.spotlight): documents are data,
    # and nothing written in one can pass for the end of its block.
    context = "\n\n".join(spotlight(d.page_content, "company_documents",
                                      document=_document_name(d)) for d in docs)

    # ---- 3. GENERATE ---------------------------------------------------
    # Fill the prompt template, then ask the AI to write the answer.
    messages = PROMPT.format_messages(context=context, question=question)
    response = llm.invoke(messages)
    answer = response.content

    # ---- GUARD (output) ------------------------------------------------
    # Final safety check. If it leaks, REASK the model to rewrite it cleanly.
    # Authorized roles (HR) skip the salary filter — they may see it.
    answer = check_answer(answer, llm, allow_restricted)

    # Return the answer AND the source chunks (so the UI can show citations).
    # (docs is None only when a guard blocked the question above.)
    return answer, docs


# Quick manual test: python rag.py
if __name__ == "__main__":
    retriever, llm = load_chain()
    q = "What is the refund policy?"
    ans, sources = answer_question(q, retriever, llm)
    print("Q:", q)
    print("A:", ans)
