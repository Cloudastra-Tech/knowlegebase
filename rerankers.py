"""
rerankers.py  —  REAL reranking (pipeline.md P1-1), the RERANKER slot in rag.py.

Hybrid search (rag.HybridRetriever) is good at FINDING candidates and poor at
ORDERING them: RRF only knows each chunk's rank in two lists. A reranker reads
the question and each candidate together and scores how well the candidate
answers it. So when one is on, the search fuses a larger pool (RERANK_CANDIDATES,
default 30), the reranker scores the whole pool, and only the best RERANK_KEEP
(default 6) reach the model.

RERANKER=        what it is                                  needs
  none           keep the fused order (the default)           -
  cohere         Cohere's rerank API                          COHERE_API_KEY
  voyage         Voyage AI's rerank API                       VOYAGE_API_KEY
  local          a small cross-encoder run here on the CPU    nothing to pay for;
                 (ms-marco-MiniLM-L-6, ONNX, about 23 MB,     the model is downloaded
                 through onnxruntime: no torch)               once (Hugging Face)
  bedrock        AWS Bedrock's Rerank API, in the workspace's  BEDROCK_RERANK_MODEL
                 own AWS account when it brings one (models.py) (cohere.rerank-v3-5:0;
                 or the server's (AWS_REGION, its credentials)  amazon.rerank-v1:0
                                                               isn't in us-east-1)

A workspace whose models are on AWS Bedrock (models.py) only reranks with a
reranker that keeps its text in-house: local or bedrock. Cohere's and Voyage's APIs
are skipped there (models.Models.allows_reranker), the fused order is kept.

The API ones SEND THE CANDIDATE CHUNKS' TEXT to that company, which then
processes company documents: only turn one on with a data processing agreement
in place, and check what the landing page promises about where data goes. Each
is off unless both RERANKER names it and its key is set. A reranker that fails
(timeout, API down, model not downloadable) is logged and the fused order is
used: a question never fails because of it.

Only chunks the asker may see are candidates (the searches filter first), so a
reranker never sees, or reorders in, anything the asker couldn't read.
"""

import logging
import os
import threading

import requests

log = logging.getLogger("uvicorn.error")

TIMEOUT = 8                     # seconds per API call
MAX_DOC_CHARS = 4000            # of each candidate sent to an API reranker


def _env_int(name, default):
    try:
        return max(1, int(os.getenv(name, "") or default))
    except ValueError:
        return default


CANDIDATES = _env_int("RERANK_CANDIDATES", 30)      # fused candidates the reranker scores
KEEP = _env_int("RERANK_KEEP", 6)                    # ...of which the best reach the model


def _min_score():
    """RERANK_MIN_SCORE: candidates scoring below it are dropped (except the
    best one). Unset = keep the best KEEP whatever their scores. The scale is the
    reranker's own: 0-1 for Cohere and Voyage, a logit for the local model."""
    raw = (os.getenv("RERANK_MIN_SCORE") or "").strip()
    try:
        return float(raw) if raw else None
    except ValueError:
        return None


class Reranker:
    """scores(query, texts) -> one relevance score per text (higher = better).
    Called as reranker(query, docs) -> the docs best first, each with its
    rerank_score in its metadata."""

    name = "reranker"
    private = False           # True: the candidates never leave this server or AWS

    def scores(self, query, texts):
        raise NotImplementedError

    def __call__(self, query, docs):
        if not docs:
            return docs
        try:
            scores = self.scores(query, [d.page_content for d in docs])
        except Exception as e:
            log.warning("Rerank: %s failed (%s); keeping the fused order", self.name,
                        type(e).__name__)
            return docs
        ranked = sorted(zip(docs, scores), key=lambda pair: -pair[1])
        floor = _min_score()
        if floor is not None:
            ranked = ranked[:1] + [(d, s) for d, s in ranked[1:] if s >= floor]
        out = []
        for doc, score in ranked:
            doc.metadata = {**doc.metadata, "rerank_score": round(float(score), 4)}
            out.append(doc)
        return out


class CohereReranker(Reranker):
    name = "cohere"
    URL = "https://api.cohere.com/v2/rerank"

    def __init__(self, key, model=None):
        self.key = key
        self.model = model or os.getenv("COHERE_RERANK_MODEL") or "rerank-v3.5"

    def scores(self, query, texts):
        r = requests.post(self.URL, timeout=TIMEOUT, headers={
            "Authorization": f"Bearer {self.key}", "Content-Type": "application/json"},
            json={"model": self.model, "query": query,
                  "documents": [t[:MAX_DOC_CHARS] for t in texts], "top_n": len(texts)})
        r.raise_for_status()
        out = [0.0] * len(texts)
        for item in r.json().get("results", []):
            out[item["index"]] = float(item["relevance_score"])
        return out


