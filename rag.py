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

import contextvars
import logging
import os
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from dotenv import load_dotenv

import regex      # Unicode-aware regular expressions (\p{L} = any letter, any script)

from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.prompts import ChatPromptTemplate
from langchain_community.retrievers import BM25Retriever   # keyword search

import models
import rerankers
from ingest import CHROMA_LOCK
from safety import check_question, check_answer  # our Guardrails-AI safety layer
from untrusted import RULE, spotlight

load_dotenv()
log = logging.getLogger("uvicorn.error")
DB_FOLDER = "chroma_db"
INTRO_CHARS = 1200      # chunks starting this early count as a document's intro
INTRO_CHUNKS = 3        # ...at most this many per document (title + summary)

TOP_K = 4               # chunks a search keeps without a reranker


def _switch(name, default):
    """An on/off/auto setting from .env: True, False or "auto"."""
    value = (os.getenv(name) or default).strip().lower()
    if value in ("0", "off", "false", "no"):
        return False
    if value in ("1", "on", "true", "yes", "always"):
        return True
    return "auto"


def _env_float(name, default):
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


# ----------------------------------------------------------------------------
# Retrieval upgrades — switches (.env). Each one that calls a model uses the
# FAST model (models.py) and runs alongside the others, not one after another.
# Measure with evaluate.py (needs a key) before changing a default.
# ----------------------------------------------------------------------------
# HyDE: a guessed answer added to the MEANING search only (never to keyword
# search: its invented words would match real chunks). RAG_HYDE=
#   auto   only for a short or vague question (is_vague), the default: a
#          specific question already matches well, and skipping it saves a call
#   on     every question;  off   never
USE_HYDE = _switch("RAG_HYDE", "auto")
# MULTI-QUERY: RAG_MULTI_QUERY=on asks for MULTI_QUERY_N rewordings of the
# question, each searched by MEANING too (keyword search keeps the question as
# asked), all fused together. One more call per question; off by default.
MULTI_QUERY = _switch("RAG_MULTI_QUERY", "off") is True
MULTI_QUERY_N = 3

# RERANK: a REAL reranker (rerankers.py: RERANKER=cohere / voyage / local) —
# a callable (question, docs) -> docs, best first. None = keep the fused order.
# With one, both searches return rerankers.CANDIDATES chunks, the reranker
# scores the fused pool, and the best rerankers.KEEP reach the model.
# (The old LLM rerank reordered the same 4 chunks it was given: one extra
# call that changed nothing about WHICH chunks the model read.)
RERANKER = rerankers.from_env()

# FRESHNESS: when chunks rank close, the one from the more recently changed
# document goes first. A chunk's score is multiplied by 1 + RECENCY_BOOST x
# its freshness (1 for the newest document among the candidates, halving every
# RECENCY_HALF_LIFE_DAYS older). Mild on purpose: at 0.03, near the top a newer
# chunk passes the one just above it, never one ranked two places higher.
# RAG_RECENCY_BOOST=0 turns it off.
RECENCY_BOOST = max(0.0, _env_float("RAG_RECENCY_BOOST", 0.03))
RECENCY_HALF_LIFE_DAYS = max(1.0, _env_float("RAG_RECENCY_HALF_LIFE_DAYS", 365))


