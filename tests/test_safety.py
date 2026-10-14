"""Input guards that need no LLM call and no Guardrails Hub install."""

import os

os.environ.setdefault("OPENAI_API_KEY", "sk-test")
from safety import MAX_QUESTION_CHARS, check_question  # noqa: E402


def test_overlong_questions_are_refused_without_the_hub():
    # allow_restricted=True skips the salary check, which would call the LLM.
    assert check_question("a" * MAX_QUESTION_CHARS, allow_restricted=True) == (True, "")
    ok, _ = check_question("a" * (MAX_QUESTION_CHARS + 1), allow_restricted=True)
    assert not ok
