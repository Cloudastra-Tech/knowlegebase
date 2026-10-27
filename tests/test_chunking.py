"""
Structure-aware chunking (chunking.py, pipeline.md P1-2) and the chunk header
(ingest.py, P1-3): every file type is cut where its structure says — at
headings, between table rows with the header row repeated, around whole code
blocks, page by page for PDFs — sized in tokens, and every chunk still points at
exactly its place in the document. Files are generated here
(tests/connector_fixtures.py); nothing calls OpenAI.
"""

import csv
import io
import os

import pytest

import chunking
import ingest
import rag
from connector_fixtures import make_docx, make_xlsx
from test_pdf_ingest import _pdf

LIMIT = 60          # tokens: small, so short fixtures already need several chunks
MIN_TOKENS = chunking.MIN_TOKENS


@pytest.fixture(autouse=True)
def small_sections(monkeypatch):
    """Sections this small still get chunks of their own."""
    monkeypatch.setattr(chunking, "MIN_TOKENS", 5)


def texts(text, pieces):
    return [text[p.start:p.end] for p in pieces]


def chunks_of(tmp_path, name, data, title=None):
    """ingest.load_and_split of one file written to tmp_path/public/<name>."""
    folder = tmp_path / "public"
    folder.mkdir(exist_ok=True)
    path = folder / name
    path.write_bytes(data) if isinstance(data, bytes) else path.write_text(data, "utf-8")
    key = f"public/{name}"
    meta = {"doc_key": key, "title": title or name, "access": "public"}
    return ingest.load_and_split(str(tmp_path), key, meta, "ns")


def assert_slices(tmp_path, name, chunks):
    """Every chunk's char_start / char_end is its own text's place in the document."""
    from connectors import extract
    data = (tmp_path / "public" / name).read_bytes()
    source = data.decode() if name.endswith(".txt") else extract.extract(data, name=name)
    for _, text, meta in chunks:
        assert source[meta["char_start"]:meta["char_end"]] == rag.passage_text(text, meta)


# ---- Tokens ----------------------------------------------------------------------
@pytest.mark.parametrize("text, tokens", [
    ("", 0), ("Refund policy", 2), ("2000", 2), ("Hi!", 2),
    ("internationalisation", 3),          # long words count more than one
])
def test_sizes_are_counted_in_tokens(text, tokens):
    assert chunking.count_tokens(text) == tokens


def test_long_prose_is_cut_between_sentences_with_overlap():
    text = " ".join(f"Rule {n} says claims need a receipt." for n in range(60))
    pieces = chunking.split(text, limit=LIMIT, overlap=10)
    assert len(pieces) > 3
    for piece, body in zip(pieces, texts(text, pieces)):
        assert chunking.count_tokens(body) <= LIMIT
        assert body.startswith("Rule ") and body.endswith("receipt.")      # whole sentences
    for a, b in zip(pieces, pieces[1:]):
        assert b.start < a.end                                             # they overlap
    assert pieces[0].start == 0 and pieces[-1].end == len(text)


# ---- Markdown / HTML / Word: headings ---------------------------------------------
GUIDE = """# Staff guide

Welcome to the team.

## Leave

Everyone gets 18 days of paid leave a year. Unused days carry over, up to ten.

## Printer

Run this once from a terminal.

```bash
# this is a comment, not a heading
lpadmin -p office -E -v ipp://print.local/office
lpoptions -d office
```

## Contacts

Ask the IT desk."""


def test_markdown_is_cut_at_headings_and_code_stays_whole():
    pieces = chunking.split(GUIDE, limit=LIMIT, min_tokens=5)
    bodies = texts(GUIDE, pieces)
    # Each section is its own chunk, starting with its heading.
    assert [b.split("\n", 1)[0] for b in bodies] == ["# Staff guide", "## Leave", "## Printer",
                                                    "## Contacts"]
    printer = bodies[2]
    assert "lpadmin" in printer and printer.rstrip().endswith("```")      # the block is whole
    assert [t for _, _, t in chunking.headings(GUIDE)] == ["Staff guide", "Leave", "Printer",
                                                          "Contacts"]


def test_a_code_block_longer_than_a_chunk_is_kept_whole_up_to_twice_the_size(monkeypatch):
    monkeypatch.setattr(chunking, "CODE_TOKENS", 2 * LIMIT)
    code = "```\n" + "\n".join(f"step_{n} = run({n})" for n in range(12)) + "\n```"
    assert LIMIT < chunking.count_tokens(code) <= 2 * LIMIT
    (piece,) = chunking.split("## Script\n\n" + code, limit=LIMIT)
    assert piece.kind == "code" and piece.end == len("## Script\n\n" + code)


def test_a_tiny_section_is_kept_with_the_next_one():
    text = "Handbook 2027\n\nAbout us:\nWe teach AI.\n\nRefund policy:\n" + \
           "Customers get a full refund within 30 days of purchase, then credit only."
    bodies = texts(text, chunking.split(text, limit=LIMIT, min_tokens=8))
    # The title line alone would be a useless chunk: it goes with "About us".
    assert bodies[0].startswith("Handbook 2027\n\nAbout us:")
    assert bodies[1].startswith("Refund policy:")


