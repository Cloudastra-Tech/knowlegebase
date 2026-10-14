"""
docs_store.py  —  the files in docs/ that the assistant answers from.

Plain file operations shared by both frontends (Streamlit's docs_admin.py and
the React app via api.py):
    docs/public/      -> everyone can search these
    docs/restricted/  -> only HR / Admin can search these
"""

import os
import re

DOCS_FOLDER = "docs"
ACCESS_LEVELS = ("public", "restricted")
EXTENSIONS = (".txt", ".pdf")      # what ingest.py can read


def list_docs(root=DOCS_FOLDER):
    """Uploaded .txt/.pdf files of a workspace, as dicts (access, name, path, size).
    (Files synced by connectors live in root/connectors/ and are not listed.)"""
    found = []
    for access in ACCESS_LEVELS:
        folder = os.path.join(root, access)
        if not os.path.isdir(folder):
            continue
        for name in sorted(os.listdir(folder)):
            if name.lower().endswith(EXTENSIONS):
                path = os.path.join(folder, name)
                found.append({"access": access, "name": name, "path": path,
                              "size": os.path.getsize(path)})
    return found


def safe_name(name, default_ext=".txt"):
    """Keep only a plain file name (no folders, no odd characters)."""
    name = os.path.basename(name or "").strip()
    stem, ext = os.path.splitext(name)
    stem = re.sub(r"[^A-Za-z0-9 _.-]", "", stem).strip().replace(" ", "_")
    ext = ext.lower() if ext.lower() in EXTENSIONS else default_ext
    return f"{stem}{ext}" if stem else ""


def doc_path(access, name, root=DOCS_FOLDER):
    """Path of an existing-or-new doc, or None if access/name are not allowed."""
    if access not in ACCESS_LEVELS or not name or safe_name(name) != name:
        return None
    return os.path.join(root, access, name)


def save(access, name, data, root=DOCS_FOLDER):
    folder = os.path.join(root, access)
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, name), "wb") as f:
        f.write(data)
