"""
workspaces.py  —  one company = one WORKSPACE, fully separated.

Each workspace has its own documents folder, its own search index (a Chroma
collection) and its own integrations, so one company's data can never show up
in another company's answers.

The "default" workspace keeps the original locations (docs/ and the original
Chroma collection), so existing data needs no migration.
"""

import os
import re

DEFAULT = "default"
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")


def valid(slug):
    return bool(slug and _SLUG.match(slug))


def docs_root(slug):
    """Folder with this workspace's documents (uploads + synced connectors)."""
    if slug == DEFAULT:
        return "docs"
    return os.path.join("data", "workspaces", slug, "docs")


def collection(slug):
    """Chroma collection holding this workspace's search index."""
    return "langchain" if slug == DEFAULT else f"ws_{slug}"


def connector_dir(slug, provider):
    """Where a connector writes the files it syncs (indexed, not editable in the UI)."""
    return os.path.join(docs_root(slug), "connectors", provider)
