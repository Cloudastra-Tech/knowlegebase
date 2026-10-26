"""
models.py  —  which OpenAI model does which job (pipeline.md P1-5, model routing).

    ANSWER_MODEL   writes the answer the user reads (agent.py's agent node)
    FAST_MODEL     the small jobs: the guards' classifiers (safety.py), rewriting a
                   follow-up, HyDE, multi-query variants (rag.py), the optional
                   chunk context at indexing (ingest.py) and insights labels

Both default to gpt-4o-mini, the model every step used before, so nothing
changes until they are set in .env. A cheaper FAST_MODEL (gpt-4.1-nano,
gpt-5-nano) is the likely saving; check the leak and injection suites before
switching (python -m eval.harness --gate; python evaluate.py --redteam), since
the guards' classifiers run on it.

PROMPT CACHING. OpenAI caches the longest repeated PREFIX of a prompt
automatically once a prompt is long enough (1,024 tokens on current models), at
a discount on those input tokens. So prompts here put what never changes first
(the agent's rules, its tool list) and what changes last (the conversation, the
passages found). prompt_cache_key() groups requests that share that prefix, so
OpenAI routes them to the same cache: one key per workspace and role keeps one
workspace's traffic from probing another's cache. The tokens served from the
cache are logged per question (question_log tokens_cached).
"""

import os

DEFAULT_MODEL = "gpt-4o-mini"


def answer_model():
    return (os.getenv("ANSWER_MODEL") or "").strip() or DEFAULT_MODEL


def fast_model():
    return (os.getenv("FAST_MODEL") or "").strip() or DEFAULT_MODEL


def prompt_cache_key(collection, role):
    """The prompt_cache_key for a workspace's (collection's) agent for one role."""
    return f"kb:{collection or 'langchain'}:{role}"[:64]
