"""
Drafts written by the assistant (platform.md PL-31), for HR and admins to review:

    "Summarise this folder / source"   (the Knowledge screen)  map-reduce: each
        document the requester may read is summarised (summaries.summarise_doc,
        cached per version), then one overview is written from those summaries,
        every statement cited [n] to its document
    "Draft a doc for this gap"         (Insights, a content gap)  the gap's sample
        questions are searched as the requester's role, and a document is drafted
        from the passages found, cited [n]; what they don't cover is marked
        "TO CONFIRM:"

Writing takes a few model calls, so it runs as a `draft` job; the draft shows as
"generating" until then. The requester's role is looked up again when the job
runs, and every document and passage is checked with access.may_read for them.

A draft is never searchable and never published by itself (drafts_store.py): HR
edits it on the Documents screen, then publishes it at an access level no lower
than the most restricted document it was written from. Publishing copies it into
public/ or restricted/ with its sources list, indexes it, and notes it in the
change feed. Every step is audited by the endpoint that asks for it.
"""

import logging
import os
import time

import docs_store
import drafts_store
import integrations_store
import members_store
import question_log_store
import sync_store
import workspaces
from kb import jobs
from kb.services import access, digests, indexing, insights, retrieval, summaries

log = logging.getLogger("uvicorn.error")

MAX_FOLDER_DOCS = 40           # the most recently changed documents a summary covers
MAX_GAP_QUERIES = 5            # sample questions searched for a gap
PASSAGES_PER_QUERY = 4
MAX_PASSAGES = 12


class Empty(Exception):
    """Nothing to write from (the message says why, for the draft's row)."""


# ---- What a scope holds ---------------------------------------------------------------
def scope_docs(ws, member, provider, folder=None):
    """The documents of a source (and one of its folders) that `member` may read,
    newest first: [{doc_key, title, provider, url, access, modified}]."""
    root = workspaces.docs_root(ws)
    found = []
    if provider == access.UPLOADS:
        for d in docs_store.list_docs(root):
            key = f"{d['access']}/{d['name']}"
            if folder and d["access"] != folder:
                continue
            found.append({"doc_key": key, "title": d["name"], "url": None,
                          "access": d["access"], "acl": None,
                          "modified": os.path.getmtime(d["path"])})
    else:
        level = integrations_store.chunk_access_levels(ws).get(provider)
        if level is None:
            return []
        conn_dir = workspaces.connector_dir(ws, provider)
        for item in sync_store.items(ws, provider).values():
            name = item.get("file_name")
            if not name or not os.path.isfile(os.path.join(conn_dir, name)):
                continue
            if folder and (item.get("parent_id") or "") != folder:
                continue
            found.append({"doc_key": f"connectors/{provider}/{name}",
                          "title": item.get("title") or name, "url": item.get("url"),
                          "access": level, "acl": item.get("acl"),
                          "modified": os.path.getmtime(os.path.join(conn_dir, name))})
    label = digests.source_label(provider)
    out = [{**d, "provider": label} for d in found
           if access.may_read(member, {**d, "provider": provider})]
    out.sort(key=lambda d: -d["modified"])
    return out


def scope_label(provider, folder=None):
    label = digests.source_label(provider)
    if not folder:
        return f"Everything from {label}"
    if provider == access.UPLOADS:
        return f"{label}: {'HR only' if folder == 'restricted' else 'public'} files"
    return f"{label}: folder {folder}"


def _source(d):
    return {k: d.get(k) for k in ("doc_key", "title", "provider", "url", "access")}


# ---- Starting one ---------------------------------------------------------------------
def start_summary(ws, user, provider, folder=None):
    """A new draft summarising a source or one of its folders (written by a job)."""
    title = f"Summary - {scope_label(provider, folder)}"
    draft = drafts_store.create(ws, "summary", title, {"provider": provider, "folder": folder},
                                user["username"])
    _queue(ws, draft)
    return draft


def start_gap(ws, user, gap, now=None):
    """A new draft for a content gap the requester may see (insights.gaps_for)."""
    label = gap["label"] or (gap["samples"][0] if gap["samples"] else "Content gap")
    draft = drafts_store.create(ws, "gap", f"Draft - {label}"[:200],
                                {"gap_id": gap["id"], "label": gap["label"]}, user["username"])
    _queue(ws, draft)
    return draft


def _queue(ws, draft):
    jobs.enqueue("draft", ws, {"id": draft["id"]}, lane=f"draft:{ws}",
                 dedupe_key=f"draft:{draft['id']}")


def find_gap(ws, role, gap_id):
    """A content gap as `role` may see it, or None (gone, or hidden from the role)."""
    mode = question_log_store.settings(ws)["mode"]
    gap = next((g for g in insights.gaps_for(ws, role, mode) if g["id"] == gap_id), None)
    return None if not gap or gap["hidden"] else gap


