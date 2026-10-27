"""
insights_store.py  —  what the admin Insights screen reads (config/app.db, see
db.py; tables made by migrations/versions/0007_question_log_and_insights.py):

    insights_daily    the question log rolled up per workspace and day
    source_stats      citations per document and day
    insights_docs     the documents the assistant can search, with when each was
                      first seen
    content_gaps      the latest clusters of unanswered questions
    eval_candidates   answers promoted from feedback to the test set

Only SQL lives here; kb/services/insights.py computes what goes in.
"""

import json
import threading
import time

import db

_lock = threading.Lock()
DAILY_COUNTS = ("questions", "active_users", "answered", "partial", "not_found", "blocked",
                "general_knowledge", "error", "stopped", "tokens_in", "tokens_out")


def _db():
    return db.connect()


# ---- insights_daily and source_stats ----------------------------------------------------
def save_day(workspace, day, totals, latency_hist, cost_usd, sources):
    """Replace one day's rollup: its totals (DAILY_COUNTS), latency histogram, cost,
    and sources: {doc_key: {"title", "provider", "url", "access", "cited"}}."""
    with _lock, _db() as conn:
        conn.execute(
            f"""INSERT OR REPLACE INTO insights_daily (workspace, day, {', '.join(DAILY_COUNTS)},
                    latency_hist, cost_usd, computed_at)
                VALUES (?, ?, {', '.join('?' * len(DAILY_COUNTS))}, ?, ?, ?)""",
            (workspace, day, *(totals.get(k, 0) for k in DAILY_COUNTS),
             json.dumps(latency_hist), cost_usd, time.time()))
        conn.execute("DELETE FROM source_stats WHERE workspace = ? AND day = ?", (workspace, day))
        conn.executemany(
            "INSERT INTO source_stats (workspace, doc_key, day, title, provider, url, access, "
            "cited) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(workspace, key, day, s.get("title"), s.get("provider"), s.get("url"),
              s.get("access") or "restricted", s["cited"]) for key, s in sources.items()])


def days(workspace, since_day, until_day):
    """{day: rollup} in [since_day, until_day]."""
    with _db() as conn:
        out = {}
        for r in conn.execute("SELECT * FROM insights_daily WHERE workspace = ? AND day >= ? "
                              "AND day <= ? ORDER BY day", (workspace, since_day, until_day)):
            row = dict(r)
            row["latency_hist"] = json.loads(row["latency_hist"] or "[]")
            out[row["day"]] = row
        return out


def cited(workspace, since_day, until_day):
    """[{doc_key, title, provider, url, access, cited}] in the range, most cited first."""
    with _db() as conn:
        return [dict(r) for r in conn.execute(
            """SELECT doc_key, MAX(title) AS title, MAX(provider) AS provider, MAX(url) AS url,
                      MAX(access) AS access, SUM(cited) AS cited
                 FROM source_stats WHERE workspace = ? AND day >= ? AND day <= ?
                GROUP BY doc_key ORDER BY cited DESC, doc_key""",
            (workspace, since_day, until_day))]


def ever_cited(workspace):
    """Every doc_key with a citation on record."""
    with _db() as conn:
        return {r[0] for r in conn.execute(
            "SELECT DISTINCT doc_key FROM source_stats WHERE workspace = ? AND cited > 0",
            (workspace,))}


# ---- insights_docs ----------------------------------------------------------------------
def save_docs(workspace, docs, now=None):
    """Make insights_docs what the workspace can search now. docs: {doc_key: {"title",
    "provider", "url", "access", "modified", "since"}}; since is the earliest it may
    have been searchable (a file's time), first_seen keeps the earliest known."""
    now = time.time() if now is None else now
    with _lock, _db() as conn:
        conn.executemany(
            """INSERT INTO insights_docs (workspace, doc_key, title, provider, url, access,
                   modified, first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(workspace, doc_key) DO UPDATE SET title = excluded.title,
                   provider = excluded.provider, url = excluded.url, access = excluded.access,
                   modified = excluded.modified, last_seen = excluded.last_seen,
                   first_seen = MIN(insights_docs.first_seen, excluded.first_seen)""",
            [(workspace, key, d.get("title"), d.get("provider"), d.get("url"),
              d.get("access") or "restricted", d.get("modified"),
              min(now, d.get("since") or now), now) for key, d in docs.items()])
        keys = list(docs)
        conn.execute(f"DELETE FROM insights_docs WHERE workspace = ? AND doc_key NOT IN "
                     f"({','.join('?' * len(keys))})", (workspace, *keys))