def test_markdown_upload_chunks_know_their_heading_path(tmp_path, monkeypatch):
    monkeypatch.setattr(chunking, "CHUNK_TOKENS", LIMIT)
    chunks = chunks_of(tmp_path, "guide.md", GUIDE.replace("## Contacts", "### Contacts"))
    paths = [m.get("heading_path") for _, _, m in chunks]
    assert "Staff guide › Leave" in paths and "Staff guide › Printer › Contacts" in paths
    assert {m["block"] for _, _, m in chunks} <= {"text", "code", "table"}
    assert_slices(tmp_path, "guide.md", chunks)


def test_html_headings_split_the_page(tmp_path, monkeypatch):
    monkeypatch.setattr(chunking, "CHUNK_TOKENS", LIMIT)
    page = ("<html><body><main><h1>Travel</h1><p>Book through the desk.</p>"
            "<h2>Flights</h2><p>" + "Economy class for trips under six hours. " * 6 + "</p>"
            "<h2>Hotels</h2><p>Up to 150 euros a night.</p></main></body></html>")
    chunks = chunks_of(tmp_path, "travel.html", page)
    paths = {m.get("heading_path") for _, _, m in chunks}
    assert {"Travel › Flights", "Travel › Hotels"} <= paths
    assert_slices(tmp_path, "travel.html", chunks)


# ---- Tables: Word, Excel, CSV ------------------------------------------------------
def _rows_whole(body, header):
    """Every line of a table chunk's text is one whole row (same cell count)."""
    width = header.count("|") if "|" in header else header.count(",")
    sep = "|" if "|" in header else ","
    return all(line.count(sep) == width for line in body.splitlines() if line.strip())


def test_word_table_rows_stay_whole_and_repeat_the_header(tmp_path, monkeypatch):
    monkeypatch.setattr(chunking, "CHUNK_TOKENS", LIMIT)
    rows = [["Region", "Daily limit", "Approver"]] + [
        [f"Region {n}", f"{100 + n} euros", f"Manager {n}"] for n in range(30)]
    chunks = chunks_of(tmp_path, "limits.docx", make_docx(["Travel limits by region."],
                                                          heading="Limits", table=rows))
    table = [(t, m) for _, t, m in chunks if m["block"] == "table"]
    assert len(table) > 2
    header = "Region | Daily limit | Approver"
    for text, meta in table[1:]:
        body = rag.passage_text(text, meta)
        assert header in text and header not in body        # a prefix, not the passage
        assert _rows_whole(body, header)
        first, last = map(int, meta["rows"].split("-"))
        assert last - first + 1 == len(body.splitlines())
    assert_slices(tmp_path, "limits.docx", chunks)


def test_excel_sheets_are_sections_and_rows_keep_their_header(tmp_path, monkeypatch):
    monkeypatch.setattr(chunking, "CHUNK_TOKENS", LIMIT)
    data = make_xlsx({"Budget": [["Team", "Quarter", "Spend"]] +
                      [[f"Team {n}", "Q3", 1000 + n] for n in range(40)],
                      "Notes": [["Approved by the board"]]})
    chunks = chunks_of(tmp_path, "budget.xlsx", data)
    budget = [(t, m) for _, t, m in chunks if m.get("heading_path") == "Budget"]
    assert len(budget) > 2 and all("Team | Quarter | Spend" in t for t, _ in budget)
    assert any(m.get("heading_path") == "Notes" for _, _, m in chunks)
    # The row people ask about is in a chunk together with the column names.
    hit = next(t for t, _ in budget if "Team 33 | Q3 | 1033" in t)
    assert "Team | Quarter | Spend" in hit
    assert_slices(tmp_path, "budget.xlsx", chunks)


def test_a_google_sheet_exported_as_csv_repeats_its_header_row(tmp_path, monkeypatch):
    monkeypatch.setattr(chunking, "CHUNK_TOKENS", LIMIT)
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["Batch", "Course", "City", "Seats left"])
    for n in range(40):
        writer.writerow([f"B-{n:02}", "PY-110", "Pune, Kothrud" if n % 2 else "Delhi", n])
    # The Drive sync writes a Sheet as its title line, then the CSV (kb/services/sync.py).
    text = "Batch schedule\n\n" + out.getvalue()
    chunks = chunks_of(tmp_path, "sheet.txt", text, title="Batch schedule")
    rows = [(t, m) for _, t, m in chunks if m["block"] == "table"]
    assert len(rows) > 2
    for t, m in rows[1:]:
        body = rag.passage_text(t, m)
        assert t.split("\n")[1] == "Batch,Course,City,Seats left"         # after the title
        assert all(line.startswith("B-") for line in body.splitlines())   # whole rows
    assert any('B-33,PY-110,"Pune, Kothrud",33' in t for t, _ in rows)    # quoted commas
    assert_slices(tmp_path, "sheet.txt", chunks)


