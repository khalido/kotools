"""local document → text via liteparse (and python-docx for .docx).

PDFs parse natively (Rust/PDFium, fully local, no models). .docx reads directly with
python-docx (`docx_markdown`): paragraphs and tables in document order, no LibreOffice.
Other Office docs, and a .docx when page markers are wanted, convert to PDF first —
liteparse shells out to LibreOffice / ImageMagick and errors helpfully if they're missing
(`brew install --cask libreoffice`, `brew install imagemagick`).

OCR is per page, and only where it's needed: a modern PDF carries a text layer,
and OCRing every page of a 132-page magazine took 109 s for ~6% more text than the
0.9 s text-layer read. `pages_needing_ocr` picks the scanned / textless / garbled
pages from liteparse's cheap `is_complex` pass; only those get OCR.
"""

from __future__ import annotations

import hashlib
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from liteparse import LiteParse

# liteparse's own `needs_ocr` also fires on "embedded-images" and "sparse-text" — i.e. any
# page with a photo (126 of 132 magazine pages). Those pages already have their text; OCR
# only adds words baked into images. These reasons mean the text layer is actually missing —
# "vector-text" is text drawn as outlines, which reads as nothing without OCR.
_NO_TEXT_LAYER = {"scanned", "no-text", "garbled", "vector-text"}


@contextmanager
def _quiet_stderr():
    """Tesseract prints "Detected N diacritics" straight to fd 2, past liteparse's `quiet`;
    stderr is for notes an agent can act on. Real failures still raise."""
    saved = os.dup(2)
    try:
        with open(os.devnull, "w") as null:
            os.dup2(null.fileno(), 2)
        yield
    finally:
        os.dup2(saved, 2)
        os.close(saved)


def pages_needing_ocr(path: str | Path) -> set[int]:
    """1-based page numbers whose text layer is missing or unusable (PDFs only)."""
    stats = LiteParse(quiet=True).is_complex(str(path))
    return {s.page_number for s in stats if s.full_page_image or _NO_TEXT_LAYER & set(s.reasons)}


_MAX_PAGES = 100_000


@dataclass
class Doc:
    path: Path
    total: int  # pages in the document, before any --pages selection
    pages: list[Page]
    markdown: bool = False  # page text is liteparse markdown, not layout-preserving plain text


@dataclass
class Page:
    num: int  # 1-based position in the document
    label: str | None  # the PDF's own page label ("iv", "A-1") when it defines one
    text: str


def _lp(*, ocr: bool, pages: str | None, password: str | None, markdown: bool) -> LiteParse:
    fmt = {"output_format": "markdown", "image_mode": "none"} if markdown else {}  # "none": no
    # `![](img_p6_1.png)` links to images that were never written anywhere
    # max_pages: liteparse stops at 1,000 by default — silently (a 1,113-page manual came back
    # as pages 1-1000, exit 0). ko wants the whole document; parse_pages checks it got it.
    return LiteParse(
        ocr_enabled=ocr, target_pages=pages, password=password, quiet=True, max_pages=_MAX_PAGES, **fmt
    )


def parse_pages(
    path: str | Path,
    pages: str | None = None,
    ocr: bool | None = None,
    password: str | None = None,
    markdown: bool = False,
) -> Doc:
    """Each page's text (or markdown: tables as pipe tables), in order — so a claim can cite
    its page. `ocr`: None (default) = OCR only the PDF pages that lack a text layer
    (non-PDFs: always); True = OCR every page; False = never."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(p)

    total = 0

    def read(lp: LiteParse) -> list[Page]:
        nonlocal total
        r = lp.parse(str(p))
        total = max(total, r.total_pages)
        return [Page(pg.page_num, pg.page_label, pg.markdown if markdown else pg.text) for pg in r.pages]

    if ocr is not None or p.suffix.lower() != ".pdf":
        with _quiet_stderr():
            out = read(_lp(ocr=ocr is not False, pages=pages, password=password, markdown=markdown))
        return Doc(p, total or len(out), out, markdown)
    out = read(_lp(ocr=False, pages=pages, password=password, markdown=markdown))
    if pages is None and len(out) < total:  # never hand back part of a document as if whole
        raise RuntimeError(f"{p.name}: liteparse returned {len(out)} of {total} pages")
    need = pages_needing_ocr(p) & {pg.num for pg in out}
    if need:
        with _quiet_stderr():
            target = ",".join(map(str, sorted(need)))
            ocred = {pg.num: pg for pg in read(_lp(ocr=True, pages=target, password=password, markdown=markdown))}
        out = [ocred.get(pg.num, pg) for pg in out]
    return Doc(p, total or len(out), out, markdown)


def _fence(text: str) -> str:
    """Plain text in a ```text block, so column alignment survives a markdown render. The fence
    outgrows any backtick run inside the text."""
    longest = max((len(m) for m in re.findall(r"`{3,}", text)), default=2)
    tick = "`" * (longest + 1)
    return f"{tick}text\n{text}\n{tick}"


