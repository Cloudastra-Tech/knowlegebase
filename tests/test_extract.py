"""
connectors/extract.py: text out of every file type connectors and uploads
bring in, from small files generated here (tests/connector_fixtures.py), and
the limits that keep a hostile file from doing harm.
"""

import io
import zipfile

import pytest

from connector_fixtures import make_docx, make_pdf, make_pptx, make_xlsx
from connectors import extract

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
FRENCH = "Le café est très bon, merci pour le déjeuner à la française et la crème brûlée."


def test_word_paragraphs_headings_and_tables():
    data = make_docx(["The travel policy needs approval.", "Second paragraph."],
                     heading="Travel", table=[["Region", "Limit"], ["EU", "150"]])
    text = extract.extract(data, DOCX)
    assert text.splitlines()[:3] == ["# Travel", "The travel policy needs approval.",
                                     "Second paragraph."]
    assert "Region | Limit" in text and "EU | 150" in text


def test_powerpoint_slides_in_order_with_speaker_notes():
    data = make_pptx([["Q3 plan", "Grow revenue"], ["Hiring", "Two engineers"]],
                     notes={2: "Mention the budget"})
    text = extract.extract(data, name="deck.pptx")
    assert text.index("## Slide 1") < text.index("Q3 plan") < text.index("## Slide 2")
    assert "Two engineers" in text and "Notes: Mention the budget" in text


def test_excel_sheets_shared_strings_numbers_and_dates():
    data = make_xlsx({"Budget": [["Team", "Amount", "Due"], ["Sales", 1200, ("date", 45658)]],
                      "Other": [["x"]]})
    text = extract.extract(data, name="budget.xlsx")
    assert "## Budget" in text and "## Other" in text
    assert "Team | Amount | Due" in text
    assert "Sales | 1200 | 2025-01-01" in text


def test_html_drops_boilerplate_and_keeps_main():
    page = b"""<html><head><title>Refunds | Acme Help</title><style>p{}</style></head>
    <body><nav>Home Pricing Login</nav><header>Acme</header>
    <main><h1>Refunds</h1><p>Refunds take <b>5 days</b>.</p><ul><li>Card</li><li>Bank</li></ul>
    <script>steal()</script></main><footer>(c) Acme</footer></body></html>"""
    page_obj = extract.html_page(page, "https://help.acme.com/refunds")
    assert page_obj.title == "Refunds | Acme Help"
    assert [line for line in page_obj.text.splitlines() if line] == [
        "# Refunds", "Refunds take 5 days.", "- Card", "- Bank"]
    assert extract.extract(page, "text/html") == page_obj.text
    for junk in ("Pricing", "Acme\n", "steal", "(c) Acme", "p{}"):
        assert junk not in page_obj.text


def test_html_table_rows_are_consecutive_lines():
    """chunking.py reads consecutive "a | b" lines as one table."""
    page = (b"<p>Prices:</p><table><tr><th>Plan</th><th>Price</th></tr>"
            b"<tr><td>Pro</td><td>2000</td></tr><tr><td>Team</td><td>5000</td></tr></table>"
            b"<p>Billed yearly.</p>")
    assert extract.extract(page, "text/html") == (
        "Prices:\n\nPlan | Price\nPro | 2000\nTeam | 5000\n\nBilled yearly.")


def test_html_links_and_robots_meta():
    page = b"""<html><head><meta name="robots" content="noindex, nofollow"></head><body>
    <a href="/a#top">A</a><a href="b.html">B</a><a href="https://other.com/x">X</a>
    <a rel="nofollow" href="/hidden">H</a></body></html>"""
    got = extract.html_page(page, "https://docs.acme.com/guide/")
    assert got.links == ["https://docs.acme.com/a", "https://docs.acme.com/guide/b.html",
                         "https://other.com/x"]
    assert got.noindex and got.nofollow


@pytest.mark.parametrize("data, mime, name, expected", [
    (b"# Title\n\nSome *markdown*.", "", "notes.md", "# Title\n\nSome *markdown*."),
    (b"plain \r\ntext\n\n\n\nend  ", "text/plain", "", "plain\ntext\n\nend"),
    (FRENCH.encode("latin-1"), "text/plain", "", FRENCH),
    (b"\xef\xbb\xbfwith a BOM", "", "a.txt", "with a BOM"),
])
def test_text_types_are_decoded_and_tidied(data, mime, name, expected):
    assert extract.extract(data, mime, name) == expected