# FRESHNESS (pipeline.md P1-7): every passage's block carries its document's
# `updated` date, and when two disagree the newer one wins, said openly.
FRESHNESS_RULE = (
    "If two passages disagree (a different price, date, number or rule), go by the one "
    "whose block has the later `updated` date, and say that an older document says "
    "otherwise.")


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
- """ + FRESHNESS_RULE + """
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

    # With a reranker, both searches bring a larger pool for it to choose from.
    reranker = RERANKER
    pool = rerankers.CANDIDATES if reranker else TOP_K

    # ---- SEARCH 1: semantic (dense) — matches by MEANING ----
    # The 'filter' makes Chroma only search chunks whose access is allowed.
    dense_retriever = vectorstore.as_retriever(
        search_kwargs={"k": pool, "filter": {"access": {"$in": allowed}}}
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
        bm25_retriever.k = pool
    else:                       # an empty workspace: nothing to search yet
        bm25_retriever = _NoHits()

    # ---- HYBRID: fuse both searches with Reciprocal Rank Fusion (RRF) ----
    # Our own little retriever runs BOTH searches and merges their rankings
    # (then reranks the pool, if a reranker is plugged in).
    retriever = HybridRetriever(dense_retriever, bm25_retriever,
                                top_k=rerankers.KEEP if reranker else TOP_K,
                                pool=pool, reranker=reranker)

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

    # The FAST model (models.py): HyDE, multi-query, rewriting a follow-up and
    # the output guard's rewrite. The agent's own model writes the answer.
    llm = ChatOpenAI(model=models.fast_model(), temperature=0)

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

    def __init__(self, dense_retriever, bm25_retriever, top_k=4, rrf_k=60, pool=None,
                 reranker=None):
        self.dense = dense_retriever
        self.bm25 = bm25_retriever
        self.top_k = top_k
        self.rrf_k = rrf_k
        self.pool = max(pool or top_k, top_k)   # fused candidates a reranker scores
        self.reranker = reranker
        self.intros = {}      # source -> its first chunks (set by load_chain)
        # SOURCE RULES (platform.md PL-28): weigh(metadata) -> (multiplier, rule).
        # Set by the agent's builder for workspaces with rules; None = no rules.
        self.weigh = None

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

    def invoke(self, query, dense_query=None, dense_queries=None, k=None):
        """query: what the user asked — the KEYWORD search uses exactly this.
        dense_query: an enriched version for the MEANING search only (e.g.
        the question + a HyDE guess); defaults to query. dense_queries: several
        (multi-query), each searched by meaning and fused like the rest. A
        guessed answer must not feed keyword search: its invented words would
        match real chunks.
        k: how many chunks (default top_k), each search asked for as many (at
        least the reranker's pool, when one is plugged in).
        Each chunk comes back as a copy whose metadata also says how it was
        found ("why this source"): rank (1 = best, after fusion and any
        rerank), dense_rank and bm25_rank (None when that search didn't return
        it), fused_rank and rerank_score (with a reranker), and its chunk_id;
        and the source rule that weighed it, if one did (rule, rule_weight).

        SOURCE RULES (self.weigh): each chunk's fused score is multiplied by its
        document's rule (boost > 1, demote < 1), and a blocked document's chunks
        are dropped. The FRESHNESS boost (prefer_recent) then reorders near ties
        with the rule's multiplier in the same score: the two compose, neither
        replaces the other, and a reranker's order is weighed by the rules too."""
        want = k or self.top_k
        each = max(k, self.pool) if k and self.reranker else k
        # Run the searches. Each returns an ORDERED list of chunks (best first).
        lists = [("dense_rank", self._hits(self.dense, q, each))
                 for q in (dense_queries or [dense_query or query])]
        lists.append(("bm25_rank", self._hits(self.bm25, query, each)))

        # Score every chunk by its rank in each list (RRF).
        scores = {}        # chunk text -> fused score
        docs = {}          # chunk text -> the Document object
        ranks = {}         # chunk text -> {"dense_rank": n, "bm25_rank": n}
        for name, hits in lists:
            for rank, doc in enumerate(hits):
                key = doc.page_content
                scores[key] = scores.get(key, 0) + 1 / (self.rrf_k + rank)
                docs[key] = doc
                seen = ranks.setdefault(key, {})
                seen[name] = min(seen.get(name, rank + 1), rank + 1)

        # Source rules: multiply the fused score; a blocked document is gone.
        rules = {}
        if self.weigh:
            for key in list(scores):
                factor, rule = self.weigh(docs[key].metadata)
                if factor <= 0:
                    del scores[key]
                    continue
                scores[key] *= factor
                if rule:
                    rules[key] = (factor, rule)

        # Sort by fused score (highest first): the candidates.
        order = sorted(scores, key=scores.get, reverse=True)[: max(self.pool, want)]
        fused = {key: n for n, key in enumerate(order, 1)}
        rerank = {}
        if self.reranker and order:
            # The reranker reads the question with each candidate and reorders them.
            ranked = self.reranker(query, [Document(page_content=key, id=docs[key].id,
                                                    metadata=dict(docs[key].metadata))
                                           for key in order])
            order = [d.page_content for d in ranked if d.page_content in fused]
            rerank = {d.page_content: d.metadata.get("rerank_score") for d in ranked}
        order = prefer_recent(order, {key: docs[key].metadata for key in order}, self.rrf_k,
                              factors={k: f for k, (f, _) in rules.items()})
        best = order[:want]
        return [Document(page_content=key, id=docs[key].id, metadata={
                    **docs[key].metadata,
                    "chunk_id": docs[key].metadata.get("chunk_id") or docs[key].id,
                    "rank": n, "dense_rank": ranks[key].get("dense_rank"),
                    "bm25_rank": ranks[key].get("bm25_rank"),
                    **({"fused_rank": fused[key], "rerank_score": rerank.get(key)}
                       if self.reranker else {}),
                    **({"rule": rules[key][1], "rule_weight": rules[key][0]}
                       if key in rules else {})})
                for n, key in enumerate(best, 1)]


def _timestamp(meta):
    """A chunk's document's last change (metadata `modified`, ISO) in seconds, or None."""
    value = meta.get("modified")
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def prefer_recent(order, metas, rrf_k=60, boost=None, half_life_days=None, factors=None):
    """`order` (keys, best first) with the FRESHNESS boost applied (see
    RECENCY_BOOST): each position's score 1 / (rrf_k + position) is multiplied
    by 1 + boost x freshness, freshness being 1 for the newest document among
    them and halving every half_life_days older (0 without a date). Only near
    ties change places; the same dates everywhere change nothing.
    factors: {key: multiplier}, the SOURCE RULES (HybridRetriever.weigh), which
    multiply the same score: so freshness can't undo a demote by passing the
    demoted chunk back over the one above it, nor a reranker a rule."""
    boost = RECENCY_BOOST if boost is None else boost
    factors = factors or {}
    half_life = (half_life_days or RECENCY_HALF_LIFE_DAYS) * 86400
    times = {k: _timestamp(metas.get(k) or {}) for k in order}
    known = [t for t in times.values() if t is not None]
    if len(order) < 2 or ((not boost or not known) and not factors):
        return list(order)
    newest = max(known) if known else 0
    fresh = {k: 0.0 if t is None or not boost else 0.5 ** ((newest - t) / half_life)
             for k, t in times.items()}
    score = {k: factors.get(k, 1.0) * (1 + boost * fresh[k]) / (rrf_k + n)
             for n, k in enumerate(order)}
    return sorted(order, key=lambda k: -score[k])


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


# Words that say nothing about WHAT is asked: a question made of little else is vague.
_GENERIC = set("""a an the is are was were be do does did what whats who which when where why
how can could would should will may might must of in on at to for from by with and or not no
our your my their its this that these those it i we you they me us any some all about tell
give show explain describe please there here more info information detail details policy
policies rule rules process thing things stuff one ones same other else again also so""".split())
VAGUE_WORDS = 2         # content words at most: vague
SHORT_WORDS = 4         # words at most: short


def is_vague(question):
    """True for a SHORT question ("leave?", "refund policy") or a VAGUE one
    that names little ("tell me more about the policy"): these match chunks
    poorly as typed, which is when a HyDE guess helps (RAG_HYDE=auto)."""
    words = tokenize(question)
    content = [w for w in words if w not in _GENERIC]
    return len(words) <= SHORT_WORDS or len(content) <= VAGUE_WORDS


MULTI_QUERY_PROMPT = (
    "Write {n} different search queries that would find the passages answering the "
    "question below in a company's documents: other words for the same thing, the "
    "terms a policy or a handbook would use, or one part of a two-part question. One "
    "query per line, no numbering, no answer.\n\nQuestion: {question}\n\nQueries:")


def query_variants(question, llm, n=MULTI_QUERY_N):
    """Up to n rewordings of the question (multi-query), for the meaning search only."""
    reply = str(llm.invoke(MULTI_QUERY_PROMPT.format(n=n, question=question)).content or "")
    out = []
    for line in reply.splitlines():
        line = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", line).strip().strip('"')
        if line and line.casefold() != question.casefold() and line not in out:
            out.append(line[:300])
    return out[:n]


def dense_queries(question, llm):
    """What the MEANING search looks for: the question (with a HyDE guess when
    USE_HYDE says so) and, with MULTI_QUERY, its rewordings. The model calls run
    side by side, in the caller's context (so a question's token count, kept in
    a context variable, sees them)."""
    hyde = USE_HYDE is True or (USE_HYDE == "auto" and is_vague(question))
    if not MULTI_QUERY:
        return [question + "\n" + hyde_expand(question, llm) if hyde else question]
    pool = ThreadPoolExecutor(max_workers=2)
    try:
        guess = (pool.submit(contextvars.copy_context().run, hyde_expand, question, llm)
                 if hyde else None)
        variants = pool.submit(contextvars.copy_context().run, query_variants, question, llm)
        first = question + "\n" + guess.result() if guess else question
        try:
            more = variants.result()
        except Exception as e:             # the rewordings are a bonus: search without them
            log.warning("Retrieval: multi-query failed (%s)", type(e).__name__)
            more = []
    finally:
        pool.shutdown(wait=False)
    return [first] + more


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


def retrieve(question, retriever, llm, role="public", guard=None, expand=None):
    """Retrieve: the chunks a question is answered from -> (docs, None), or
    (None, reason) when an input guard refused it. Nothing is generated here:
    the agent writes the one answer, citing these chunks (agent.py).
    guard: optional function -> (ok, message) that replaces the input check
    below. The caller runs its guards WHILE this retrieves; guard() is waited
    on before anything found is returned.
    expand: optional function -> the question as searched (the workspace's
    glossary adds the other names of its terms); the guard checks it as asked."""

    # Does this role get to see restricted info? (HR yes, public no.)
    allow_restricted = "restricted" in ROLE_ACCESS.get(role, ["public"])

    # ---- GUARD (input) -------------------------------------------------
    # Check the question BEFORE doing any work (e.g. block empty/abusive input).
    if guard is None:
        ok, reason = check_question(question, allow_restricted)
        if not ok:
            return None, reason      # None = blocked by a guard (not "no results")

    # ---- 0. GLOSSARY: "PTO" is searched as "PTO (paid time off)" too, by both
    # searches. Then HyDE / multi-query (optional) enrich the MEANING search only.
    if expand:
        question = expand(question)
    queries = dense_queries(question, llm)

    # ---- 1. RETRIEVE (and rerank, if a reranker is plugged in: RERANKER) -
    # Keyword search gets the question as asked; meaning search the enriched one(s).
    if len(queries) == 1:
        docs = retriever.invoke(question, dense_query=queries[0])
    else:
        docs = retriever.invoke(question, dense_queries=queries)

    # ---- 1b. Add how the top documents BEGIN (title / summary), so a chunk
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
                                      document=_document_name(d),
                                      updated=str(d.metadata.get("modified") or "")[:10])
                          for d in docs)

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