def marked(doc: Doc) -> str:
    """A markdown document: a `<!-- source: … -->` provenance line (file, page count, md5 — so a
    citation pins the exact file — and how many pages had no text), then each page under a
    `<!-- page N -->` line: invisible when rendered, trivially greppable, what lets a reader
    trace a claim to its page. Plain-text pages sit in a ```text fence; --md pages don't."""
    def mark(pg: Page) -> str:
        label = f" · {pg.label}" if pg.label and pg.label != str(pg.num) else ""
        return f"<!-- page {pg.num}{label} -->"

    def body(pg: Page) -> str:
        text = pg.text.strip("\n")
        if not text.strip():
            return "(no text on this page)"
        return text.strip() if doc.markdown else _fence(text)

    md5 = hashlib.md5(doc.path.read_bytes()).hexdigest()
    empty = sum(1 for pg in doc.pages if not pg.text.strip())
    blank = f" · {empty} without text" if empty else ""
    head = f"<!-- source: {doc.path.name} · {doc.total} pages{blank} · md5 {md5} -->"
    return "\n\n".join([head, *(f"{mark(pg)}\n\n{body(pg)}" for pg in doc.pages)])


# --- .docx, read directly: Word has no pages until something lays it out ---

_HEADING = {"Title": 1, "Subtitle": 2}
_LIST_ITEM = re.compile(r"\s*(?:-|\d+\.|[a-zA-Z]\.|[ivxlcdmIVXLCDM]+\.) ")


def _cell(text: str) -> str:
    return text.strip().replace("|", "\\|").replace("\n", "<br>")


def _docx_table(table) -> str:
    """A pipe table. A merged cell is written once: python-docx repeats the same cell for
    every grid column (gridSpan) and row (vMerge) it covers, so any repeat — whatever the
    merge's shape — is left blank."""
    rows: list[list[str]] = []
    seen: set = set()
    for row in table.rows:
        cells = []
        for cell in row.cells:
            cells.append("" if cell._tc in seen else _cell(cell.text))
            seen.add(cell._tc)
        if any(cells):  # an all-blank row is Word spacing, not data
            rows.append(cells)
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    lines = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * width]
    lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(lines)


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _par_text(par) -> str:
    """Paragraph text from every w:t — python-docx's `.text` skips runs inside fields
    (w:fldSimple), which is where Word templates keep things like a judgment's title.
    Deleted text (w:delText) and field codes (w:instrText) stay out."""
    out = []
    for el in par._p.iter(f"{_W}t", f"{_W}tab", f"{_W}br", f"{_W}cr"):
        out.append(el.text or "" if el.tag == f"{_W}t" else "\t" if el.tag == f"{_W}tab" else "\n")
    return "".join(out)


