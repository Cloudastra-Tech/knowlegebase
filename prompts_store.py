"""
prompts_store.py  —  saved prompts and templates (chat.md C22; config/app.db,
table made by migrations/versions/0018_spaces_attachments_prompts.py):

    prompt_templates    a person's own prompts (owner = their username) and the
                        workspace's (owner NULL), which its admins keep. A body
                        may hold {{variables}}, filled in before it is asked.
                        `search_knowledge` (0027): 1 runs it as any question,
                        searching the knowledge first; 0 answers it directly,
                        nothing searched or cited (kb/routers/chat.py,
                        agent.run(search=)). Read back as `search`, a bool.

variables() finds them; fill() puts values in. Only these and SQL live here; who
may change which is kb/routers/prompts.py.
"""

import contextlib
import json
import re
import time
import uuid

import db

TITLE_CHARS = 80
BODY_CHARS = 2000           # a filled-in template still has to fit a question (500 asked)
MAX_VARIABLES = 10
MAX_PER_OWNER = 200
VALUE_CHARS = 200
# {{customer}}, {{ start date }}: a letter first, then letters, digits, spaces, _ or -.
VARIABLE = re.compile(r"\{\{\s*([A-Za-z][A-Za-z0-9 _-]{0,39}?)\s*\}\}")


def variables(body):
    """The names of the {{variables}} in `body`, each once, in order of first use."""
    return list(dict.fromkeys(m.group(1) for m in VARIABLE.finditer(body or "")))


def fill(body, values):
    """`body` with each {{variable}} replaced by its value (one line, cut at
    VALUE_CHARS). A variable with no value is left as it is."""
    def put(m):
        value = (values or {}).get(m.group(1))
        if value is None:
            return m.group(0)
        return " ".join(str(value).split())[:VALUE_CHARS]
    return VARIABLE.sub(put, body or "")


@contextlib.contextmanager
def _db():
    conn = db.connect()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _row(r):
    if not r:
        return None
    out = dict(r)
    out["variables"] = json.loads(out["variables"] or "[]")
    out["search"] = bool(out.pop("search_knowledge", 1))
    return out


def visible(workspace, username):
    """The person's own templates and the workspace's, workspace ones first."""
    with _db() as conn:
        return [_row(r) for r in conn.execute(
            "SELECT * FROM prompt_templates WHERE workspace = ? AND (owner IS NULL OR owner = ?) "
            "ORDER BY owner IS NOT NULL, title COLLATE NOCASE", (workspace, username))]


def get(workspace, prompt_id):
    with _db() as conn:
        return _row(conn.execute("SELECT * FROM prompt_templates WHERE id = ? AND workspace = ?",
                                 (prompt_id, workspace)).fetchone())


def count(workspace, owner):
    with _db() as conn:
        if owner is None:
            return conn.execute("SELECT COUNT(*) FROM prompt_templates WHERE workspace = ? AND "
                                "owner IS NULL", (workspace,)).fetchone()[0]
        return conn.execute("SELECT COUNT(*) FROM prompt_templates WHERE workspace = ? AND "
                            "owner = ?", (workspace, owner)).fetchone()[0]


def create(workspace, owner, title, body, created_by, search=True):
    """owner None: a workspace template. search False: answered without searching."""
    now = time.time()
    pid = uuid.uuid4().hex
    with _db() as conn:
        conn.execute("INSERT INTO prompt_templates (id, workspace, owner, title, body, variables, "
                     "search_knowledge, created_by, created_at, updated_at) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (pid, workspace, owner, title, body, json.dumps(variables(body)),
                      int(bool(search)), created_by, now, now))
    return get(workspace, pid)


def update(workspace, prompt_id, title, body, search=None):
    """search None: left as it is."""
    with _db() as conn:
        conn.execute("UPDATE prompt_templates SET title = ?, body = ?, variables = ?, "
                     "search_knowledge = COALESCE(?, search_knowledge), updated_at = ? "
                     "WHERE id = ? AND workspace = ?",
                     (title, body, json.dumps(variables(body)),
                      None if search is None else int(bool(search)), time.time(), prompt_id,
                      workspace))
    return get(workspace, prompt_id)


def searches(workspace, username, prompt_id):
    """Whether running the prompt `prompt_id` searches the knowledge: False only
    for one `username` may use (theirs or the workspace's) that is set not to.
    Anything else (no id, gone, someone else's) searches, as any question."""
    if not prompt_id:
        return True
    p = get(workspace, prompt_id)
    if not p or (p["owner"] is not None and p["owner"] != username):
        return True
    return p["search"]


def delete(workspace, prompt_id):
    with _db() as conn:
        return conn.execute("DELETE FROM prompt_templates WHERE id = ? AND workspace = ?",
                            (prompt_id, workspace)).rowcount > 0
