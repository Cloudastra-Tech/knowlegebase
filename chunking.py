"""
chunking.py  —  STRUCTURE-AWARE splitting of a document's text into chunks
(pipeline.md P1-2). ingest.load_and_split calls split() on each loaded text: a
whole file, or one PDF page.

What every file type looks like by the time it gets here (connectors/extract.py
and the connector sync write it this way):
    Markdown, HTML, Word   "# Heading" lines, "a | b | c" table rows
    PowerPoint             "## Slide 3" per slide
    Excel                  "## <sheet name>" per sheet, then "a | b | c" rows
    Google Sheets, CSV     comma-separated rows (Drive exports a Sheet as CSV)
    Notion, Outline        Markdown
    plain text             "Refund policy:" label lines as headings, if any
    PDF                    one page at a time (the page is kept on every chunk)

The rules:
  - A chunk never starts in the middle of a section: every heading starts a new
    chunk, unless the chunk so far is tiny (under MIN_TOKENS: a title line, a
    one-line section), which is kept with what follows rather than left alone.
  - A table (pipe rows or CSV rows) is cut between rows, never inside one, and
    every piece after the first starts with the table's HEADER ROW again (as a
    prefix: see Piece), so "Pro | 2000" still says which column is the price.
  - A fenced code block (```) is kept whole up to CODE_TOKENS, then cut between
    lines.
  - Prose is packed paragraph by paragraph; a paragraph too long for one chunk
    is cut between sentences, with OVERLAP_TOKENS of the end of one piece
    repeated at the start of the next.
  - Sizes are TOKENS (count_tokens), not characters.

A piece is always a contiguous slice of the text (start, end), so a citation can
point at exactly where it is (ingest's char_start / char_end; the source viewer
pieces the document together from them). Anything added for search only — the
repeated header row, the title line — is a prefix outside the slice.
"""

import os
import re
from dataclasses import dataclass

import regex


def _env_int(name, default):
    try:
        return max(0, int(os.getenv(name, "") or default))
    except ValueError:
        return default


# Sizes in tokens. These decide what a chunk's text is, so they are part of
# ingest.FINGERPRINT: changing one rebuilds the index (a shadow rebuild).
CHUNK_TOKENS = _env_int("RAG_CHUNK_TOKENS", 200) or 200
MIN_TOKENS = _env_int("RAG_CHUNK_MIN_TOKENS", 25)
OVERLAP_TOKENS = _env_int("RAG_CHUNK_OVERLAP_TOKENS", 30)
CODE_TOKENS = 2 * CHUNK_TOKENS
VERSION = 1                     # of the rules above: bump when they change

HEADING_SEPARATOR = " › "
# A Markdown heading ("## Refunds", as the Notion sync writes them), or a short
# line of its own ending in a colon ("Refund policy:") after a blank line.
_MD_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t#]*$", re.M)
_LABEL_HEADING = re.compile(r"(?:\A|\n[ \t]*\n)[ \t]*([^\n:.!?#]{2,60}):[ \t]*(?=\n)")
_FENCE = re.compile(r"^[ \t]*(```|~~~)")
_MD_RULE = re.compile(r"^[ \t]*\|?[ \t]*:?-{2,}:?[ \t]*(\|[ \t]*:?-{2,}:?[ \t]*)+\|?[ \t]*$")
_SENTENCE_END = re.compile(r"(?<=[.!?:;])[ \t]+|\n")
# One "piece" of text for counting: a run of letters/digits, or one other character.
_TOKEN_PIECE = regex.compile(r"[\p{L}\p{M}]+|\p{N}+|[^\s\p{L}\p{M}\p{N}]")


def count_tokens(text):
    """About how many model tokens `text` is, without a tokenizer download:
    a word is one token per 7 letters (common words are one), digits one per 3,
    every punctuation mark one. Within about 10% of OpenAI's o200k tokenizer on
    English prose, and deterministic, so the same text always splits the same
    way (chunk ids depend on it)."""
    n = 0
    for piece in _TOKEN_PIECE.findall(text or ""):
        if piece[0].isdigit():
            n += (len(piece) + 2) // 3
        elif piece[0].isalpha() or len(piece) > 1:
            n += 1 + (len(piece) - 1) // 7
        else:
            n += 1
    return n


# ---- Headings -------------------------------------------------------------------
def headings(text):
    """[(position, level, heading)] in a document's text, in order. A label line
    ("Refund policy:") ranks below every Markdown level. Headings inside a
    fenced code block don't count ("# a comment")."""
    fenced = [(b.start, b.end) for b in _blocks(text) if b.kind == "code"]

    def in_code(at):
        return any(s <= at < e for s, e in fenced)

    found = [(m.start(), len(m.group(1)), m.group(2).strip())
             for m in _MD_HEADING.finditer(text) if not in_code(m.start())]
    found += [(m.start(1), 7, m.group(1).strip()) for m in _LABEL_HEADING.finditer(text)
              if not in_code(m.start(1))]
    return sorted(found)


