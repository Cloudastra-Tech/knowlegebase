"""
eval/harness.py  —  score RETRIEVAL on the golden set, with no OpenAI key.

    python -m eval.harness              print the scores
    python -m eval.harness --gate       ...and exit 1 on a leak or a drop below baseline.json
    python -m eval.harness --write-baseline     accept the current scores as the new baseline

The corpus (eval/corpus/) is indexed by the app's own ingest.main and searched
by the app's own rag.load_chain, so access tagging, chunking, BM25 and fusion
are the real code. Only the embedding model is swapped: HashEmbeddings turns
text into a vector deterministically (hashed words and character trigrams), so
the dense half of hybrid search behaves like a fuzzy keyword match. Absolute
scores are therefore not the production numbers — they are a regression gate.
The real-embedding, real-answer evaluation is evaluate.py.

GOLDEN SET FORMAT (one JSON object per line, eval/golden/<corpus>.jsonl):
    id                a short stable name
    question          what the user types
    role              who asks: "public" or "hr" (the access level, see rag.ROLE_ACCESS)
    expect            "answer" | "not_found" (no document covers it, or not for this role)
                      | "blocked" (a guard must refuse it)
    sources           documents that answer it, relative to the corpus (e.g.
                      "public/handbook.txt"); [] when nothing should be found
    evidence          optional: phrases the answering chunk contains (a chunk-level check)
    notes             what a correct answer says (for the LLM judge in evaluate.py)
    must_include      optional: strings the final answer must contain (evaluate.py)
    must_not_include  optional: strings it must never contain, e.g. restricted facts
    lang              optional: the question's language when it isn't English (language.py
                      codes: "hi", "hi-Latn" for Hinglish, "es", ...): graded here by
                      retrieval like the rest; its notes name the answer's language for
                      evaluate.py's judge

LEAK SUITE: for every restricted document, public-role searches must never
return any of its chunks — not for the golden questions, not for the
document's own sentences typed back verbatim, and not anywhere in what the
public retriever can reach at all (its keyword index, its intros, a dense
search with no limit). "Restricted" comes from the corpus folder, not from
chunk metadata, so a tagging bug is caught rather than trusted.
"""

import argparse
import contextlib
import hashlib
import io
import json
import math
import os
import re
import shutil
import sys
import tempfile
import unicodedata

from langchain_core.embeddings import Embeddings

EVAL_DIR = os.path.dirname(os.path.abspath(__file__))
CORPUS = os.path.join(EVAL_DIR, "corpus")
GOLDEN = os.path.join(EVAL_DIR, "golden", "default.jsonl")
BASELINE = os.path.join(EVAL_DIR, "baseline.json")
COLLECTION = "eval"
ROLES = ("public", "hr")
EXPECT = ("answer", "not_found", "blocked")
METRICS = ("hit_rate", "recall", "mrr", "evidence_hit_rate", "evidence_mrr")
# Allowed drop below the baseline before the gate fails: about one question
# flipping, so float noise between machines can't fail CI but a real
# regression does.
TOLERANCE = 0.03


def load_golden(path=GOLDEN):
    """The golden rows, checked for the fields every consumer relies on."""
    rows = []
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            for field in ("id", "question", "role", "expect", "sources", "notes"):
                if field not in row:
                    raise ValueError(f"{path}:{n}: missing '{field}'")
            if row["role"] not in ROLES or row["expect"] not in EXPECT:
                raise ValueError(f"{path}:{n}: bad role or expect")
            rows.append(row)
    if len({r["id"] for r in rows}) != len(rows):
        raise ValueError(f"{path}: duplicate ids")
    return rows


def _norm(text):
    """Text compared loosely: Unicode-normalised, case-folded, single spaces."""
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


class HashEmbeddings(Embeddings):
    """A deterministic stand-in for OpenAIEmbeddings — no network, no key.
    Each word and each character trigram of each word is hashed to one of
    `dim` buckets with a +/-1 sign; the counts are scaled to unit length."""

    def __init__(self, dim=1024):
        self.dim = dim

    def _vector(self, text):
        vec = [0.0] * self.dim
        for word in re.findall(r"\w+", unicodedata.normalize("NFKC", text).casefold()):
            padded = f"#{word}#"
            for feature in [word] + [padded[i:i + 3] for i in range(len(padded) - 2)]:
                h = int.from_bytes(hashlib.md5(feature.encode()).digest()[:8], "big")
                vec[h % self.dim] += 1.0 if (h >> 32) & 1 else -1.0
        size = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / size for v in vec]

    def embed_documents(self, texts):
        return [self._vector(t) for t in texts]

    def embed_query(self, text):
        return self._vector(text)


