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
# Tests ask the same questions again and again and expect the model to answer
# each time; tests/test_answer_cache.py turns the answer cache on for itself.
os.environ["ANSWER_CACHE"] = "off"
# Starting the app doesn't rebuild indexes other tests left with other settings.
os.environ["REINDEX_ON_SETTINGS_CHANGE"] = "off"
# No model calls for a chat's title or its follow-ups (kb/services/suggestions.py):
# the question's own words, and follow-ups from the cited passages' headings.
os.environ.update(CHAT_AUTO_TITLE="off", CHAT_FOLLOWUPS="headings")
os.environ.update(APP_DB=os.path.join(_tmp, "app.db"),
                  USERS_FILE=os.path.join(_tmp, "users.json"),
                  SSO_CONFIG=os.path.join(_tmp, "sso.json"))


if os.getenv("KB_TEST_PLAN"):
    # tests/test_billing_off_or_on.py runs a slice of the suite again with BILLING=on
    # and every workspace without a subscription on this plan, active: a workspace
    # whose plan includes everything must work exactly as with billing off.
    @pytest.fixture(autouse=True)
    def on_a_paid_plan(monkeypatch):
        import billing_store
        stored = billing_store.subscription

        def subscription(workspace):
            return stored(workspace) or {
                "workspace": workspace, "plan": os.environ["KB_TEST_PLAN"], "status": "active",
                "trial_ends_at": None, "stripe_customer": None, "stripe_subscription": None,
                "seats": 0, "current_period_end": None, "reminders": [],
                "deletion_scheduled_at": None, "created_at": 0, "updated_at": 0}
        monkeypatch.setattr(billing_store, "subscription", subscription)


@pytest.fixture
def jobs_paused(monkeypatch):
    """No job runner in this process: jobs stay queued until the test runs them
    (kb.jobs.Runner(...).run_once()). The next test that queues one starts it again."""
    from kb import jobs
    monkeypatch.setenv("JOBS_IN_PROCESS", "off")
    jobs.stop_in_process()
