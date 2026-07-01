"""
evaluate.py  —  MEASURE how good your RAG answers are (RAGAS-style metrics).

Instead of guessing "is my app good?", we SCORE it. For each test question we run
the real pipeline, then an LLM "judge" rates three things (0-10):

  1. Context Relevance  — were the RETRIEVED chunks relevant to the question?
                          (measures your RETRIEVAL: BM25 + Chroma + RRF + rerank)
  2. Groundedness       — is the ANSWER supported by those chunks, not made up?
                          (measures HALLUCINATION — the whole point of RAG)
  3. Answer Relevance   — does the answer actually ADDRESS the question?
                          (measures the final answer quality)

This is exactly what the RAGAS library measures; we do it transparently here so
you can see how it works. Run:  python evaluate.py
"""

import re
import sys
import warnings
warnings.filterwarnings("ignore")

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from rag import load_chain, answer_question

load_dotenv()

# A separate model used ONLY as the JUDGE (scores answers; doesn't write them).
judge_llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)

# CI THRESHOLDS — minimum AVERAGE score (out of 10) each metric must hit.
# If any average falls below its threshold, the run FAILS (exit code 1) so a
# CI pipeline (e.g. GitHub Actions) catches a silent quality regression.
THRESHOLDS = {"context": 6.0, "ground": 7.0, "answer": 7.0}

# Test questions a public user might ask (these should all pass the guards).
TEST_QUESTIONS = [
    "What is the refund policy?",
    "What is the student stipend?",
    "What are the support hours?",
    "How much does the Pro plan cost?",
]


def _score(prompt):
    """Ask the judge for a 0-10 score and pull the number out."""
    text = judge_llm.invoke(prompt).content
    nums = re.findall(r"\d+", text)
    return min(int(nums[0]), 10) if nums else 0


def context_relevance(question, context):
    return _score(
        f"On a scale of 0-10, how RELEVANT is this retrieved context to the "
        f"question? Reply with only a number.\n\n"
        f"Question: {question}\n\nContext: {context}\n\nScore:"
    )


def groundedness(answer, context):
    return _score(
        f"On a scale of 0-10, how well is this ANSWER SUPPORTED by the context "
        f"(0 = makes things up, 10 = fully supported)? Reply with only a number.\n\n"
        f"Context: {context}\n\nAnswer: {answer}\n\nScore:"
    )


def answer_relevance(question, answer):
    return _score(
        f"On a scale of 0-10, how well does this ANSWER address the question? "
        f"Reply with only a number.\n\n"
        f"Question: {question}\n\nAnswer: {answer}\n\nScore:"
    )


def main():
    retriever, llm = load_chain("public")

    print(f"{'Question':<38}{'Context':>9}{'Ground':>9}{'Answer':>9}")
    print("-" * 65)

    totals = [0, 0, 0]
    for q in TEST_QUESTIONS:
        answer, docs = answer_question(q, retriever, llm, "public")
        context = "\n\n".join(d.page_content for d in docs)

        cr = context_relevance(q, context)
        gr = groundedness(answer, context)
        ar = answer_relevance(q, answer)
        totals[0] += cr; totals[1] += gr; totals[2] += ar

        print(f"{q[:36]:<38}{cr:>7}/10{gr:>7}/10{ar:>7}/10")

    n = len(TEST_QUESTIONS)
    avg = {"context": totals[0] / n, "ground": totals[1] / n, "answer": totals[2] / n}
    print("-" * 65)
    print(f"{'AVERAGE':<38}{avg['context']:>6.1f}/10{avg['ground']:>6.1f}/10{avg['answer']:>6.1f}/10")
    print("\nHigher = better. Context=retrieval quality, Ground=no hallucination, "
          "Answer=addresses the question.")

    # ---- CI gate: compare each average to its threshold ----
    print("\nCI thresholds:")
    failures = []
    for key, label in (("context", "Context"), ("ground", "Ground"), ("answer", "Answer")):
        need = THRESHOLDS[key]
        got = avg[key]
        ok = got >= need
        print(f"  {label:<8} {got:>5.1f} / need {need:>4.1f}  ->  {'PASS' if ok else 'FAIL'}")
        if not ok:
            failures.append(label)

    if failures:
        print(f"\n[FAILED] EVAL FAILED - below threshold: {', '.join(failures)}")
        sys.exit(1)        # non-zero exit -> CI marks the build as failed
    print("\n[PASSED] EVAL PASSED - all metrics meet their thresholds.")


if __name__ == "__main__":
    main()
