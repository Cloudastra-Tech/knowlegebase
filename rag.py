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

This file is imported by app.py. You can also test it directly:
    python rag.py
"""

import os
from dotenv import load_dotenv

import re

from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.prompts import ChatPromptTemplate
from langchain_community.retrievers import BM25Retriever   # keyword search

from safety import check_question, check_answer  # our Guardrails-AI safety layer

load_dotenv()
DB_FOLDER = "chroma_db"
INTRO_CHARS = 1200      # chunks starting this early count as a document's intro
INTRO_CHUNKS = 3        # ...at most this many per document (title + summary)

# ----------------------------------------------------------------------------
# Phase 3 retrieval upgrades — ON/OFF switches.
# Each ON adds ONE extra gpt-4o-mini call per question (more tokens).
# They shine on BIG document sets; on this tiny demo the effect is small.
# Set to False to save tokens.
# ----------------------------------------------------------------------------
USE_HYDE = True      # expand a vague question before searching
USE_RERANK = True    # reorder retrieved chunks so the best one is first


# This is the INSTRUCTION we give the AI. Notice the strict rules:
# "use ONLY the context" and "say you don't know if it's not there".
# That single instruction is what reduces hallucination (made-up answers).
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
    for text, meta in zip(stored["documents"], stored["metadatas"]):
        if meta.get("access", "public") in allowed:
            texts.append(text)
            metas.append(meta)

    if texts:
        bm25_retriever = BM25Retriever.from_texts(texts=texts, metadatas=metas)
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

    def intros_for(self, docs, max_docs=2):
        """The opening chunks of the top documents among `docs` that aren't
        already included — added to the context, not used for ranking."""
        have = {d.page_content for d in docs}
        sources = list(dict.fromkeys(d.metadata.get("source", "") for d in docs))[:max_docs]
        return [i for src in sources for i in self.intros.get(src, [])
                if i.page_content not in have]

    def invoke(self, query):
        # Run both searches. Each returns an ORDERED list of chunks (best first).
        dense_hits = self.dense.invoke(query)
        bm25_hits = self.bm25.invoke(query)

        # Score every chunk by its rank in each list (RRF).
        scores = {}        # chunk text -> fused score
        docs = {}          # chunk text -> the Document object
        for hits in (dense_hits, bm25_hits):
            for rank, doc in enumerate(hits):
                key = doc.page_content
                scores[key] = scores.get(key, 0) + 1 / (self.rrf_k + rank)
                docs[key] = doc

        # Sort by fused score (highest first) and return the top chunks.
        best = sorted(scores, key=scores.get, reverse=True)[: self.top_k]
        return [docs[k] for k in best]


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


def rerank(question, docs, llm, top_n=4):
    """
    RERANK: the retriever ranks chunks fast but roughly. Here we ask the AI to
    RE-ORDER them by how well each truly answers the question, then keep the best.
    One LLM call reads all chunks and returns the order (e.g. "2,0,1,3").
    This pushes the single best chunk to the top — accuracy with small k.
    """
    if not docs:
        return docs
    listing = "\n\n".join(f"[{i}] {d.page_content[:300]}" for i, d in enumerate(docs))
    prompt = (
        "Order the chunks from MOST to LEAST relevant for answering the question. "
        "Reply ONLY with the chunk numbers, comma-separated (e.g. 2,0,1).\n\n"
        f"Question: {question}\n\nChunks:\n{listing}\n\nOrder:"
    )
    resp = llm.invoke(prompt).content
    order = [int(x) for x in re.findall(r"\d+", resp) if int(x) < len(docs)]
    # Rebuild the list in the new order, then append any the AI forgot.
    reranked = [docs[i] for i in order]
    for i, d in enumerate(docs):
        if i not in order:
            reranked.append(d)
    return reranked[:top_n]


def answer_question(question, retriever, llm, role="public", guard=None):
    """Run the full Retrieve -> Augment -> Generate -> Guard flow.
    guard: optional function -> (ok, message) that replaces the input check
    below. The caller runs its guards WHILE this retrieves; guard() is waited
    on just before generation, so a blocked question never reaches the model."""

    # Does this role get to see restricted info? (HR yes, public no.)
    allow_restricted = "restricted" in ROLE_ACCESS.get(role, ["public"])

    # ---- GUARD (input) -------------------------------------------------
    # Check the question BEFORE doing any work (e.g. block empty/abusive input).
    if guard is None:
        ok, reason = check_question(question, allow_restricted)
        if not ok:
            return reason, None      # None = blocked by a guard (not "no results")

    # ---- 0. HyDE (optional) — enrich the search query ------------------
    search_query = question
    if USE_HYDE:
        search_query = question + "\n" + hyde_expand(question, llm)

    # ---- 1. RETRIEVE ---------------------------------------------------
    # Search the vector DB for chunks whose MEANING matches the (enriched) query.
    docs = retriever.invoke(search_query)

    # ---- 1b. RERANK (optional) — put the best chunk first --------------
    if USE_RERANK:
        docs = rerank(question, docs, llm, top_n=4)

    # ---- 1c. Add how the top documents BEGIN (title / summary), so a chunk
    # from the middle of a document is read in context. Citations still come
    # from the matched chunks only (the intros are appended after them).
    if hasattr(retriever, "intros_for"):
        docs = docs + retriever.intros_for(docs)

    # The caller's guards ran alongside the retrieval above: nothing retrieved
    # goes into a prompt until they pass.
    if guard is not None:
        ok, reason = guard()
        if not ok:
            return reason, None

    # ---- 2. AUGMENT ----------------------------------------------------
    # Glue the retrieved chunks together into one block of "context" text.
    # Each chunk already starts with "[Document title]" (see ingest.py), so the
    # model knows which document every piece of context comes from.
    context = "\n\n---\n\n".join(d.page_content for d in docs)

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
