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
