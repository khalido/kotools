"""local document → text via liteparse.

PDFs parse natively (Rust/PDFium, fully local, no models). Office docs and
images convert to PDF first — liteparse shells out to LibreOffice /
ImageMagick and errors helpfully if they're missing (`brew install --cask
libreoffice`, `brew install imagemagick`).
"""

from __future__ import annotations

from pathlib import Path

from liteparse import LiteParse


def parse(
    path: str | Path,
    pages: str | None = None,
    ocr: bool = True,
    password: str | None = None,
) -> str:
    """Extract reading-order text from a PDF / Office doc / image. Returns plain text."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(p)
    # quiet=True: liteparse's timing diagnostics would pollute piped output
    parser = LiteParse(
        ocr_enabled=ocr, target_pages=pages, password=password, quiet=True
    )
    return parser.parse(str(p)).text
