"""What the assistant can search, as the Knowledge page lists it."""

import json
import logging
import os

from fastapi import APIRouter, Depends

import docs_store
import ingest
import integrations_store
import workspaces
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

    levels = integrations_store.chunk_access_levels(ws)
    connectors_dir = os.path.join(root, "connectors")
    for provider in sorted(os.listdir(connectors_dir)) if os.path.isdir(connectors_dir) else []:
        folder = os.path.join(connectors_dir, provider)
        try:
            with open(os.path.join(folder, "manifest.json"), encoding="utf-8") as f:
                manifest = json.load(f)
        except (OSError, ValueError):
            continue
        access = levels.get(provider, "restricted")       # as ingest.py tags them
        if role != "hr" and access == "restricted":
            continue
        for name, info in manifest.items():
            path = os.path.join(folder, name)
            if not os.path.isfile(path):
                continue
            items.append({"title": info.get("title") or name,
                          "source": info.get("provider") or provider, "access": access,
                          "url": info.get("url"), "modified": info.get("modified"),
                          "size": os.path.getsize(path), "chunks": indexed(path)})

    items.sort(key=lambda i: (i["source"] != "Uploaded", i["title"].lower()))
    return {"workspace": ws, "items": items,
            "outline": agents.outline_on(ws, role)}         # searched live, so not listed here