def _roman(n: int) -> str:
    out = ""
    for v, r in ((1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"),
                 (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")):
        while n >= v:
            out, n = out + r, n - v
    return out


class _Numbering:
    """Word's list numbers, resolved: a paragraph's numPr (its own, or inherited from its
    style chain) → numbering.xml's format and start → a running count per list. A judgment's
    "1." "2." are citable; flattening them to bullets loses the reference."""

    def __init__(self, document):
        self.fmt: dict[tuple[str, int], tuple[str, int]] = {}
        try:
            root = document.part.numbering_part.element
        except (KeyError, NotImplementedError, AttributeError):
            root = None
        if root is not None:
            abstract = {a.get(f"{_W}abstractNumId"): a for a in root.iter(f"{_W}abstractNum")}
            for num in root.iter(f"{_W}num"):
                ref = num.find(f"{_W}abstractNumId")
                a = abstract.get(ref.get(f"{_W}val")) if ref is not None else None
                for lvl in a.iter(f"{_W}lvl") if a is not None else []:
                    fmt = lvl.find(f"{_W}numFmt")
                    start = lvl.find(f"{_W}start")
                    self.fmt[(num.get(f"{_W}numId"), int(lvl.get(f"{_W}ilvl")))] = (
                        fmt.get(f"{_W}val") if fmt is not None else "decimal",
                        int(start.get(f"{_W}val")) if start is not None else 1,
                    )
        self.count: dict[str, list[int]] = {}

    @staticmethod
    def _num_pr(par):
        if par._p.pPr is not None and par._p.pPr.numPr is not None:
            return par._p.pPr.numPr
        style = par.style
        while style is not None:  # numbering set on the style (e.g. "ParaNumbering")
            ppr = style.element.pPr
            if ppr is not None and ppr.numPr is not None:
                return ppr.numPr
            style = style.base_style
        return None

    def prefix(self, par) -> str | None:
        """"1. " / "a. " / "- " for a list paragraph (indented by level), None otherwise."""
        num_pr = self._num_pr(par)
        if num_pr is None or num_pr.numId is None:
            return None
        num_id, ilvl = str(num_pr.numId.val), num_pr.ilvl.val if num_pr.ilvl is not None else 0
        if num_id == "0":  # numId 0 = numbering explicitly switched off
            return None
        fmt, start = self.fmt.get((num_id, ilvl), ("bullet", 1))
        levels = self.count.setdefault(num_id, [0] * 9)
        levels[ilvl] += 1
        levels[ilvl + 1 :] = [0] * (8 - ilvl)  # a new item resets the levels under it
        n = start + levels[ilvl] - 1
        mark = {
            "decimal": f"{n}.", "lowerLetter": f"{chr(96 + n)}.", "upperLetter": f"{chr(64 + n)}.",
            "lowerRoman": f"{_roman(n)}.", "upperRoman": f"{_roman(n).upper()}.",
        }.get(fmt, "-" if fmt != "none" else "")
        return "  " * ilvl + (f"{mark} " if mark else "")


def _docx_paragraph(par, numbering: _Numbering) -> str:
    text = _par_text(par).strip()
    if not text:
        return ""
    style = par.style.name if par.style is not None else ""
    if style.startswith("Heading ") and style[8:].isdigit():
        return "#" * min(int(style[8:]), 6) + " " + text
    if style in _HEADING:
        return "#" * _HEADING[style] + " " + text
    prefix = numbering.prefix(par)
    if prefix is not None:
        return prefix + text
    if style.startswith("List"):
        return f"- {text}"
    return text


def docx_markdown(path: str | Path) -> tuple[str, str]:
    """(provenance line, markdown body) for a .docx — paragraphs and tables in document
    order, headings and lists from their Word styles. No LibreOffice, no pages."""
    import docx
    from docx.table import Table

    p = Path(path)
    d = docx.Document(str(p))
    numbering = _Numbering(d)
    blocks = [
        _docx_table(item) if isinstance(item, Table) else _docx_paragraph(item, numbering)
        for item in d.iter_inner_content()
    ]
    cp = d.core_properties
    bits = [p.name]
    if cp.author:
        bits.append(f"author {cp.author}")
    if cp.created:
        bits.append(f"created {cp.created:%Y-%m-%d}")
    if cp.modified:
        by = f" by {cp.last_modified_by}" if cp.last_modified_by and cp.last_modified_by != cp.author else ""
        bits.append(f"changed {cp.modified:%Y-%m-%d}{by}")
    data = p.read_bytes()
    bits += [f"{len(data) / 1024:,.0f} KB", f"md5 {hashlib.md5(data).hexdigest()}"]
    out: list[str] = []
    for b in filter(None, blocks):
        is_item = bool(_LIST_ITEM.match(b))
        if is_item and out and _LIST_ITEM.match(out[-1].split("\n")[-1]):
            out[-1] += "\n" + b  # consecutive list items: one tight list, not a blank line each
        else:
            out.append(b)
    return f"<!-- source: {' · '.join(bits)} -->", "\n\n".join(out)


def parse(
    path: str | Path,
    pages: str | None = None,
    ocr: bool | None = None,
    password: str | None = None,
) -> str:
    """Plain text of the whole document, pages joined as liteparse joins them (no markers) —
    for `fetch` and `llm -f`, which want one body. A .docx reads directly (no LibreOffice)."""
    if Path(path).suffix.lower() == ".docx" and pages is None:
        return docx_markdown(path)[1]
    return "\n\n".join(pg.text for pg in parse_pages(path, pages=pages, ocr=ocr, password=password).pages)
