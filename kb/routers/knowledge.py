"""What the assistant can search, as the Knowledge page lists it."""

import logging
import os

from fastapi import APIRouter, Depends

import docs_store
import ingest
import integrations_store
import sync_store
import workspaces
from connectors import registry
from kb.deps import current_role, current_user, ws_of
from kb.services import agents, indexing

router = APIRouter()
log = logging.getLogger("uvicorn.error")


@router.get("/api/knowledge")
def knowledge(user=Depends(current_user), role=Depends(current_role)):
    """Everything this workspace's assistant can search: uploaded files plus files
    synced by connectors, with how much of each is in the search index.
    Public users only see public items (like search itself)."""
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

    items = []
    for d in docs_store.list_docs(root):
        if role != "hr" and d["access"] == "restricted":
            continue
        items.append({"title": d["name"], "source": "Uploaded", "access": d["access"],
                      "url": None, "modified": os.path.getmtime(d["path"]),
                      "size": d["size"], "chunks": indexed(d["path"])})

    # Synced files: what source_items says each connector folder holds.
    levels = integrations_store.chunk_access_levels(ws)
    connectors_dir = os.path.join(root, "connectors")
    for provider in sorted(os.listdir(connectors_dir)) if os.path.isdir(connectors_dir) else []:
        folder = os.path.join(connectors_dir, provider)
        access = levels.get(provider, "restricted")       # as ingest.py tags them
        if role != "hr" and access == "restricted":
            continue
        for item in sync_store.items(ws, provider).values():
            name = item.get("file_name")
            path = os.path.join(folder, name) if name else None
            if not path or not os.path.isfile(path):
                continue
            items.append({"title": item.get("title") or name,
                          "source": registry.label(provider), "access": access,
                          "url": item.get("url"), "modified": item.get("modified"),
                          "size": os.path.getsize(path), "chunks": indexed(path)})

    items.sort(key=lambda i: (i["source"] != "Uploaded", i["title"].lower()))
    return {"workspace": ws, "items": items,
            "outline": agents.outline_on(ws, role)}         # searched live, so not listed here
