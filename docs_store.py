"""
docs_store.py  —  the files in docs/ that the assistant answers from.

Plain file operations behind the Documents screen (the React app via kb/routers/docs.py):
Text and PDF files, plus Word, PowerPoint, Excel, Markdown and HTML, which
ingest.py reads through connectors/extract.py.
    docs/public/      -> everyone can search these
    docs/restricted/  -> only HR / Admin can search these
"""

import os
import re
import shutil

DOCS_FOLDER = "docs"
ACCESS_LEVELS = ("public", "restricted")
EXTENSIONS = (".txt", ".pdf", ".md", ".html", ".docx", ".pptx", ".xlsx")  # ingest.EXTENSIONS
OFFICE = (".docx", ".pptx", ".xlsx")     # zip archives


def list_docs(root=DOCS_FOLDER):
    """Uploaded files of a workspace, as dicts (access, name, path, size).
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


def save_file(access, name, source, root=DOCS_FOLDER):
    """Save an uploaded file from an open file object, a piece at a time (never
    all of it in memory). Written beside the target first, so a failed copy
    leaves any file of that name as it was."""
    folder = os.path.join(root, access)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, name)
    partial = path + ".part"
    try:
        with open(partial, "wb") as f:
            shutil.copyfileobj(source, f, 1024 * 1024)
        os.replace(partial, path)
    finally:
        if os.path.exists(partial):
            os.remove(partial)


# Starts of common binary files: a ".txt" that begins like one of these isn't text.
_BINARY_STARTS = (b"%PDF-", b"PK\x03\x04", b"MZ", b"\x7fELF", b"\x89PNG", b"\xff\xd8\xff",
                  b"GIF8", b"\xd0\xcf\x11\xe0", b"\x1f\x8b", b"Rar!", b"7z\xbc\xaf")


def content_matches(name, head):
    """Do the first bytes of a file (up to 8 KB) look like what its extension
    says? A PDF starts with %PDF- (within its first 1 KB); a Word, PowerPoint or
    Excel file is a zip archive (PK); a text file (.txt, .md, .html) has no NUL
    bytes and doesn't start like a known binary format."""
    if name.lower().endswith(".pdf"):
        return b"%PDF-" in head[:1024]
    if name.lower().endswith(OFFICE):
        return head.startswith(b"PK\x03\x04")
    return b"\x00" not in head and not head.startswith(_BINARY_STARTS)
