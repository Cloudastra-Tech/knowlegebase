"""The agent's calculator: arithmetic works, nothing else runs."""

import os

import pytest

os.environ.setdefault("OPENAI_API_KEY", "sk-test")
from agent import calculate  # noqa: E402


@pytest.mark.parametrize("expression, value", [
    ("2000*12", 24000),
    ("24000*0.8", 19200.0),
    ("(1500 + 500) / 4", 500.0),
    ("-2**3 + 7 % 4 + 9 // 2", -1),
    ("2,000 * 12", 24000),             # models sometimes write thousands separators
])
def test_does_arithmetic(expression, value):
    assert calculate(expression) == value


@pytest.mark.parametrize("expression", [
    "().__class__.__bases__[0].__subclasses__()",   # escapes eval() with no builtins
    "__import__('os').system('id')",
    "open('/etc/passwd').read()",
    "[1, 2, 3]",
    "9**9**9",                                       # would hang the server
    "1+" * 150 + "1",
])
def test_refuses_anything_but_arithmetic(expression):
    with pytest.raises(ValueError):
        calculate(expression)
