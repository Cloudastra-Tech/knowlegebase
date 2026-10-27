"""
A new workspace's first steps (platform.md PL-17), with or without billing.

STEPS are worked out from the data, never ticked by hand, and a step stays done
once it has been (onboarding_store: when each was first seen done):

    domain       an email domain is verified
    source       a source is connected, or 3+ documents are uploaded
    index        the first index has finished
    answer       a question was answered with a citation from the workspace's
                 own documents (the sample ones don't count)
    colleagues   3 colleagues have signed in (people besides the billing owners)
    idp          optional: the workspace's own sign-in provider is added
    slack        optional: the Slack app is installed

The checklist can be put away for everyone in the workspace ("dismissed", kept
like a step), which replaces the per-browser checklist the Access screen had.

SAMPLE DOCUMENTS ("Try it with a sample HR handbook"): samples/hr-handbook/,
copied into the workspace's docs/sample/ and indexed like an upload, for
everyone, shown as "Sample documents". Removing them deletes the files and
their chunks from the index there and then (not when the next index run gets
to it), and empties the workspace's answer cache, so no answer after that can
cite them; an index run follows to tidy up. They count for no limit and no
onboarding step (an answer citing only them isn't the first answer).

FIRST RUN: someone who hasn't asked anything yet gets an "ask your first
question" screen with questions made from the titles of documents they may read.
"""

import json
import logging
import os
import shutil

import db
import docs_store
import ingest
import members_store
import onboarding_store as store
import slack_store
import workspaces
from kb.services import answer_cache, indexing

log = logging.getLogger("uvicorn.error")

SAMPLE_FOLDER = ingest.SAMPLE_FOLDER
SAMPLE_SOURCE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "samples", "hr-handbook")
DOCS_FOR_SOURCE = 3
COLLEAGUES = 3
DISMISSED = "dismissed"
STEPS = (
    ("domain", "Verify your email domain", False),
    ("source", "Connect your documents", False),
    ("index", "Index them", False),
    ("answer", "Get a first answer with a source", False),
    ("colleagues", f"{COLLEAGUES} colleagues sign in", False),
    ("idp", "Add your company's identity provider", True),
    ("slack", "Connect Slack", True),
)
GENERIC_SUGGESTIONS = ("What is our leave policy?", "How do I claim expenses?",
                       "Who do I ask for help with my laptop?")


def _one(conn, sql, *args):
    row = conn.execute(sql, args).fetchone()
    return row[0] if row else None


def _real_citation(sources):
    try:
        cited = json.loads(sources or "[]")
    except ValueError:
        return False
    return any(isinstance(s, dict) and not _sample_source(s) for s in cited)


def _sample_source(s):
    """A source from the sample documents: by its key, or (a list that keeps no key)
    by the provider ingest.py tags them with."""
    return (str(s.get("doc_key") or "").startswith(SAMPLE_FOLDER + "/")
            or s.get("provider") == ingest.SAMPLES)


def _checks(ws):
    """{step: done now?} for every step, from the data."""
    root = workspaces.docs_root(ws)
    uploads = len(docs_store.list_docs(root)) if os.path.isdir(root) else 0
    conn = db.connect()
    try:
        connected = _one(conn, "SELECT COUNT(*) FROM integrations WHERE workspace = ?", ws)
        indexed = (_one(conn, "SELECT 1 FROM search_indexes WHERE workspace = ?", ws)
                   or _one(conn, "SELECT 1 FROM jobs WHERE workspace = ? AND kind = 'index' AND "
                                 "status = 'done' LIMIT 1", ws))
        cited = any(_real_citation(r[0]) for r in conn.execute(
            "SELECT sources FROM question_log WHERE workspace = ? AND outcome IN "
            "('answered', 'partial') AND sources != '[]' ORDER BY at DESC LIMIT 200", (ws,)))
        colleagues = _one(conn, """SELECT COUNT(*) FROM members WHERE workspace = ?
                                     AND disabled = 0 AND last_login_at IS NOT NULL
                                     AND username NOT IN (SELECT username FROM billing_owners
                                                           WHERE workspace = ?)""", ws, ws)
    finally:
        conn.close()
    install = slack_store.get(ws)
    return {"domain": bool(members_store.verified_domains(ws)),
            "source": bool(connected) or uploads >= DOCS_FOR_SOURCE,
            "index": bool(indexed),
            "answer": cited,
            "colleagues": (colleagues or 0) >= COLLEAGUES,
            "idp": bool(members_store.list_connections(ws)),
            "slack": bool(install and install.get("status") == "installed")}