# ---- PDF: page by page -------------------------------------------------------------
def test_pdf_chunks_stay_on_their_page(tmp_path):
    chunks = chunks_of(tmp_path, "travel.pdf", _pdf([
        "Book every work trip through the travel desk two weeks ahead.",
        "Expense claims are filed in the finance portal within 30 days.",
    ]))
    assert [m["page"] for _, _, m in chunks] == [0, 1]
    assert "travel desk" in chunks[0][1] and "finance portal" in chunks[1][1]


def test_headings_carry_over_from_one_pdf_page_to_the_next():
    page1 = "Expenses:\nClaims are filed monthly."
    found1 = chunking.headings("\n\n" + page1)
    stack = chunking.heading_stack(found1, 10_000)
    page2 = "Receipts are kept for seven years."
    assert chunking.heading_path(chunking.headings(page2), 0, stack) == "Expenses"


# ---- The chunk header (P1-3) and the fingerprint -------------------------------------
def test_the_header_has_the_title_or_with_headings_the_section_too(tmp_path, monkeypatch):
    monkeypatch.setattr(chunking, "CHUNK_TOKENS", LIMIT)
    text = "Leave rules\n\n" + GUIDE
    plain = chunks_of(tmp_path, "Leave rules.txt", text)
    assert any(t.startswith("[Leave rules]\n## Leave") for _, t, _ in plain)
    monkeypatch.setattr(ingest, "CHUNK_CONTEXT", "headings")
    rich = chunks_of(tmp_path, "Leave rules.txt", text)
    assert any(t.startswith("[Leave rules › Staff guide › Leave]\n## Leave") for _, t, _ in rich)
    # Either way the passage a citation shows is the document's own text.
    assert [rag.passage_text(t, m) for _, t, m in plain] == \
        [rag.passage_text(t, m) for _, t, m in rich]


def test_the_llm_context_line_is_cached_and_never_part_of_the_passage(tmp_path, monkeypatch):
    import chunk_context
    from langchain_core.messages import AIMessage

    calls = []

    class Model:
        def invoke(self, prompt):
            calls.append(prompt)
            assert "<untrusted_data" in prompt                 # the document is spotlighted
            return AIMessage(content="  This part covers the leave allowance.  ")

    monkeypatch.setattr(chunking, "CHUNK_TOKENS", LIMIT)
    monkeypatch.setattr(ingest, "CHUNK_SUMMARY", "llm")
    monkeypatch.setattr(chunk_context, "_llm", lambda: Model())
    first = chunks_of(tmp_path, "policy.md", GUIDE + "\n\n<!-- v1 -->")
    assert calls and all(m["chunk_context"] == "This part covers the leave allowance."
                         for _, _, m in first)
    assert all("This part covers" in t and "This part covers" not in rag.passage_text(t, m)
               for _, t, m in first)
    n = len(calls)
    again = chunks_of(tmp_path, "policy.md", GUIDE + "\n\n<!-- v1 -->")
    assert len(calls) == n and [c for c, _, _ in again] == [c for c, _, _ in first]  # cached

    class Broken:
        def invoke(self, prompt):
            raise TimeoutError()

    monkeypatch.setattr(chunk_context, "_llm", lambda: Broken())
    failed = chunks_of(tmp_path, "other.md", GUIDE + "\n\nAnother document.")
    assert failed and all("chunk_context" not in m for _, _, m in failed)    # indexed anyway


@pytest.mark.parametrize("module, name, value", [
    (chunking, "CHUNK_TOKENS", 250), (chunking, "MIN_TOKENS", 40),
    (chunking, "OVERLAP_TOKENS", 10), (chunking, "VERSION", 99),
    (ingest, "CHUNK_CONTEXT", "headings"), (ingest, "CHUNK_SUMMARY", "llm"),
])
def test_chunking_settings_are_in_the_fingerprint(monkeypatch, module, name, value):
    """A change to any of them makes the app rebuild indexes (test_indexing.py)."""
    before = ingest.fingerprint()
    monkeypatch.setattr(module, name, value)
    assert ingest.fingerprint() != before


def test_the_settings_as_loaded_make_the_fingerprint(monkeypatch):
    monkeypatch.setattr(chunking, "MIN_TOKENS", MIN_TOKENS)     # not this file's fixture's
    assert ingest.fingerprint() == ingest.FINGERPRINT


def test_the_eval_corpus_has_no_chunk_crossing_a_section():
    """On the golden corpus: a chunk never holds two sections of real size (a
    heading inside a chunk has only headings or a tiny section above it)."""
    from eval import harness
    for root, _, files in os.walk(harness.CORPUS):
        for name in files:
            with open(os.path.join(root, name), encoding="utf-8") as f:
                text = f.read()
            found = chunking.headings(text)
            for piece in chunking.split(text, min_tokens=25):
                inside = [at for at, _, _ in found if piece.start < at < piece.end]
                for at in inside:
                    above = "\n".join(line for line in text[piece.start:at].splitlines()
                                      if not line.startswith("#"))
                    assert chunking.count_tokens(above) < 25, (name, above[:80])