def heading_stack(found, position, start=()):
    """[(level, heading)] a position in the text sits under, outermost first.
    start: the stack the text begins under (a PDF page continues the last one)."""
    path = list(start)
    for at, level, title in found:
        if at > position:
            break
        path = [(lv, t) for lv, t in path if lv < level] + [(level, title)]
    return path


def heading_path(found, position, start=()):
    """The headings a position sits under, joined by HEADING_SEPARATOR ('' if none)."""
    return HEADING_SEPARATOR.join(t for _, t in heading_stack(found, position, start))[:300]


# ---- Blocks: the units a chunk is made of ----------------------------------------
@dataclass
class _Block:
    start: int
    end: int                     # exclusive, without the line break after it
    kind: str                    # heading | para | table | code
    tokens: int = 0


def _lines(text):
    """[(start, end, line)] of every line, end without its line break."""
    out, at = [], 0
    for line in text.split("\n"):
        out.append((at, at + len(line), line))
        at += len(line) + 1
    return out


def _is_pipe_row(line):
    s = line.strip()
    return s.count("|") >= 1 and len([c for c in s.strip("|").split("|")]) >= 2 \
        and not s.startswith("#")


def _csv_cells(line):
    """How many comma-separated cells a line has (quotes respected), 0 if under 2."""
    cells, quoted, n = 1, False, 0
    for ch in line:
        if ch == '"':
            quoted = not quoted
        elif ch == "," and not quoted:
            cells += 1
        n += 1
    return cells if cells >= 2 and not quoted else 0


def _table_starts(lines, i):
    """True if a table begins at line i, right under a paragraph (Word puts no
    blank line between them): two pipe rows, or three CSV rows alike."""
    if i + 1 < len(lines) and _is_pipe_row(lines[i][2]) and _is_pipe_row(lines[i + 1][2]):
        return True
    cells = _csv_cells(lines[i][2])
    return bool(cells) and i + 2 < len(lines) and all(
        _csv_cells(lines[k][2]) == cells for k in (i + 1, i + 2))


def _blocks(text):
    """The text as blocks, in order: headings, paragraphs (lines up to a blank
    line), tables (2+ pipe rows, or 3+ CSV rows with the same number of cells)
    and fenced code blocks."""
    lines = _lines(text)
    md = {m.start() for m in _MD_HEADING.finditer(text)}
    out, i = [], 0
    while i < len(lines):
        start, end, line = lines[i]
        if not line.strip():
            i += 1
            continue
        if _FENCE.match(line):                       # code, up to its closing fence
            fence = _FENCE.match(line).group(1)
            j = i + 1
            while j < len(lines) and not lines[j][2].lstrip().startswith(fence):
                j += 1
            j = min(j, len(lines) - 1)
            out.append(_Block(start, lines[j][1], "code"))
            i = j + 1
            continue
        if start in md:
            out.append(_Block(start, end, "heading"))
            i += 1
            continue
        if _is_pipe_row(line):
            j = i
            while j + 1 < len(lines) and _is_pipe_row(lines[j + 1][2]):
                j += 1
            if j > i:
                out.append(_Block(start, lines[j][1], "table"))
                i = j + 1
                continue
        cells = _csv_cells(line)
        if cells:
            j = i
            while j + 1 < len(lines) and _csv_cells(lines[j + 1][2]) == cells:
                j += 1
            if j >= i + 2:
                out.append(_Block(start, lines[j][1], "table"))
                i = j + 1
                continue
        j = i                                        # a paragraph
        while (j + 1 < len(lines) and lines[j + 1][2].strip()
               and not _FENCE.match(lines[j + 1][2]) and lines[j + 1][0] not in md
               and not _table_starts(lines, j + 1)):
            j += 1
        out.append(_Block(start, lines[j][1], "para"))
        i = j + 1
    for b in out:
        b.tokens = count_tokens(text[b.start:b.end])
    return out


# ---- Pieces ----------------------------------------------------------------------
@dataclass
class Piece:
    """One chunk: text[start:end], plus a prefix that goes before it for search
    only (a table's header row). rows: "first-last" data rows of a table piece.
    anchor: where its content starts, after the headings it opens with (the
    place whose heading path it has); -1 = at start."""
    start: int
    end: int
    prefix: str = ""
    kind: str = "text"
    rows: str = ""
    anchor: int = -1

    @property
    def at(self):
        return self.start if self.anchor < 0 else self.anchor


