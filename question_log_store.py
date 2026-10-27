"""
question_log_store.py  —  the question log (config/app.db, see db.py; tables made
by migrations/versions/0007_question_log_and_insights.py): one row per question
asked, whatever it was asked through, with its outcome, latency, tokens and the
documents it cited.

Only SQL lives here. What gets written (the text per the workspace's retention
mode, the outcome, the tokens) is decided in kb/services/question_log.py.

The workspace's logging settings live on its `workspaces` row, beside its other
settings (members_store.py reads those):

    question_log_mode   full       the question and answer as asked and given
                        redacted   the same with emails, phone and card numbers
                                   masked (safety.redact_pii), the default
                        metadata   no text at all: outcome, timings, sources
    question_log_days   how long the text is kept (then cleared; the rest stays
                        KEEP_DAYS for the counts)
"""

import json
import threading
import time

import db

MODES = ("full", "redacted", "metadata")
DEFAULT_MODE = "redacted"
DEFAULT_DAYS = 90
DAY_CHOICES = (30, 90, 180, 365)
KEEP_DAYS = 400                 # a row's metadata (no text) is deleted after this
SURFACES = ("web", "api", "slack", "mcp", "teams", "extension", "email")   # migration 0015

_lock = threading.Lock()


def _db():
    return db.connect()


def day_of(at):
    return time.strftime("%Y-%m-%d", time.gmtime(at))


# ---- Settings ---------------------------------------------------------------------------
def settings(workspace):
    """{"mode", "days"} of a workspace (the defaults for one with no row yet)."""
    with _db() as conn:
        row = conn.execute("SELECT question_log_mode, question_log_days FROM workspaces "
                           "WHERE slug = ?", (workspace,)).fetchone()
    if not row:
        return {"mode": DEFAULT_MODE, "days": DEFAULT_DAYS}
    return {"mode": row["question_log_mode"], "days": row["question_log_days"]}


def set_settings(workspace, mode, days):
    if mode not in MODES:
        raise ValueError(f"unknown question log mode {mode!r}")
    with _lock, _db() as conn:
        conn.execute("INSERT OR IGNORE INTO workspaces (slug, name, created_at) VALUES (?, ?, ?)",
                     (workspace, workspace, time.time()))
        conn.execute("UPDATE workspaces SET question_log_mode = ?, question_log_days = ? "
                     "WHERE slug = ?", (mode, int(days), workspace))
        if mode == "metadata":            # stop keeping text now, not in N days
            conn.execute("UPDATE question_log SET question = NULL, answer = NULL, "
                         "embedding = NULL, text_mode = 'metadata' WHERE workspace = ?",
                         (workspace,))


