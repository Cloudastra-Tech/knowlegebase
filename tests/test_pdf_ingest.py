"""
PDF ingestion: a real PDF is read by ingest.main (PyPDFLoader -> pypdf), split
page by page, tagged by folder and found by rag.load_chain's search. Uses the
eval harness's offline index (deterministic embedding, no OpenAI).
"""

import rag
from eval import harness


def _pdf(pages):
    """A minimal, valid PDF with one page of Helvetica text per string."""
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", None,
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for text in pages:
        content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET"
        objs.append(f"<< /Length {len(content)} >>\nstream\n{content}\nendstream")
        objs.append("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                    f"/Resources << /Font << /F1 3 0 R >> >> /Contents {len(objs)} 0 R >>")
        kids.append(f"{len(objs)} 0 R")
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>"
    out, offsets = b"%PDF-1.4\n", []
    for n, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n{body}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


def test_a_pdf_is_indexed_page_by_page_and_found(tmp_path):
    (tmp_path / "public").mkdir()
    (tmp_path / "restricted").mkdir()
    (tmp_path / "public" / "travel.pdf").write_bytes(_pdf([
        "Book every work trip through the travel desk two weeks ahead.",
        "Expense claims are filed in the finance portal within 30 days of returning.",
    ]))
    (tmp_path / "restricted" / "bonus.pdf").write_bytes(_pdf([
        "The expense team bonus pool is nine lakh rupees this quarter.",
    ]))
    corpus = str(tmp_path)

    with harness.fixture_index(corpus=corpus):
        public = rag.load_chain("public", harness.COLLECTION)[0].invoke(
            "When are expense claims filed?")
        hr = rag.load_chain("hr", harness.COLLECTION)[0].invoke("expense team bonus pool")

    top = public[0]
    assert harness.source_of(top, corpus) == "public/travel.pdf"
    assert top.metadata["page"] == 1                     # the second page, 0-based
    assert "within 30 days of returning" in top.page_content
    assert top.metadata["access"] == "public"
    assert "restricted/bonus.pdf" not in [harness.source_of(d, corpus) for d in public]
    bonus = [d for d in hr if harness.source_of(d, corpus) == "restricted/bonus.pdf"]
    assert bonus and bonus[0].metadata["access"] == "restricted"