@contextlib.contextmanager
def fixture_index(offline=True, corpus=CORPUS):
    """Index the corpus into a throwaway Chroma folder with ingest.main, and
    point rag at it for the duration. offline=True swaps in HashEmbeddings;
    offline=False embeds for real (needs OPENAI_API_KEY). Search it with
    rag.load_chain(role, COLLECTION)."""
    import ingest
    import rag
    from chromadb.api.client import SharedSystemClient

    folder = tempfile.mkdtemp(prefix="kb-eval-index-")
    saved = [(m, name, getattr(m, name))
             for m in (ingest, rag) for name in ("DB_FOLDER", "OpenAIEmbeddings")]
    try:
        for module in (ingest, rag):
            module.DB_FOLDER = folder
            if offline:
                module.OpenAIEmbeddings = lambda **kwargs: HashEmbeddings()
        with contextlib.redirect_stdout(io.StringIO()):      # ingest prints progress
            ingest.main(docs_folder=corpus, collection=COLLECTION)
        yield folder
    finally:
        for module, name, value in saved:
            setattr(module, name, value)
        SharedSystemClient.clear_system_cache()             # let go of the folder
        shutil.rmtree(folder, ignore_errors=True)


def source_of(doc, corpus=CORPUS):
    """A chunk's document, relative to the corpus: 'public/handbook.txt'."""
    source = doc.metadata.get("source", "")
    try:
        return os.path.relpath(source, corpus).replace("\\", "/")
    except ValueError:                  # another drive on Windows
        return source.replace("\\", "/")


def score_retrieval(row, docs, corpus=CORPUS):
    """How well one search found a golden row's sources. `docs` is the ranked
    list the retriever returned (best first)."""
    got = [source_of(d, corpus) for d in docs]
    expected = set(row["sources"])
    ranks = [i for i, s in enumerate(got) if s in expected]
    evidence = [_norm(e) for e in row.get("evidence", [])]
    ev_ranks = [i for i, d in enumerate(docs)
                if any(e in _norm(d.page_content) for e in evidence)]
    return {
        "id": row["id"], "role": row["role"], "question": row["question"],
        "retrieved": got,
        "hit": bool(ranks),
        "recall": len(expected & set(got)) / len(expected),
        "rr": 1 / (ranks[0] + 1) if ranks else 0.0,
        "evidence_hit": bool(ev_ranks) if evidence else None,
        "evidence_rr": (1 / (ev_ranks[0] + 1) if ev_ranks else 0.0) if evidence else None,
    }


def summarize_retrieval(items):
    """Averages over scored rows: hit rate, recall@k and MRR by document, and
    the same by the chunk that holds the evidence (the stricter test)."""
    def mean(values):
        values = [v for v in values if v is not None]
        return round(sum(values) / len(values), 4) if values else None
    return {
        "n": len(items),
        "hit_rate": mean([float(i["hit"]) for i in items]),
        "recall": mean([i["recall"] for i in items]),
        "mrr": mean([i["rr"] for i in items]),
        "evidence_hit_rate": mean([None if i["evidence_hit"] is None else float(i["evidence_hit"])
                                   for i in items]),
        "evidence_mrr": mean([i["evidence_rr"] for i in items]),
    }


def retrieval_report(golden, corpus=CORPUS):
    """Search every golden question that names sources, as its own role.
    Call inside fixture_index()."""
    import rag
    retrievers = {}
    items = []
    for row in golden:
        if not row["sources"]:
            continue
        if row["role"] not in retrievers:
            retrievers[row["role"]] = rag.load_chain(row["role"], COLLECTION)[0]
        docs = retrievers[row["role"]].invoke(row["question"])
        items.append(score_retrieval(row, docs, corpus))
    return {"summary": summarize_retrieval(items), "items": items}


# ---- Leak suite -------------------------------------------------------------
def restricted_lines(corpus=CORPUS):
    """{document: its sentences} for every file under the corpus's restricted/
    folder. Lines shorter than 25 characters are too generic to prove a leak."""
    out = {}
    base = os.path.join(corpus, "restricted")
    for root, _, files in os.walk(base):
        for name in sorted(files):
            path = os.path.join(root, name)
            with open(path, encoding="utf-8") as f:
                lines = [_norm(line) for line in f]
            rel = os.path.relpath(path, corpus).replace("\\", "/")
            out[rel] = [line for line in lines if len(line) >= 25]
    return out


