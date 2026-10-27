"""
verified_store.py  —  verified answers (config/app.db, tables made by
migrations/versions/0012_verified_answers_and_assistant_settings.py):

    verified_answers          one row per approved answer: its title, the question
                              variants it answers, the answer, the documents it
                              rests on (keys), who it's for (access), its owner,
                              when it was verified and how often it's reviewed
    verified_answer_vectors   its title and variants, embedded, for matching

Only SQL lives here; what's checked, matched and served is kb/services/verified.py.
"""

import contextlib
import json
import time
import uuid

import db

STATUSES = ("current", "due", "expired")
_JSON = ("variants", "sources")


@contextlib.contextmanager
def _db():
    conn = db.connect()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _row(row):
    if not row:
        return None
    out = dict(row)
    for key in _JSON:
        out[key] = json.loads(out[key] or "[]")
    return out


def get(workspace, answer_id):
    with _db() as conn:
        return _row(conn.execute("SELECT * FROM verified_answers WHERE id = ? AND workspace = ?",
                                 (answer_id, workspace)).fetchone())


def list_for(workspace):
    with _db() as conn:
        return [_row(r) for r in conn.execute(
            "SELECT * FROM verified_answers WHERE workspace = ? ORDER BY title COLLATE NOCASE",
            (workspace,))]


def count(workspace):
    with _db() as conn:
        return conn.execute("SELECT COUNT(*) FROM verified_answers WHERE workspace = ?",
                            (workspace,)).fetchone()[0]


def workspaces():
    with _db() as conn:
        return [r[0] for r in conn.execute("SELECT DISTINCT workspace FROM verified_answers")]


def stamp(workspace):
    """Something that changes whenever any of the workspace's answers does."""
    with _db() as conn:
        row = conn.execute("SELECT COUNT(*), MAX(updated_at) FROM verified_answers "
                           "WHERE workspace = ?", (workspace,)).fetchone()
    return tuple(row)


def create(workspace, fields, by):
    """A new answer from checked fields: title, variants, answer_md, sources, access,
    min_access, owner, review_days, from_message, from_gap. Verified now, by `by`."""
    now = time.time()
    answer_id = uuid.uuid4().hex
    with _db() as conn:
        conn.execute("""INSERT INTO verified_answers (id, workspace, title, variants, answer_md,
                            sources, access, min_access, owner, verified_by, verified_at,
                            review_days, status, from_message, from_gap, created_by,
                            created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'current', ?, ?, ?, ?, ?)""",
                     (answer_id, workspace, fields["title"], json.dumps(fields["variants"]),
                      fields["answer_md"], json.dumps(fields["sources"]), fields["access"],
                      fields.get("min_access") or "public", fields["owner"], by, now,
                      fields["review_days"], fields.get("from_message"), fields.get("from_gap"),
                      by, now, now))
    return get(workspace, answer_id)


def update(workspace, answer_id, fields, by, reverify=True):
    """Change an answer (any of create's fields). An edit is a new verification by
    `by` (reverify), so its review date starts again."""
    sets = {k: json.dumps(v) if k in _JSON else v for k, v in fields.items()}
    now = time.time()
    if reverify:
        sets.update(verified_by=by, verified_at=now, status="current", notified=None)
    sets["updated_at"] = now
    with _db() as conn:
        cur = conn.execute(f"UPDATE verified_answers SET {', '.join(f'{k} = ?' for k in sets)} "
                           "WHERE id = ? AND workspace = ?", (*sets.values(), answer_id, workspace))
        if not cur.rowcount:
            return None
    return get(workspace, answer_id)


def delete(workspace, answer_id):
    """The deleted answer, or None."""
    old = get(workspace, answer_id)
    if old:
        with _db() as conn:
            conn.execute("DELETE FROM verified_answers WHERE id = ? AND workspace = ?",
                         (answer_id, workspace))
    return old


def set_status(answer_id, status, notified=None):
    """The nightly review job's verdict (and the status its owner was emailed about)."""
    if status not in STATUSES:
        raise ValueError(f"unknown status {status!r}")
    with _db() as conn:
        if notified is None:
            conn.execute("UPDATE verified_answers SET status = ? WHERE id = ?", (status, answer_id))
        else:
            conn.execute("UPDATE verified_answers SET status = ?, notified = ? WHERE id = ?",
                         (status, notified, answer_id))


# ---- Vectors ----------------------------------------------------------------------------
def vectors(workspace):
    """{answer id: [(n, text, model, embedding bytes)]} of the workspace's answers."""
    out = {}
    with _db() as conn:
        for r in conn.execute("""SELECT v.answer_id, v.n, v.text, v.model, v.embedding
                                 FROM verified_answer_vectors v
                                 JOIN verified_answers a ON a.id = v.answer_id
                                 WHERE a.workspace = ? ORDER BY v.answer_id, v.n""",
                              (workspace,)):
            out.setdefault(r[0], []).append((r[1], r[2], r[3], r[4]))
    return out


def set_vectors(answer_id, rows):
    """Replace an answer's vectors: rows [(n, text, model, embedding bytes)]."""
    with _db() as conn:
        conn.execute("DELETE FROM verified_answer_vectors WHERE answer_id = ?", (answer_id,))
        conn.executemany("INSERT INTO verified_answer_vectors (answer_id, n, text, model, "
                         "embedding) VALUES (?, ?, ?, ?, ?)",
                         [(answer_id, *row) for row in rows])