def state(ws):
    """The checklist: each step, done or not (and since when), whether it's all
    done, and whether it was put away."""
    done = store.done_steps(ws)
    now_done = [s for s, ok in _checks(ws).items() if ok and s not in done]
    if now_done:
        store.mark_done(ws, now_done)
        done = store.done_steps(ws)
    steps = [{"id": sid, "title": title, "optional": optional, "done": sid in done,
              "done_at": done.get(sid)} for sid, title, optional in STEPS]
    return {"steps": steps,
            "complete": all(s["done"] for s in steps if not s["optional"]),
            "dismissed": DISMISSED in done,
            "sample": sample_state(ws)}


def dismiss(ws, on=True):
    if on:
        store.mark_done(ws, [DISMISSED])
    else:
        store.forget(ws, DISMISSED)


# ---- First run ---------------------------------------------------------------------------
def asked_before(ws, username):
    conn = db.connect()
    try:
        return bool(_one(conn, "SELECT 1 FROM question_log WHERE workspace = ? AND user = ? "
                               "LIMIT 1", ws, username))
    finally:
        conn.close()


def _title(t):
    stem = os.path.splitext(t)[0] if t.lower().endswith(docs_store.EXTENSIONS) else t
    return " ".join(stem.replace("_", " ").replace("-", " ").split())


def suggestions(items, n=4):
    """Questions from the titles of indexed documents the person may read
    (kb/routers/knowledge.listing), newest first; generic ones without any."""
    def modified(i):
        m = i.get("modified")
        return m if isinstance(m, (int, float)) else 0
    titles = []
    for i in sorted(items, key=modified, reverse=True):
        t = _title(i.get("title") or "")
        if i.get("chunks") and t and t not in titles:
            titles.append(t)
    if not titles:
        return list(GENERIC_SUGGESTIONS)
    return [f"What does “{t}” say?" for t in titles[:n]]


# ---- Sample documents --------------------------------------------------------------------
def _sample_dir(ws):
    return os.path.join(workspaces.docs_root(ws), SAMPLE_FOLDER)


def sample_files():
    try:
        return sorted(f for f in os.listdir(SAMPLE_SOURCE) if f.endswith(docs_store.EXTENSIONS))
    except OSError:
        return []


def sample_keys(ws):
    folder = _sample_dir(ws)
    try:
        return sorted(f"{SAMPLE_FOLDER}/{f}" for f in os.listdir(folder)
                      if f.endswith(docs_store.EXTENSIONS))
    except OSError:
        return []


def sample_state(ws):
    return {"on": bool(sample_keys(ws)), "available": bool(sample_files()),
            "files": [k.split("/", 1)[1] for k in sample_keys(ws)]}


def add_sample(ws):
    """Copy the sample documents into the workspace and index them. Returns their keys."""
    folder = _sample_dir(ws)
    os.makedirs(folder, exist_ok=True)
    for name in sample_files():
        shutil.copyfile(os.path.join(SAMPLE_SOURCE, name), os.path.join(folder, name))
    keys = sample_keys(ws)
    indexing.schedule_index(ws, docs=keys)
    return keys


def remove_sample(ws):
    """Delete the sample documents and, now, their chunks. Returns their keys."""
    keys = sample_keys(ws)
    shutil.rmtree(_sample_dir(ws), ignore_errors=True)
    if keys:
        forget_chunks(ws, keys)
        answer_cache.clear(ws)                       # no answer kept from before is served
        indexing.schedule_index(ws, docs=keys)       # tidies up after a run in progress
    return keys


def forget_chunks(ws, keys):
    """Delete the chunks of these documents from the index questions use, and make
    the workspace's agents load it again (their keyword index included)."""
    import vector_store                                # Chroma or pgvector
    collection = indexing.current(ws)[0]
    try:
        with ingest.CHROMA_LOCK:
            col = vector_store.existing(ingest.DB_FOLDER, collection)
    except Exception as e:
        if type(e).__name__ in ("NotFoundError", "InvalidCollectionException", "ValueError"):
            return 0                                   # nothing indexed yet
        raise
    found = col.get(where={"doc_key": {"$in": list(keys)}}, include=[])["ids"]
    if found:
        col.delete(ids=found)
        indexing.mark_changed(ws)
    return len(found)