def leaked(docs, restricted, corpus=CORPUS):
    """The chunks among `docs` that come from a restricted document — by path,
    or by carrying one of its sentences (in case metadata lost the path)."""
    out = []
    for d in docs:
        src = source_of(d, corpus)
        text = _norm(d.page_content)
        if src in restricted or any(line in text for lines in restricted.values()
                                    for line in lines):
            out.append(d)
    return out


def leak_queries(golden, restricted):
    """Every golden question, plus each restricted document's own lines typed
    back verbatim (the closest a query can get to those chunks)."""
    queries = [row["question"] for row in golden]
    for lines in restricted.values():
        queries += lines
    return list(dict.fromkeys(queries))


def leak_report(golden, corpus=CORPUS, role="public"):
    """Every way `role` reached restricted text. [] means no leak.
    Call inside fixture_index()."""
    import rag
    restricted = restricted_lines(corpus)
    if not restricted:
        raise ValueError(f"no restricted documents under {corpus} - the leak suite would prove nothing")
    retriever = rag.load_chain(role, COLLECTION)[0]
    leaks = []

    def record(where, docs, query=None):
        for d in leaked(docs, restricted, corpus):
            leaks.append({"where": where, "query": query, "source": source_of(d, corpus),
                          "chunk": d.page_content[:120]})

    # 1. Everything this role can reach at all, whatever the query.
    record("keyword index", getattr(retriever.bm25, "docs", []))
    record("document intros", [d for docs in retriever.intros.values() for d in docs])
    dense = retriever.dense
    record("dense search, no limit", dense.vectorstore.similarity_search(
        "confidential salary contract plan", **{**dense.search_kwargs, "k": 10_000}))

    # 2. What the real search returns for questions aimed at restricted text.
    for q in leak_queries(golden, restricted):
        docs = retriever.invoke(q)
        record("search", docs + retriever.intros_for(docs), q)
    return leaks


# ---- Gate ------------------------------------------------------------------
def gate(summary, leaks, baseline):
    """Reasons to fail CI: any leak, or a metric below baseline - TOLERANCE."""
    failures = [f"LEAK ({leak['where']}): {leak['source']} for query {leak['query']!r}"
                for leak in leaks]
    for name in METRICS:
        want, got = baseline.get(name), summary.get(name)
        if want is not None and (got is None or got < want - TOLERANCE):
            failures.append(f"{name} {got} is below the baseline {want} (tolerance {TOLERANCE})")
    return failures


def run(golden_path=GOLDEN, corpus=CORPUS):
    golden = load_golden(golden_path)
    with fixture_index(offline=True, corpus=corpus):
        report = retrieval_report(golden, corpus)
        report["leaks"] = leak_report(golden, corpus)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--gate", action="store_true",
                        help="exit 1 on any leak or a metric below eval/baseline.json")
    parser.add_argument("--write-baseline", action="store_true",
                        help="save the current scores as eval/baseline.json")
    parser.add_argument("--json", help="also write the full report to this file")
    args = parser.parse_args(argv)

    report = run()
    s = report["summary"]
    print(f"Retrieval on {s['n']} golden questions (offline embedding, top-k as the app uses):")
    for name in METRICS:
        print(f"  {name:<18} {s[name]}")
    for item in report["items"]:
        if not item["hit"] or item["evidence_hit"] is False:
            print(f"  miss: {item['id']:<22} got {item['retrieved']}")
    print(f"Leak suite: {len(report['leaks'])} leak(s)")
    for leak in report["leaks"][:20]:
        print(f"  LEAK {leak['where']}: {leak['source']} <- {leak['query']!r}")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
    if args.write_baseline:
        if report["leaks"]:
            sys.exit("Refusing to write a baseline while restricted text leaks.")
        with open(BASELINE, "w", encoding="utf-8") as f:
            json.dump({name: s[name] for name in METRICS}, f, indent=2)
            f.write("\n")
        print(f"Wrote {os.path.relpath(BASELINE)}")
    if args.gate:
        with open(BASELINE, encoding="utf-8") as f:
            failures = gate(s, report["leaks"], json.load(f))
        for failure in failures:
            print("FAIL", failure)
        if failures:
            sys.exit(1)
        print("Gate passed.")


if __name__ == "__main__":
    main()