def test_pdf_text():
    assert "Hello from a PDF" in extract.extract(make_pdf("Hello from a PDF"), "application/pdf")


def test_kind_comes_from_the_mime_type_then_the_name():
    assert extract.kind_of("text/html; charset=utf-8") == "html"
    assert extract.kind_of("", "Report.DOCX") == "docx"
    assert extract.kind_of("application/octet-stream", "x.bin") is None


@pytest.mark.parametrize("data, mime", [
    (b"not a zip", DOCX),                                     # says Word, isn't
    (b"%PDF-1.4 broken", "application/pdf"),
    (b"\x89PNG....", "image/png"),                            # a type we don't read
])
def test_unreadable_or_unsupported_files_are_refused(data, mime):
    with pytest.raises(extract.Unsupported):
        extract.extract(data, mime)


def test_a_zip_bomb_is_refused(monkeypatch):
    monkeypatch.setattr(extract, "MAX_UNPACKED", 10_000)
    big = make_docx(["x" * 50_000])                           # unpacks past the limit
    assert len(big) < 10_000
    with pytest.raises(extract.Unsupported, match="too much"):
        extract.extract(big, DOCX)


def test_xml_entity_tricks_are_refused():
    """A billion-laughs style document: defusedxml refuses entity declarations."""
    evil = ('<?xml version="1.0"?><!DOCTYPE d [<!ENTITY a "aaaaaaaaaa">'
            '<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">]>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:body><w:p><w:r><w:t>&b;</w:t></w:r></w:p></w:body></w:document>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", evil)
    with pytest.raises(extract.Unsupported):
        extract.extract(buf.getvalue(), DOCX)


def test_text_is_capped(monkeypatch):
    monkeypatch.setattr(extract, "MAX_CHARS", 100)
    assert len(extract.extract(b"word " * 1000, "text/plain")) == 100


# ---- Uploads use the same extraction ----------------------------------------------------
def test_uploaded_office_files_are_read_into_chunks(tmp_path):
    import ingest
    folder = tmp_path / "public"
    folder.mkdir()
    (folder / "plan.docx").write_bytes(make_docx(["Hiring two engineers in Q3."]))
    (folder / "budget.xlsx").write_bytes(make_xlsx({"Q3": [["Team", "Spend"], ["Eng", 900]]}))
    (folder / "faq.md").write_text("# FAQ\n\nWifi password: on the fridge.")
    assert ingest.doc_files(str(tmp_path)) == ["public/budget.xlsx", "public/faq.md",
                                               "public/plan.docx"]
    for key, expected in (("public/plan.docx", "Hiring two engineers"),
                          ("public/budget.xlsx", "Eng | 900"),
                          ("public/faq.md", "Wifi password")):
        chunks = ingest.load_and_split(str(tmp_path), key, {"doc_key": key}, "ns")
        assert any(expected in text for _, text, _ in chunks), key


def test_the_upload_endpoint_takes_the_new_types_and_checks_them(monkeypatch, tmp_path):
    import uuid
    from fastapi.testclient import TestClient
    import api
    import auth
    import members_store
    from kb.services import indexing
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(indexing, "schedule_index", lambda ws, docs=None, full=False: None)
    ws = f"w-{uuid.uuid4().hex[:8]}"
    members_store.set_password(f"admin@{ws}.example", "Admin",
                               auth.hash_password("correct-horse"), ws, "admin")
    client = TestClient(api.app)
    token = client.post("/api/auth/login", json={"username": f"admin@{ws}.example",
                                                 "password": "correct-horse"}).json()["token"]
    headers = {"Authorization": f"Bearer {token}", "X-Role": "hr"}
    r = client.post("/api/docs/upload", headers=headers, data={"access": "public"}, files=[
        ("files", ("plan.docx", make_docx(["Q3 plan"]), DOCX)),
        ("files", ("deck.pptx", make_pptx([["Slide"]]), "application/octet-stream")),
        ("files", ("notes.md", b"# Notes", "text/markdown"))])
    assert r.status_code == 200, r.text
    assert sorted(r.json()["saved"]) == ["deck.pptx", "notes.md", "plan.docx"]
    r = client.post("/api/docs/upload", headers=headers, data={"access": "public"},
                    files=[("files", ("fake.xlsx", b"just text", "text/plain"))])
    assert r.status_code == 415 and "isn't an Excel file" in r.json()["detail"]
    # Office files and pages are downloaded as files, never served as a web page.
    r = client.get("/api/docs/public/notes.md", headers=headers)
    assert r.headers["content-type"].startswith("text/markdown")
