"""
docs_admin.py  —  the DOCUMENTS screen: view, edit, add, upload, delete docs.

Everything here works on the files in docs/ (the same folder ingest.py reads):
    docs/public/      -> everyone can search these
    docs/restricted/  -> only HR / Admin can search these

Changes to files are NOT searchable until you press "Re-index", which re-runs
ingest.py and reloads the agent — exactly what you'd do by hand in a terminal.
"""

import os
import contextlib
import io

import streamlit as st

import docs_store
import ingest
import integrations_store
import workspaces

DOCS_FOLDER = docs_store.DOCS_FOLDER
ACCESS_FOLDERS = {"Public": "public", "Restricted (HR only)": "restricted"}
_safe_name = docs_store.safe_name
_save = docs_store.save


def _list_docs():
    """All .txt/.pdf files under docs/, as (access, filename, full_path)."""
    return [(d["access"], d["name"], d["path"]) for d in docs_store.list_docs()]


def _mark_changed(message):
    st.session_state.docs_changed = True
    st.session_state.docs_flash = message


def _reindex():
    """Re-run ingest.py, then drop the cached agents so they reload the new DB."""
    log = io.StringIO()
    with contextlib.redirect_stdout(log):
        summary = ingest.main(connector_access=integrations_store.chunk_access_levels(
            workspaces.DEFAULT))
    st.cache_resource.clear()
    return summary, log.getvalue()


def render():
    st.header("📁 Documents")
    st.caption("Add, edit or remove the documents the assistant answers from. "
               "Press **Re-index** after changes so the assistant can search them.")

    if msg := st.session_state.pop("docs_flash", None):
        st.success(msg)

    docs = _list_docs()

    # ---- Re-index ----------------------------------------------------------
    if st.session_state.get("docs_changed"):
        st.warning("You have changes the assistant can't see yet. Press **Re-index**.")
    if st.button("🔄 Re-index documents", type="primary", disabled=not docs):
        with st.spinner("Re-indexing (reading, splitting and embedding every document)..."):
            summary, log = _reindex()
        st.session_state.docs_changed = False
        if summary:
            st.success(f"Done: {summary['documents']} document(s), {summary['chunks']} "
                       f"chunk(s) indexed ({summary['restricted']} restricted). "
                       "The assistant now uses the new content.")
        else:
            st.error("No documents were found, so nothing was indexed.")
        with st.expander("Re-index log"):
            st.code(log or "(no output)")

    # ---- Existing documents ------------------------------------------------
    st.subheader(f"Current documents ({len(docs)})")
    if not docs:
        st.info("No documents yet. Add or upload one below.")
    for access, name, path in docs:
        label = "🔒 Restricted" if access == "restricted" else "🌐 Public"
        with st.expander(f"{label} · {name}"):
            key = f"{access}/{name}"
            if name.lower().endswith(".txt"):
                with open(path, encoding="utf-8", errors="replace") as f:
                    text = f.read()
                edited = st.text_area("Content", text, height=300, key=f"edit:{key}")
                if st.button("💾 Save changes", key=f"save:{key}", disabled=edited == text):
                    _save(access, name, edited.encode("utf-8"))
                    _mark_changed(f"Saved {name}.")
                    st.rerun()
            else:
                st.caption("PDFs can't be edited here. Download it, change it, then "
                           "upload it again with the same name to replace it.")
                with open(path, "rb") as f:
                    st.download_button("⬇️ Download", f.read(), file_name=name,
                                       key=f"dl:{key}")

            confirm = st.checkbox("I want to delete this document", key=f"confirm:{key}")
            if st.button("🗑️ Delete", key=f"del:{key}", disabled=not confirm):
                os.remove(path)
                _mark_changed(f"Deleted {name}.")
                st.rerun()

    # ---- Add a new text document -------------------------------------------
    st.subheader("Add a new document")
    with st.form("new_doc", clear_on_submit=True):
        title = st.text_input("File name", placeholder="e.g. leave_policy")
        access_label = st.radio("Who can see it?", list(ACCESS_FOLDERS), horizontal=True,
                                key="new_access")
        body = st.text_area("Content", height=200)
        if st.form_submit_button("➕ Add document"):
            name = _safe_name(title)
            access = ACCESS_FOLDERS[access_label]
            if not name or not body.strip():
                st.error("Please give a file name and some content.")
            elif os.path.exists(os.path.join(DOCS_FOLDER, access, name)):
                st.error(f"{name} already exists. Edit it above instead.")
            else:
                _save(access, name, body.encode("utf-8"))
                _mark_changed(f"Added {name}.")
                st.rerun()

    # ---- Upload files ------------------------------------------------------
    st.subheader("Upload files")
    with st.form("upload", clear_on_submit=True):
        files = st.file_uploader("TXT or PDF files", type=["txt", "pdf"],
                                 accept_multiple_files=True)
        access_label = st.radio("Who can see them?", list(ACCESS_FOLDERS), horizontal=True,
                                key="upload_access")
        if st.form_submit_button("⬆️ Upload"):
            access = ACCESS_FOLDERS[access_label]
            saved = []
            for f in files or []:
                name = _safe_name(f.name)
                if name:
                    _save(access, name, f.getvalue())
                    saved.append(name)
            if saved:
                _mark_changed(f"Uploaded {', '.join(saved)} (files with the same "
                              "name were replaced).")
                st.rerun()
            else:
                st.error("Please choose at least one .txt or .pdf file.")
