"""
eval/  —  how we MEASURE the answer pipeline.

    corpus/            a small, versioned set of company documents (public/ and restricted/)
    golden/*.jsonl     questions about that corpus, with the documents that answer them
    harness.py         builds a search index of the corpus and scores RETRIEVAL;
                       needs no OpenAI key (a deterministic stand-in embedding),
                       so it runs in pytest and CI, including the LEAK SUITE
    baseline.json      the retrieval scores CI must not fall below

The answer-quality evaluation (an LLM judge, needs a real key) is ../evaluate.py.
"""
