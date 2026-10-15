"""
Point every test at throwaway files BEFORE any app module is imported (the
stores read these paths once, at import), so no test can touch config/app.db
or the real accounts, whichever test file pytest collects first.
"""

import atexit
import os
import shutil
import tempfile

_tmp = tempfile.mkdtemp(prefix="kb-tests-")
atexit.register(shutil.rmtree, _tmp, ignore_errors=True)
os.environ.setdefault("OPENAI_API_KEY", "sk-test")
os.environ.update(APP_DB=os.path.join(_tmp, "app.db"),
                  USERS_FILE=os.path.join(_tmp, "users.json"),
                  SSO_CONFIG=os.path.join(_tmp, "sso.json"))
