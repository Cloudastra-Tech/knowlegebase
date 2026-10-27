"""
python tests/e2e/server.py  —  the app the end-to-end tests (frontend/e2e/) drive:
the built site (frontend/dist, `npm run build` first) and the API on one port,
as in production, with

    - every store in a new temp folder (deleted on exit): config/app.db, docs/,
      chroma_db/ — never the developer's config/ or .env
    - the fake OpenAI server (fake_openai.py) on the next port, so no key and no
      network: OPENAI_BASE_URL points at it
    - the default workspace seeded with two accounts (ACCOUNTS: an admin and a
      public member, made the way `python auth.py add` makes them, so the audit
      log has their rows) and a small corpus indexed: two public policies and
      one HR-only plan (docs/restricted/)

Playwright starts it (frontend/playwright.config.ts) and waits for
/api/health/ready. Ports: E2E_PORT (default 8045) and E2E_PORT + 1.
"""

import atexit
import os
import shutil
import signal
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
PORT = int(os.getenv("E2E_PORT", "8045"))
OPENAI_PORT = PORT + 1
# Also in frontend/e2e/accounts.ts.
ACCOUNTS = (("admin@e2e.example", "Ada Admin", "admin-password-1", "admin"),
            ("member@e2e.example", "Pat Member", "member-password-1", "public"))
CORPUS = (("public", "leave_policy.txt"), ("public", "student_faq.txt"),
          ("restricted", "hiring_plan_2027.txt"))


def environment(tmp):
    os.environ.update(
        APP_DB=os.path.join(tmp, "config", "app.db"),
        USERS_FILE=os.path.join(tmp, "config", "users.json"),
        SSO_CONFIG=os.path.join(tmp, "config", "sso.json"),
        OPENAI_API_KEY="sk-e2e-fake", OPENAI_BASE_URL=f"http://127.0.0.1:{OPENAI_PORT}/v1",
        PUBLIC_URL=f"http://127.0.0.1:{PORT}",
        RATE_LIMITS="off",                     # every test signs in from one address
        CHAT_AUTO_TITLE="off",                 # titles and follow-ups without model calls
        CHAT_FOLLOWUPS="headings",
        REINDEX_ON_SETTINGS_CHANGE="off", RERANKER="none",
        OUTLINE_API_TOKEN="", LOG_FORMAT="text")
    for name in ("INTEGRATIONS_KEY", "SENTRY_DSN"):   # a key is made in the temp config/
        os.environ.pop(name, None)
    # Nobody's .env either (kb/__init__.py would load the repo's): as tests/conftest.py.
    import dotenv
    dotenv.load_dotenv = lambda *args, **kwargs: False


def fake_openai():
    import uvicorn
    from fake_openai import app               # beside this file
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=OPENAI_PORT,
                                           log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            return
        time.sleep(0.05)
    sys.exit(f"The fake OpenAI server didn't start on port {OPENAI_PORT}")


def seed(tmp):
    for access, name in CORPUS:
        os.makedirs(os.path.join(tmp, "docs", access), exist_ok=True)
        shutil.copy(os.path.join(ROOT, "eval", "corpus", access, name),
                    os.path.join(tmp, "docs", access, name))
    import auth
    import members_store
    import workspaces
    for username, name, password, role in ACCOUNTS:
        members_store.set_password(username, name, auth.hash_password(password),
                                   workspaces.DEFAULT, role)
        auth._audit(workspaces.DEFAULT, "member.add", username, {"role": role})
    # As `python ingest.py` does: here, not as a job.
    os.environ["JOBS_IN_PROCESS"] = "off"
    from kb.services import indexing
    result = indexing.rebuild_now(workspaces.DEFAULT)
    del os.environ["JOBS_IN_PROCESS"]
    print(f"e2e: indexed {result['documents']} documents ({result['restricted']} restricted)",
          flush=True)


def main():
    if not os.path.isfile(os.path.join(ROOT, "frontend", "dist", "index.html")):
        sys.exit("Build the site first: cd frontend && npm run build")
    tmp = tempfile.mkdtemp(prefix="kb-e2e-")
    atexit.register(shutil.rmtree, tmp, ignore_errors=True)
    environment(tmp)
    sys.path.insert(0, ROOT)
    os.chdir(tmp)                      # docs/ and chroma_db/ are relative to it
    fake_openai()
    seed(tmp)
    import uvicorn
    # uvicorn stops on SIGTERM (how Playwright ends it) and then raises it again:
    # as SystemExit, so the temp folder is deleted (atexit) on the way out.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    print(f"e2e: serving on http://127.0.0.1:{PORT}", flush=True)
    uvicorn.run("api:app", host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