class VoyageReranker(Reranker):
    name = "voyage"
    URL = "https://api.voyageai.com/v1/rerank"

    def __init__(self, key, model=None):
        self.key = key
        self.model = model or os.getenv("VOYAGE_RERANK_MODEL") or "rerank-2.5"

    def scores(self, query, texts):
        r = requests.post(self.URL, timeout=TIMEOUT, headers={
            "Authorization": f"Bearer {self.key}", "Content-Type": "application/json"},
            json={"model": self.model, "query": query,
                  "documents": [t[:MAX_DOC_CHARS] for t in texts], "top_k": len(texts)})
        r.raise_for_status()
        out = [0.0] * len(texts)
        for item in r.json().get("data", []):
            out[item["index"]] = float(item["relevance_score"])
        return out


class LocalCrossEncoder(Reranker):
    """A cross-encoder (query and passage read together by one small BERT)
    on onnxruntime and tokenizers, which chromadb already installs: no torch.
    Downloaded on first use from Hugging Face at a pinned revision into
    RERANK_MODEL_DIR (default: Hugging Face's cache)."""

    name = "local"
    private = True
    REPO = "Xenova/ms-marco-MiniLM-L-6-v2"
    REVISION = "a09144355adeed5f58c8ed011d209bf8ee5a1fec"
    FILE = "onnx/model_quantized.onnx"
    MAX_TOKENS = 320               # a question and a ~200-token chunk fit
    _lock = threading.Lock()

    def __init__(self):
        self._session = self._tokenizer = None

    def _load(self):
        with self._lock:
            if self._session is None:
                import onnxruntime
                from huggingface_hub import hf_hub_download
                from tokenizers import Tokenizer
                folder = os.getenv("RERANK_MODEL_DIR") or None
                model = hf_hub_download(self.REPO, self.FILE, revision=self.REVISION,
                                        cache_dir=folder)
                vocab = hf_hub_download(self.REPO, "tokenizer.json", revision=self.REVISION,
                                        cache_dir=folder)
                tokenizer = Tokenizer.from_file(vocab)
                tokenizer.enable_truncation(self.MAX_TOKENS)
                tokenizer.enable_padding()
                options = onnxruntime.SessionOptions()
                options.intra_op_num_threads = 2          # a shared server: stay small
                self._tokenizer = tokenizer
                self._session = onnxruntime.InferenceSession(
                    model, options, providers=["CPUExecutionProvider"])
        return self._session, self._tokenizer

    def scores(self, query, texts):
        import numpy as np
        session, tokenizer = self._load()
        out = []
        for start in range(0, len(texts), 16):
            batch = tokenizer.encode_batch([(query, t) for t in texts[start:start + 16]])
            feed = {"input_ids": np.array([e.ids for e in batch], dtype=np.int64),
                    "attention_mask": np.array([e.attention_mask for e in batch], dtype=np.int64),
                    "token_type_ids": np.array([e.type_ids for e in batch], dtype=np.int64)}
            names = {i.name for i in session.get_inputs()}
            logits = session.run(None, {k: v for k, v in feed.items() if k in names})[0]
            out += [float(row[0]) for row in logits]
        return out


class BedrockReranker(Reranker):
    """Bedrock's Rerank API (bedrock-agent-runtime), with the credentials of the
    workspace whose question it is (models.current()), or the server's."""
    name = "bedrock"
    private = True

    def __init__(self, model=None):
        self.model = model or os.getenv("BEDROCK_RERANK_MODEL") or "cohere.rerank-v3-5:0"

    def scores(self, query, texts):
        import models
        on = models.current()
        spec = on.spec if on.uses("bedrock") else models.Spec(provider="bedrock",
                                                      region=models.server_region())
        client = models.client(spec, "bedrock-agent-runtime", timeout=TIMEOUT, retries=0)
        arn = (self.model if self.model.startswith("arn:")
               else f"arn:aws:bedrock:{spec.region}::foundation-model/{self.model}")
        reply = client.rerank(
            queries=[{"type": "TEXT", "textQuery": {"text": query}}],
            sources=[{"type": "INLINE", "inlineDocumentSource": {
                "type": "TEXT", "textDocument": {"text": t[:MAX_DOC_CHARS]}}} for t in texts],
            rerankingConfiguration={"type": "BEDROCK_RERANKING_MODEL",
                                    "bedrockRerankingConfiguration": {
                                        "numberOfResults": len(texts),
                                        "modelConfiguration": {"modelArn": arn}}})
        out = [0.0] * len(texts)
        for item in reply.get("results", []):
            out[item["index"]] = float(item["relevanceScore"])
        return out


def from_env():
    """The reranker RERANKER asks for, or None (also when its key is missing)."""
    choice = (os.getenv("RERANKER") or "none").strip().lower()
    if choice in ("", "none", "off"):
        return None
    if choice == "cohere":
        key = os.getenv("COHERE_API_KEY")
        if key:
            return CohereReranker(key)
    elif choice == "voyage":
        key = os.getenv("VOYAGE_API_KEY")
        if key:
            return VoyageReranker(key)
    elif choice == "local":
        return LocalCrossEncoder()
    elif choice == "bedrock":
        return BedrockReranker()
    log.warning("RERANKER=%s is unknown or its API key isn't set; reranking is off", choice)
    return None