def _table_pieces(text, block, limit):
    """A table too long for one chunk, cut between rows; every piece but the
    first gets the header row (and a Markdown |---| rule under it) as a prefix."""
    rows = [(s, e) for s, e, _ in _lines(text[block.start:block.end])]
    rows = [(block.start + s, block.start + e) for s, e in rows]
    head = 2 if len(rows) > 2 and _MD_RULE.match(text[rows[1][0]:rows[1][1]]) else 1
    header = text[rows[0][0]:rows[head - 1][1]]
    budget = max(limit - count_tokens(header), limit // 4)
    out, group, size, first = [], [], 0, True
    for n, (s, e) in enumerate(rows[head:], 1):
        t = count_tokens(text[s:e])
        if group and size + t > budget:
            out.append((group, first))
            group, size, first = [], 0, False
        group.append((n, s, e))
        size += t
    if group:
        out.append((group, first))
    pieces = []
    for group, is_first in out:
        start = rows[0][0] if is_first else group[0][1]
        pieces.append(Piece(start, group[-1][2], "" if is_first else header, "table",
                            f"{group[0][0]}-{group[-1][0]}"))
    return pieces


def _cuts(text, start, end):
    """Positions in text[start:end] where a long paragraph may be cut: after a
    sentence or at a line break, else (no such place) between words."""
    cuts = [m.end() for m in _SENTENCE_END.finditer(text, start, end)]
    if not [c for c in cuts if c < end]:
        cuts = [start + m.end() for m in re.finditer(r"\s+", text[start:end])]
    return [c for c in cuts if start < c < end] + [end]


def _prose_pieces(text, start, end, limit, overlap):
    """A paragraph (or code) too long for one chunk, cut at sentence ends into
    pieces of at most `limit` tokens, each starting about `overlap` tokens before
    the previous one ended."""
    cuts = _cuts(text, start, end)
    pieces, at = [], start
    while at < end:
        stop = None
        for c in cuts:
            if c <= at:
                continue
            if stop is not None and count_tokens(text[at:c]) > limit:
                break
            stop = c
        stop = stop or end
        if count_tokens(text[at:stop]) > limit:    # one sentence longer than the limit
            words = [m.end() for m in re.finditer(r"\S+", text[at:stop])]
            take = [w for w in words if count_tokens(text[at:at + w]) <= limit]
            stop = at + (take[-1] if take else words[0])
        last = stop
        while last > at and text[last - 1].isspace():
            last -= 1
        pieces.append((at, last))
        if stop >= end:
            break
        # The next piece starts `overlap` tokens back, at a cut (or a word).
        back = [c for c in cuts if at < c < stop and count_tokens(text[c:stop]) <= overlap]
        nxt = back[0] if back and overlap else stop
        while nxt < end and text[nxt].isspace():
            nxt += 1
        at = nxt if nxt > at else stop
    return pieces


def split(text, limit=None, min_tokens=None, overlap=None):
    """The chunks of `text`, as Pieces in order (see the top of this file)."""
    limit = limit or CHUNK_TOKENS
    min_tokens = MIN_TOKENS if min_tokens is None else min_tokens
    overlap = OVERLAP_TOKENS if overlap is None else overlap
    pieces, cur = [], []                         # cur: blocks of the chunk being packed

    def flush(final=False):
        """End the chunk being packed. Headings at its very end belong to what
        follows them, so they stay in `cur` for the next chunk."""
        tail = []
        while not final and cur and cur[-1].kind == "heading":
            tail.insert(0, cur.pop())
        if cur:
            body = [b for b in cur if b.kind != "heading"] or cur
            pieces.append(Piece(cur[0].start, cur[-1].end,
                                kind="table" if all(b.kind == "table" for b in body) else
                                "code" if all(b.kind == "code" for b in body) else "text",
                                anchor=body[0].start))
        cur[:] = tail

    starts = {h[0] for h in headings(text)}
    for block in _blocks(text):
        size = sum(b.tokens for b in cur)
        # A heading (Markdown or a "Label:" line) starts a new chunk, unless the
        # chunk so far is too small to stand alone.
        starts_section = block.kind == "heading" or block.start in starts
        if cur and starts_section and size >= min_tokens:
            flush()
            size = sum(b.tokens for b in cur)
        if block.tokens > (CODE_TOKENS if block.kind == "code" else limit):
            # Too big for any chunk: cut it up on its own (with the headings
            # just above it, and anything smaller than MIN_TOKENS before them).
            if cur and size >= min_tokens:
                flush()
            head = cur[0].start if cur else None
            anchor = next((b.start for b in cur if b.kind != "heading"), block.start)
            cur.clear()
            if block.kind == "table":
                parts = _table_pieces(text, block, limit)
                if head is not None:              # a small heading above the table
                    parts[0].start, parts[0].anchor = head, anchor
                pieces += parts
            else:
                for n, (s, e) in enumerate(_prose_pieces(
                        text, block.start, block.end,
                        CODE_TOKENS if block.kind == "code" else limit,
                        0 if block.kind == "code" else overlap)):
                    first = n == 0 and head is not None
                    pieces.append(Piece(head if first else s, e,
                                        kind="code" if block.kind == "code" else "text",
                                        anchor=anchor if first else -1))
            continue
        if cur and size + block.tokens > limit:
            flush()
        cur.append(block)
    flush(final=True)
    return pieces