# ---- Rows -------------------------------------------------------------------------------
def add(workspace, user, surface, outcome, *, text_mode, question=None, answer=None,
        message_id=None, role="public", block_reason=None, sources=(), tools=(),
        max_access="restricted", latency_ms=None, tokens_in=None, tokens_out=None,
        model=None, at=None, tokens_cached=None, cache_hit=None, unsupported=None,
        language=None, provider=None):
    """Log one question. Text is stored as given: the caller has applied the mode.
    tokens_cached: how many of tokens_in OpenAI's prompt cache served (migration 0010).
    cache_hit: exact / semantic when the answer cache served it; unsupported: the
    figures the faithfulness check didn't find; language: the question's (0019).
    provider: openai / bedrock, who answered; model: the model that wrote most (0022)."""
    if surface not in SURFACES:
        raise ValueError(f"unknown surface {surface!r}")
    at = time.time() if at is None else at
    with _lock, _db() as conn:
        return conn.execute(
            """INSERT INTO question_log (workspace, at, day, user, surface, message_id, role,
                   text_mode, question, answer, outcome, block_reason, sources, tools,
                   max_access, latency_ms, tokens_in, tokens_out, model, tokens_cached,
                   cache_hit, unsupported, language, provider)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (workspace, at, day_of(at), user, surface, message_id, role, text_mode, question,
             answer, outcome, block_reason, json.dumps(list(sources)), json.dumps(list(tools)),
             max_access, latency_ms, tokens_in, tokens_out, model, tokens_cached, cache_hit,
             unsupported, language, provider)).lastrowid


def _row(r):
    out = {k: r[k] for k in r.keys() if k != "embedding"}
    out["sources"] = json.loads(out.get("sources") or "[]")
    out["tools"] = json.loads(out.get("tools") or "[]")
    return out


def rows(workspace, since_day=None, until_day=None, outcomes=None):
    """The workspace's rows in [since_day, until_day] (inclusive days), oldest first."""
    sql, args = "SELECT * FROM question_log WHERE workspace = ?", [workspace]
    if since_day:
        sql, args = sql + " AND day >= ?", args + [since_day]
    if until_day:
        sql, args = sql + " AND day <= ?", args + [until_day]
    if outcomes:
        sql += f" AND outcome IN ({','.join('?' * len(outcomes))})"
        args += list(outcomes)
    with _db() as conn:
        return [_row(r) for r in conn.execute(sql + " ORDER BY at, id", args)]


def get_many(ids):
    """{id: row} for these ids."""
    ids = list(ids)
    if not ids:
        return {}
    with _db() as conn:
        return {r["id"]: _row(r) for r in conn.execute(
            f"SELECT * FROM question_log WHERE id IN ({','.join('?' * len(ids))})", ids)}


def not_found(workspace, since_day, until_day, resolved=False, limit=500):
    """The questions in [since_day, until_day] the assistant couldn't answer from
    the documents, newest first: still open, or (resolved) the ones marked
    resolved (migration 0023)."""
    with _db() as conn:
        return [_row(r) for r in conn.execute(
            "SELECT * FROM question_log WHERE workspace = ? AND outcome = 'not_found' AND "
            f"day >= ? AND day <= ? AND resolved_at IS {'NOT ' if resolved else ''}NULL "
            "ORDER BY at DESC, id DESC LIMIT ?", (workspace, since_day, until_day, int(limit)))]


def get(workspace, log_id):
    """One of the workspace's rows, or None."""
    with _db() as conn:
        r = conn.execute("SELECT * FROM question_log WHERE id = ? AND workspace = ?",
                         (log_id, workspace)).fetchone()
    return _row(r) if r else None


def set_resolved(workspace, log_id, username=None):
    """Mark a row resolved by `username`, or open again (None). False if the
    workspace has no such row."""
    with _lock, _db() as conn:
        return conn.execute(
            "UPDATE question_log SET resolved_at = ?, resolved_by = ? WHERE id = ? AND "
            "workspace = ?", (time.time() if username else None, username, log_id,
                              workspace)).rowcount > 0


def active_users(workspace, since_day, until_day):
    """People who asked at least one question in [since_day, until_day]."""
    with _db() as conn:
        return conn.execute("SELECT COUNT(DISTINCT user) FROM question_log WHERE workspace = ? "
                            "AND day >= ? AND day <= ?",
                            (workspace, since_day, until_day)).fetchone()[0]


def surface_counts(workspace, since_day, until_day):
    """{surface: (questions, answered or partly)} in [since_day, until_day]."""
    with _db() as conn:
        return {r[0]: (r[1], r[2]) for r in conn.execute(
            "SELECT surface, COUNT(*), SUM(outcome IN ('answered', 'partial')) FROM question_log "
            "WHERE workspace = ? AND day >= ? AND day <= ? GROUP BY surface",
            (workspace, since_day, until_day))}


def days_with_rows(workspace, since_day=None):
    sql, args = "SELECT DISTINCT day FROM question_log WHERE workspace = ?", [workspace]
    if since_day:
        sql, args = sql + " AND day >= ?", args + [since_day]
    with _db() as conn:
        return [r[0] for r in conn.execute(sql + " ORDER BY day", args)]


def workspaces():
    """Every workspace that has logged a question."""
    with _db() as conn:
        return [r[0] for r in conn.execute("SELECT DISTINCT workspace FROM question_log")]


def redact_text(workspace, redact):
    """Apply `redact` to every question and answer still kept (switching to a
    stricter mode applies it to what was logged before, too)."""
    with _lock, _db() as conn:
        found = conn.execute("SELECT id, question, answer FROM question_log WHERE workspace = ? "
                             "AND (question IS NOT NULL OR answer IS NOT NULL)",
                             (workspace,)).fetchall()
        conn.executemany("UPDATE question_log SET question = ?, answer = ?, text_mode = "
                         "'redacted' WHERE id = ?",
                         [(redact(r["question"]), redact(r["answer"]), r["id"]) for r in found])
    return len(found)


def known_workspaces():
    """Every workspace there is (the workspaces table)."""
    with _db() as conn:
        return [r[0] for r in conn.execute("SELECT slug FROM workspaces")]


# ---- Embeddings (content gaps) ----------------------------------------------------------
def models_used(workspace, since_day, until_day):
    """The models that answered the workspace's questions in [since_day, until_day]."""
    with _db() as conn:
        return [r[0] for r in conn.execute(
            "SELECT DISTINCT model FROM question_log WHERE workspace = ? AND day >= ? AND "
            "day <= ? AND model IS NOT NULL ORDER BY model", (workspace, since_day, until_day))]


def clear_embeddings(workspace):
    """Forget the questions' vectors (the embedding model changed): Insights embeds
    them again with the new one."""
    with _lock, _db() as conn:
        conn.execute("UPDATE question_log SET embedding = NULL WHERE workspace = ?",
                     (workspace,))


def embeddings(ids):
    """{id: bytes} of the rows among `ids` that have one."""
    ids = list(ids)
    if not ids:
        return {}
    with _db() as conn:
        return {r["id"]: r["embedding"] for r in conn.execute(
            f"SELECT id, embedding FROM question_log WHERE embedding IS NOT NULL AND id IN "
            f"({','.join('?' * len(ids))})", ids)}


def set_embeddings(vectors):
    """vectors: {id: bytes}. Only rows that still have their text take one."""
    with _lock, _db() as conn:
        conn.executemany("UPDATE question_log SET embedding = ? WHERE id = ? "
                         "AND question IS NOT NULL", [(v, i) for i, v in vectors.items()])


# ---- Retention --------------------------------------------------------------------------
def purge(workspace, days, now=None, keep_days=KEEP_DAYS):
    """Clear the text of rows older than `days`, and delete rows older than
    keep_days. Returns (cleared, deleted)."""
    now = time.time() if now is None else now
    with _lock, _db() as conn:
        cleared = conn.execute(
            "UPDATE question_log SET question = NULL, answer = NULL, embedding = NULL "
            "WHERE workspace = ? AND at < ? AND (question IS NOT NULL OR answer IS NOT NULL "
            "OR embedding IS NOT NULL)", (workspace, now - days * 86400)).rowcount
        deleted = conn.execute("DELETE FROM question_log WHERE workspace = ? AND at < ?",
                               (workspace, now - keep_days * 86400)).rowcount
    return cleared, deleted
