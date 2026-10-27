"""
One passage (chunk) of a workspace's search index, with the text around it: what
the source viewer shows when someone opens a citation (chat.md C13). Uploaded
files have no link to open, so this is how their citations open "at the passage".

The asker's principals are checked against the chunk's ACL on EVERY call, and
the text around it only comes from chunks of the same document they may see too.
A chunk that isn't there and one they may not see look the same (None).
"""

import access
import ingest
from rag import passage_of, passage_text

CONTEXT_CHARS = 1500      # of the document before and after the passage, at most
_GAP = 4                  # whitespace the splitter dropped between chunks, filled with "\n"


def get(collection, chunk_id, who, context=CONTEXT_CHARS):
    """{title, provider, url, access, page, heading_path, ..., text, before,
    after} of a chunk, or None if it isn't in the collection or `who` (an
    access.Viewer, or a bare role) may not see it. text: the passage itself;
    before / after: the document's text around it (within `context`
    characters, as far as it can be pieced together)."""
    viewer = access.of(who)
    store = ingest._store(collection)
    got = store._collection.get(ids=[chunk_id], include=["documents", "metadatas"])
    if not got["ids"]:
        return None
    text, meta = got["documents"][0], got["metadatas"][0] or {}
    if not access.can_see(viewer, access.chunk_acl(meta)):
        return None
    body = passage_text(text, meta)
    out = {**passage_of({**meta, "chunk_id": meta.get("chunk_id") or chunk_id}),
           "text": body, "before": "", "after": ""}
    start, end = meta.get("char_start"), meta.get("char_end")
    if not (context and meta.get("doc_key") and isinstance(start, int) and isinstance(end, int)):
        return out

    # The document's text around the passage, from its other chunks (they
    # overlap, and know where they sit): position -> character.
    lo, hi = max(0, start - context), end + context
    chars = {}
    near = store._collection.get(where={"doc_key": meta["doc_key"]},
                                 include=["documents", "metadatas"])
    for t, m in zip(near["documents"], near["metadatas"]):
        s, e = (m or {}).get("char_start"), (m or {}).get("char_end")
        if (not isinstance(s, int) or not isinstance(e, int) or e < lo or s > hi
                or m.get("page") != meta.get("page")
                or not access.can_see(viewer, access.chunk_acl(m))):
            continue
        for i, ch in enumerate(passage_text(t, m), s):
            if lo <= i < hi:
                chars.setdefault(i, ch)
    out["before"] = _run(chars, start - 1, lo - 1, -1)[::-1]
    out["after"] = _run(chars, end, hi, 1)
    return out


def _run(chars, first, stop, step):
    """The characters from `first` towards `stop` while they are known (a gap
    of up to _GAP missing ones counts as a line break)."""
    out, gap = [], []
    for i in range(first, stop, step):
        if i in chars:
            out += gap[:1] + [chars[i]]
            gap = []
        elif len(gap) < _GAP:
            gap.append("\n")
        else:
            break
    return "".join(out)