def docs(workspace):
    with _db() as conn:
        return {r["doc_key"]: dict(r) for r in conn.execute(
            "SELECT * FROM insights_docs WHERE workspace = ?", (workspace,))}


# ---- content_gaps -----------------------------------------------------------------------
def save_gaps(workspace, since, clusters, now=None):
    """Replace the workspace's clusters. clusters: [{"label", "size", "not_found",
    "general_knowledge", "max_access", "log_ids"}]."""
    now = time.time() if now is None else now
    with _lock, _db() as conn:
        conn.execute("DELETE FROM content_gaps WHERE workspace = ?", (workspace,))
        conn.executemany(
            "INSERT INTO content_gaps (workspace, computed_at, since, label, size, not_found, "
            "general_knowledge, max_access, log_ids) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(workspace, now, since, c.get("label"), c["size"], c.get("not_found", 0),
              c.get("general_knowledge", 0), c.get("max_access") or "restricted",
              json.dumps(c["log_ids"])) for c in clusters])


def gaps(workspace):
    with _db() as conn:
        out = []
        for r in conn.execute("SELECT * FROM content_gaps WHERE workspace = ? "
                              "ORDER BY size DESC, id", (workspace,)):
            row = dict(r)
            row["log_ids"] = json.loads(row["log_ids"] or "[]")
            out.append(row)
        return out


# ---- eval_candidates --------------------------------------------------------------------
def _candidate(r):
    c = dict(r)
    c["sources"] = json.loads(c.get("sources") or "[]")
    c["reasons"] = json.loads(c.get("reasons") or "[]")
    return c


def add_candidate(workspace, message_id, question, answer, expected_answer, sources, reasons,
                  comment, role, max_access, created_by):
    """A golden-set candidate; promoting the same answer again updates it (and one the
    weekly job suggested becomes the admin's)."""
    with _lock, _db() as conn:
        conn.execute(
            """INSERT INTO eval_candidates (workspace, message_id, question, answer,
                   expected_answer, sources, reasons, comment, role, max_access, created_by,
                   created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(workspace, message_id) DO UPDATE SET
                   expected_answer = excluded.expected_answer, created_by = excluded.created_by,
                   created_at = excluded.created_at, origin = 'admin'""",
            (workspace, message_id, question, answer, expected_answer, json.dumps(sources),
             json.dumps(reasons), comment, role, max_access, created_by, time.time()))
        return _candidate(conn.execute(
            "SELECT * FROM eval_candidates WHERE workspace = ? AND message_id = ?",
            (workspace, message_id)).fetchone())


def suggest_candidate(workspace, message_id, question, answer, sources, reasons, comment,
                      role, max_access, origin, now=None):
    """A candidate the weekly job suggests (origin: feedback / not_found). Never
    replaces one already there (an admin's above all). True when it's new."""
    with _lock, _db() as conn:
        return bool(conn.execute(
            """INSERT OR IGNORE INTO eval_candidates (workspace, message_id, question, answer,
                   expected_answer, sources, reasons, comment, role, max_access, created_by,
                   created_at, origin) VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, 'system', ?, ?)""",
            (workspace, message_id, question, answer, json.dumps(sources), json.dumps(reasons),
             comment, role, max_access, time.time() if now is None else now, origin)).rowcount)


def feedback_for(workspace, message_id):
    """The newest feedback on an answer (conversations_store's message_feedback), with
    its snapshot of the question, answer and sources, or None."""
    with _db() as conn:
        r = conn.execute("SELECT * FROM message_feedback WHERE workspace = ? AND message_id = ? "
                         "ORDER BY rating, updated_at DESC LIMIT 1",
                         (workspace, message_id)).fetchone()
    if not r:
        return None
    f = dict(r)
    f["reasons"] = json.loads(f.get("reasons") or "[]")
    f["snapshot"] = json.loads(f.get("snapshot") or "{}")
    return f


def candidates(workspace):
    with _db() as conn:
        return [_candidate(r) for r in conn.execute(
            "SELECT * FROM eval_candidates WHERE workspace = ? ORDER BY created_at DESC, id DESC",
            (workspace,))]


def delete_candidate(workspace, cid):
    with _lock, _db() as conn:
        return bool(conn.execute("DELETE FROM eval_candidates WHERE workspace = ? AND id = ?",
                                 (workspace, cid)).rowcount)
