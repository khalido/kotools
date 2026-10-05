"""Smoke tests for ko doc (liteparse). No network, no external converters —
uses a minimal hand-built PDF (PDFium reconstructs the missing xref table).
"""

import pytest

from ko import doc

MINIMAL_PDF = b"""%PDF-1.4
1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj
2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj
3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >> endobj
4 0 obj << /Length 45 >> stream
BT /F1 24 Tf 72 720 Td (Hello ko doc) Tj ET
endstream
endobj
5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj
trailer << /Root 1 0 R /Size 6 >>
%%EOF
"""


@pytest.fixture
def pdf_file(tmp_path):
    p = tmp_path / "hello.pdf"
    p.write_bytes(MINIMAL_PDF)
    return p


def test_parse_pdf(pdf_file):
    text = doc.parse(pdf_file, ocr=False)
    assert "Hello ko doc" in text


def test_parse_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        doc.parse(tmp_path / "nope.pdf")


def test_auto_ocr_skips_a_pdf_with_a_text_layer(pdf_file, monkeypatch):
    calls = []
    real = doc.LiteParse

    def spy(**kw):
        calls.append(kw.get("ocr_enabled"))
        return real(**kw)

    monkeypatch.setattr(doc, "LiteParse", spy)
    monkeypatch.setattr(doc, "pages_needing_ocr", lambda p: set())  # every page has its text
    assert "Hello ko doc" in doc.parse(pdf_file)
    assert True not in calls  # text layer present: OCR never enabled


def test_auto_ocr_splices_ocr_pages_into_the_text_layer_read(tmp_path, monkeypatch):
    """Only the pages without a text layer are OCR'd, and they land back in page order."""
    from types import SimpleNamespace as NS

    f = tmp_path / "mixed.pdf"
    f.write_bytes(b"%PDF-")

    def pg(n, t):
        return NS(page_num=n, page_label=None, text=t, markdown=f"**{t}**")

    class FakeLP:
        def __init__(self, ocr_enabled=False, target_pages=None, **kw):
            self.ocr, self.target = ocr_enabled, target_pages

        def parse(self, path):
            if self.ocr:
                assert self.target == "2"  # only the scanned page
                return NS(pages=[pg(2, "ocr two")], total_pages=3)
            return NS(pages=[pg(1, "one"), pg(2, ""), pg(3, "three")], total_pages=3)

    monkeypatch.setattr(doc, "LiteParse", FakeLP)
    monkeypatch.setattr(doc, "pages_needing_ocr", lambda p: {2})
    assert doc.parse(f) == "one\n\nocr two\n\nthree"
    md = doc.parse_pages(f, markdown=True)
    assert [p.text for p in md.pages] == ["**one**", "**ocr two**", "**three**"]


def test_marked_fences_plain_pages_and_counts_empty_ones(tmp_path):
    f = tmp_path / "m.pdf"
    f.write_bytes(b"x")
    d = doc.Doc(f, 9, [doc.Page(1, None, "  col a     col b\n"), doc.Page(2, "ii", "  ")])
    assert doc.marked(d) == (
        "<!-- source: m.pdf · 9 pages · 1 without text · md5 9dd4e461268c8034f5c8564e155c67a6 -->\n\n"
        "<!-- page 1 -->\n\n```text\n  col a     col b\n```\n\n"
        "<!-- page 2 · ii -->\n\n(no text on this page)"
    )
    md = doc.Doc(f, 1, [doc.Page(1, None, "| a | b |\n")], markdown=True)
    assert doc.marked(md).endswith("<!-- page 1 -->\n\n| a | b |")  # --md pages: no fence
    assert doc._fence("x ``` y").startswith("````text")  # the fence outgrows backticks inside


def test_pages_needing_ocr_ignores_pages_that_merely_have_images(monkeypatch):
    """liteparse flags any page with a photo; ko OCRs only missing/garbled text layers."""
    from types import SimpleNamespace as NS

    stats = [
        NS(page_number=1, reasons=["embedded-images", "sparse-text"], full_page_image=False),
        NS(page_number=2, reasons=["scanned"], full_page_image=True),
        NS(page_number=3, reasons=["garbled"], full_page_image=False),
        NS(page_number=4, reasons=[], full_page_image=True),
    ]
    monkeypatch.setattr(doc, "LiteParse", lambda **k: NS(is_complex=lambda p: stats))
    assert doc.pages_needing_ocr("x.pdf") == {2, 3, 4}


def test_docx_reads_in_order_with_merged_cells_once_and_a_provenance_line(tmp_path):
    import docx

    d = docx.Document()
    d.core_properties.author = "KO"
    d.add_heading("Spec", level=1)
    d.add_paragraph("Intro paragraph.")
    t = d.add_table(rows=3, cols=3)
    for i, row in enumerate(t.rows):
        for j, cell in enumerate(row.cells):
            cell.text = f"r{i}c{j}"
    t.cell(0, 0).merge(t.cell(0, 1)).text = "wide"  # horizontal merge
    t.cell(1, 2).merge(t.cell(2, 2)).text = "tall"  # vertical merge
    d.add_paragraph("Item one", style="List Bullet")
    d.add_paragraph("Item two", style="List Bullet")
    d.add_paragraph("After | the table.")
    f = tmp_path / "s.docx"
    d.save(f)

    head, body = doc.docx_markdown(f)
    assert head.startswith("<!-- source: s.docx · author KO · created ") and " KB · md5 " in head
    assert body.split("\n\n") == [
        "# Spec",
        "Intro paragraph.",
        "| wide |  | r0c2 |\n|---|---|---|\n| r1c0 | r1c1 | tall |\n| r2c0 | r2c1 |  |",
        "- Item one\n- Item two",
        "After | the table.",
    ]
    assert doc.parse(f) == body  # llm -f / fetch get the same body, no LibreOffice


def test_docx_keeps_word_list_numbers_and_text_inside_fields(tmp_path):
    import docx
    from docx.oxml import parse_xml

    d = docx.Document()
    title = d.add_paragraph()
    title._p.append(parse_xml(
        '<w:fldSimple xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'w:instr=" DOCPROPERTY Title "><w:r><w:t>Federal Court of Australia</w:t></w:r></w:fldSimple>'
    ))
    d.add_paragraph("First reason.", style="List Number")  # numbering comes from the style
    d.add_paragraph("Second reason.", style="List Number")
    f = tmp_path / "j.docx"
    d.save(f)
    body = doc.docx_markdown(f)[1]
    assert body.split("\n\n") == ["Federal Court of Australia", "1. First reason.\n2. Second reason."]
