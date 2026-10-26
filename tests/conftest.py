"""
Point every test at throwaway files BEFORE any app module is imported (the
stores read these paths once, at import), so no test can touch config/app.db
or the real accounts, whichever test file pytest collects first.

The developer's .env is never loaded either, so tests run with the same
settings here as in CI (which has no .env).
"""

import atexit
import os
import shutil
import tempfile

import dotenv
import pytest

dotenv.load_dotenv = lambda *args, **kwargs: False

_tmp = tempfile.mkdtemp(prefix="kb-tests-")
atexit.register(shutil.rmtree, _tmp, ignore_errors=True)
os.environ.setdefault("OPENAI_API_KEY", "sk-test")
os.environ["RATE_LIMITS"] = "off"         # every test signs in from the same address
os.environ.update(APP_DB=os.path.join(_tmp, "app.db"),
                  USERS_FILE=os.path.join(_tmp, "users.json"),
                  SSO_CONFIG=os.path.join(_tmp, "sso.json"))


@pytest.fixture
def jobs_paused(monkeypatch):
    """No job runner in this process: jobs stay queued until the test runs them
    (kb.jobs.Runner(...).run_once()). The next test that queues one starts it again."""
    from kb import jobs
    monkeypatch.setenv("JOBS_IN_PROCESS", "off")
    jobs.stop_in_process()