# ---- Writing one (the job) ------------------------------------------------------------
def write_summary(ws, member, draft):
    scope = draft["scope"]
    docs = scope_docs(ws, member, scope.get("provider"), scope.get("folder"))
    if not docs:
        raise Empty("There are no documents here you can read.")
    summarised = []
    for d in docs[:MAX_FOLDER_DOCS]:
        text = summaries.summarise_doc(ws, d["doc_key"], d["title"])
        if text:
            summarised.append({**d, "summary": text})
    if not summarised:
        raise Empty("None of these documents could be read.")
    label = scope_label(scope.get("provider"), scope.get("folder"))
    if len(docs) > MAX_FOLDER_DOCS:
        label += f" (the {MAX_FOLDER_DOCS} most recently changed of {len(docs)} documents)"
    sources = [_source(d) for d in summarised]
    return summaries.summarise_folder(label, summarised), sources


def write_gap(ws, member, draft):
    role = access.access_role(member)
    gap = find_gap(ws, role, draft["scope"].get("gap_id"))
    if not gap:
        raise Empty("This content gap is gone (gaps are worked out again weekly).")
    queries = gap["samples"][:MAX_GAP_QUERIES] or [gap["label"] or ""]
    passages, sources, seen = [], [], set()
    for q in [q for q in queries if q.strip()]:
        try:
            found = retrieval.search(ws, role, q, k=PASSAGES_PER_QUERY)
        except retrieval.Refused:
            continue
        for p in found:
            if p["chunk_id"] in seen or not access.may_read(member, p):
                continue
            seen.add(p["chunk_id"])
            key = p.get("doc_key") or p.get("doc_id")
            index = next((n for n, s in enumerate(sources, 1) if s["doc_key"] == key), None)
            if index is None:
                sources.append({"doc_key": key, "title": p.get("title"),
                                "provider": p.get("provider"), "url": p.get("url"),
                                "access": p.get("access") or "restricted"})
                index = len(sources)
            passages.append({"text": p["text"], "source": index, "title": p.get("title")})
            if len(passages) >= MAX_PASSAGES:
                break
        if len(passages) >= MAX_PASSAGES:
            break
    text = summaries.draft_for_gap(gap["label"], gap["samples"], passages, sources)
    return text, sources


@jobs.handler("draft", max_attempts=2)
def run_draft(job):
    ws = job.workspace
    draft = drafts_store.get(ws, job.payload["id"])
    if not draft or draft["status"] != "generating":
        return {"skipped": "not waiting"}
    member = members_store.get(draft["created_by"])
    if not member or member["workspace"] != ws or access.access_role(member) is None:
        drafts_store.update(ws, draft["id"], status="failed",
                            error="Whoever asked for it can't any more.")
        return {"failed": "requester"}
    try:
        text, sources = (write_summary if draft["kind"] == "summary" else write_gap)(
            ws, member, draft)
    except Empty as e:
        drafts_store.update(ws, draft["id"], status="failed", error=str(e))
        return {"failed": str(e)}
    except Exception as e:
        log.exception("Drafts: writing draft %s in workspace %s failed", draft["id"], ws)
        if job.attempts < job.max_attempts:
            raise jobs.Retry(f"{type(e).__name__}: {e}")
        drafts_store.update(ws, draft["id"], status="failed",
                            error="It couldn't be written. Try again later.")
        return {"failed": type(e).__name__}
    drafts_store.write_text(ws, draft["id"], text)
    level = access.strictest(*(s["access"] or "restricted" for s in sources)) if sources \
        else "public"
    drafts_store.update(ws, draft["id"], status="draft", sources=sources, max_access=level,
                        error=None)
    return {"draft": draft["id"], "sources": len(sources)}


# ---- Reading, editing, publishing -----------------------------------------------------
def may_see(user, draft):
    """A draft is for HR who may read everything it was written from."""
    return access.may_read(user, {"access": draft["max_access"]})


def view(draft, content=None):
    out = {k: draft[k] for k in ("id", "kind", "title", "scope", "status", "sources",
                                 "max_access", "error", "created_by", "created_at",
                                 "updated_at", "published_as", "published_at")}
    if content is not None:
        out["content"] = content
    return out


class PublishError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


def publish(ws, user, draft, level, name):
    """Copy the draft into the documents at `level` as `name` (with its sources),
    index it and mark it published. Raises PublishError."""
    if draft["status"] != "draft":
        raise PublishError(409, "Only a finished draft can be published.")
    if level not in docs_store.ACCESS_LEVELS:
        raise PublishError(400, "Access must be public or restricted.")
    if access.strictest(level, draft["max_access"]) != level:
        raise PublishError(400, "This draft was written from HR-only documents, so it can "
                                "only be published as HR only.")
    name = docs_store.safe_name(name or draft["title"])
    if not name.endswith(".txt"):
        name = os.path.splitext(name)[0] + ".txt"
    root = workspaces.docs_root(ws)
    target = docs_store.doc_path(level, name, root)
    if not target:
        raise PublishError(400, "Please give the document a file name.")
    if os.path.exists(target):
        raise PublishError(409, f"{name} already exists. Choose another name.")
    text = summaries.with_sources(drafts_store.read_text(ws, draft["id"]), draft["sources"])
    docs_store.save(level, name, text.encode("utf-8"), root)
    key = f"{level}/{name}"
    digests.record(ws, key, "added", title=name, actor=user["username"], access_level=level)
    indexing.schedule_index(ws, docs=[key])
    drafts_store.remove_text(ws, draft["id"])
    return drafts_store.update(ws, draft["id"], status="published", published_as=key,
                               published_by=user["username"], published_at=time.time())
