"""
BILLING=off vs on over a representative slice of the suite: the rest of the suite
runs with billing off (the default); here the slice runs again in its own pytest,
with BILLING=on and every workspace on a plan that includes everything
(tests/conftest.py, KB_TEST_PLAN). The gated routers (SCIM, the audit export, API
keys, MCP, Slack, Teams), questions, uploads and connectors must behave exactly as
they do with billing off. tests/test_billing.py has what changes when a plan
doesn't include something.
"""

import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SLICE = ("tests/test_scim.py", "tests/test_audit.py", "tests/test_api_keys.py",
         "tests/test_mcp.py", "tests/test_teams.py", "tests/test_chat.py",
         "tests/test_member_isolation.py", "tests/test_onboarding.py")


@pytest.mark.parametrize("plan", ["enterprise"])
def test_a_slice_of_the_suite_passes_the_same_with_billing_on(plan):
    env = {**os.environ, "BILLING": "on", "KB_TEST_PLAN": plan}
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider",
                        "-W", "ignore", *SLICE], cwd=ROOT, env=env, capture_output=True,
                       text=True, timeout=1800)
    assert r.returncode == 0, r.stdout[-4000:] + r.stderr[-2000:]
    assert " passed" in r.stdout and " failed" not in r.stdout
