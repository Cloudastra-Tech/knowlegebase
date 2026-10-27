"""What the assistant can search, as the Knowledge page lists it."""

import logging
import os

from fastapi import APIRouter, Depends

import access
import docs_store
import ingest
import integrations_store
import sync_store
import workspaces
from connectors import registry
from kb.deps import current_role, current_user, current_viewer, ws_of
from kb.services import agents, assistant, indexing, onboarding, permissions, pii_scan

router = APIRouter()
log = logging.getLogger("uvicorn.error")


@router.get("/api/knowledge")
def knowledge(user=Depends(current_user), role=Depends(current_role),
              viewer=Depends(current_viewer)):
    """Everything this workspace's assistant can search: uploaded files plus files
    synced by connectors, with how much of each is in the search index. Each
    person sees only the items they may search (their ACL names one of their
    principals, like search itself). HR and admins, who manage them, see every
    item's title, with WHO CAN SEE it (`who`: [{principal, kind, label}];
    `dropped`: grants at the source that matched no one here, left out),
    whether they may search it themselves (`readable`), and the personal data the
    ingest scan found in it when the policy flags it (`pii`: {counts, text})."""
    return listing(user, role, viewer, everything=role == "hr")


def listing(user, role, viewer, everything=False):
    """The Knowledge listing (see knowledge()). everything=False: only what
    `viewer` may search, as the public API and the MCP server list it."""
    ws = ws_of(user)
    root = workspaces.docs_root(ws)
    sync_store.import_manifests(ws)

    # Chunks per file in the index (by file path), straight from Chroma.
    chunks = {}
    try:
        import chromadb
        col = chromadb.PersistentClient(path=ingest.DB_FOLDER).get_collection(
            indexing.current(ws)[0])
        for meta in col.get(include=["metadatas"])["metadatas"]:
            key = os.path.normcase(os.path.abspath(meta.get("source", "")))
            chunks[key] = chunks.get(key, 0) + 1
    except Exception as e:                     # no index yet: everything shows 0
        if type(e).__name__ != "NotFoundError":
            log.exception("Knowledge: couldn't read the search index of workspace %s", ws)

    def indexed(path):
        return chunks.get(os.path.normcase(os.path.abspath(path)), 0)

    # SOURCE RULES (HR / Admin): which rule decides each document, for the row
    # actions. Keys, providers and folders are what a rule matches on.
    manage = role == "hr"
    rules = assistant.rules(ws) if manage else None
    names = permissions.names(ws) if manage else None
    # PERSONAL DATA (HR / Admin): documents the ingest scan found dense personal data
    # in, under the workspace's policy (kb/services/pii_scan.py).
    flagged = pii_scan.flags(ws, assistant.pii_policy(ws)) if manage else {}

    def place(key, provider, acl, dropped=0, folder=None):
        out = {"doc_key": key, "provider": provider, "folder": folder,
               "access": access.level_of_acl(acl)}
        if rules is not None:
            rule = rules.rule_for(key, provider)
            out["rule"] = rule and {k: rule[k] for k in ("id", "match_type", "match_value",
                                                         "action", "weight")}
        if manage:
            out["who"] = permissions.labels(ws, acl, names)
            out["dropped"] = dropped
            out["readable"] = access.can_see(viewer, acl)
            out["pii"] = flagged.get(key)
        return out

    def shown(acl):
        return everything or access.can_see(viewer, acl)

    items = []
    picked = permissions.doc_acls(ws)
    for d in docs_store.list_docs(root):
        key = os.path.relpath(d["path"], root).replace(os.sep, "/")
        acl = picked.get(key) or access.level_acl(d["access"])
        if not shown(acl):
            continue
        items.append({"title": d["name"], "source": "Uploaded", "url": None,
                      "modified": os.path.getmtime(d["path"]),
                      "size": d["size"], "chunks": indexed(d["path"]),
                      **place(key, assistant.UPLOADS, acl, folder=key.rsplit("/", 1)[0])})

    # The sample documents a new workspace can try it with (kb/services/onboarding.py):
    # for everyone, and labelled as samples.
    for key in onboarding.sample_keys(ws):
        path = os.path.join(root, key)
        items.append({"title": key.split("/", 1)[1], "source": "Sample",
                      "url": None, "modified": os.path.getmtime(path),
                      "size": os.path.getsize(path), "chunks": indexed(path),
                      **place(key, ingest.SAMPLES, access.level_acl("public"),
                              folder=onboarding.SAMPLE_FOLDER)})

    # Synced files: what source_items says each connector folder holds, and who
    # may see each as ingest.py tags it (its connection's policy).
    sourced = permissions.connector_acls(ws, details=True)
    levels = integrations_store.chunk_access_levels(ws)
    connectors_dir = os.path.join(root, "connectors")
    for provider in sorted(os.listdir(connectors_dir)) if os.path.isdir(connectors_dir) else []:
        folder = os.path.join(connectors_dir, provider)
        for item in sync_store.items(ws, provider).values():
            name = item.get("file_name")
            path = os.path.join(folder, name) if name else None
            if not path or not os.path.isfile(path):
                continue
            key = f"connectors/{provider}/{name}"
            acl, dropped = permissions.connection_acl(ws, provider, key, sourced, levels)
            if not shown(acl):
                continue
            items.append({"title": item.get("title") or name,
                          "source": registry.label(provider),
                          "url": item.get("url"), "modified": item.get("modified"),
                          "size": os.path.getsize(path), "chunks": indexed(path),
                          **place(key, provider, acl, dropped, item.get("parent_id"))})

    items.sort(key=lambda i: (i["source"] != "Uploaded", i["title"].lower()))
    out = {"workspace": ws, "items": items,
           "outline": agents.outline_on(ws, role)}          # searched live, so not listed here
    if rules is not None:
        out["rules"] = rules.rules
    return out
