"""local document → text via liteparse (and python-docx for .docx).

PDFs with a text layer parse natively (Rust/PDFium, local, no models); pages without one go
to OCR (`ocr.py`): Apple Vision on macOS, else a paid gateway model. .docx reads directly with
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
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from liteparse import LiteParse

# Under this much usable native text, a page may have lost its text to an image or outlines. Raw
# numbers before liteparse's `reasons`: `needs_ocr` fires on any page with a photo (126 of 132
# magazine pages), and "vector-text" fired on 23 pages of ~2,000 good characters (of 292 sampled,
# 2026-11-06). A short page with nothing else on it (a title, a divider) is already read.
_MIN_PAGE_TEXT = 200


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


def _hides_text(s) -> bool:
    """Could this short page hold text its layer doesn't? An image or outlines on it, or nothing
    native at all (liteparse skips the outline walk then, so the area is None)."""
    return bool(s.has_substantial_images or s.uncovered_vector_area or {"no-text", "vector-text"} & set(s.reasons))


def pages_needing_ocr(path: str | Path) -> set[int]:
    """1-based page numbers whose text layer is missing or unusable (`is_complex` converts Office files first)."""
    stats = LiteParse(quiet=True, max_pages=_MAX_PAGES).is_complex(str(path))
    return {s.page_number for s in stats if s.full_page_image or s.is_garbled or (s.text_length < _MIN_PAGE_TEXT and _hides_text(s))}


_MAX_PAGES = 100_000


@dataclass
class Doc:
    path: Path
    total: int  # pages in the document, before any --pages selection
    pages: list[Page]
    markdown: bool = False  # page text is liteparse markdown, not layout-preserving plain text


@dataclass
class Page:
    num: int | None  # 1-based position in the document; None for a pageless format
    label: str | None  # the PDF's own page label ("iv", "A-1") when it defines one
    text: str
    md: bool = False  # a model's markdown transcript (OCR), whatever the document's mode


def _lp(
    *, ocr: bool, pages: str | None, password: str | None, markdown: bool, server: tuple[str | None, int | None] = (None, None)
) -> LiteParse:
    fmt = {"output_format": "markdown", "image_mode": "none"} if markdown else {}  # "none": no
    # `![](img_p6_1.png)` links to images that were never written anywhere
    # max_pages: liteparse stops at 1,000 by default — silently (a 1,113-page manual came back
    # as pages 1-1000, exit 0). ko wants the whole document; parse_pages checks it got it.
    url, workers = server  # ocr.running(): our OCR server, or (None, None) = liteparse's Tesseract
    via = {"ocr_server_url": url, "num_workers": workers} if url else {}
    return LiteParse(
        ocr_enabled=ocr, target_pages=pages, password=password, quiet=True, max_pages=_MAX_PAGES, **fmt, **via
    )


@contextmanager
def _ocr_server(name: str, fatal: bool = True):
    """Vision/Tesseract go through liteparse (ocr.running), Tesseract's stray stderr muted."""
    from ko import ocr

    with ocr.running(name, fatal=fatal) as server:
        if name == "tesseract":
            with _quiet_stderr():
                yield server
        else:
            yield server


# An image has no text layer: all of it is OCR. Anything else (PDF, or Office via LibreOffice)
# gets the text-layer read, then OCR only for pages without one.
_IMAGES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".bmp", ".gif", ".heic"}


def parse_pages(
    path: str | Path,
    pages: str | None = None,
    ocr: bool | None = None,
    password: str | None = None,
    markdown: bool = False,
    engine: str | None = None,
) -> Doc:
    """Each page's text (or markdown: tables as pipe tables), in order — so a claim can cite
    its page. `ocr`: None (default) = OCR only the pages that lack a text layer (an image:
    all of it); True = OCR every page; False = never. `engine`: see `ocr.engine` — a model
    transcribes the page images and its transcript replaces the page's text."""
    from ko import ocr as ocr_mod

    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(p)

    total = 0

    def read(lp: LiteParse) -> list[Page]:
        nonlocal total
        r = lp.parse(str(p))
        total = max(total, r.total_pages)
        return [Page(pg.page_num, pg.page_label, pg.markdown if markdown else pg.text) for pg in r.pages]

    out = read(_lp(ocr=False, pages=pages, password=password, markdown=markdown))
    if ocr is False:
        return Doc(p, total or len(out), out, markdown)
    if pages is None and len(out) < total:  # never hand back part of a document as if whole
        raise RuntimeError(f"{p.name}: liteparse returned {len(out)} of {total} pages")
    nums = {pg.num for pg in out}
    need = nums if ocr or p.suffix.lower() in _IMAGES else pages_needing_ocr(p) & nums
    if not need:
        return Doc(p, total or len(out), out, markdown)
    name = ocr_mod.engine(engine)
    if name in ocr_mod.LOCAL:
        rescue = ocr_mod.fallback()
        with _ocr_server(name, fatal=not rescue) as server:
            target = ",".join(map(str, sorted(need)))
            ocred = {pg.num: pg for pg in read(_lp(ocr=True, pages=target, password=password, markdown=markdown, server=server))}
        out = [ocred.get(pg.num, pg) for pg in out]
        empty = sorted(pg.num for pg in out if pg.num in need and not pg.text.strip())
        images = _page_images(p, empty, password)
        if not images:
            return Doc(p, total or len(out), out, markdown)
        pages_csv = ",".join(map(str, sorted(images)))
        if not rescue:
            print(f"[ocr: {name} read nothing on page(s) {pages_csv}; try a model: ko doc {p} --engine llm --pages {pages_csv}]", file=sys.stderr)
            return Doc(p, total or len(out), out, markdown)
        print(f"[ocr: {name} read nothing on page(s) {pages_csv}; sending them to {rescue.removeprefix('vercel:')}]", file=sys.stderr)
        name, need_images = rescue, images
    else:
        need_images = _page_images(p, sorted(need), password)
    if not need_images:  # blank pages only: nothing to pay for, and no key needed
        return Doc(p, total or len(out), out, markdown)
    text = ocr_mod.transcribe(name, need_images)
    if empty := sorted(n for n, t in text.items() if not t):
        print(f"[ocr: no transcript for page(s) {', '.join(map(str, empty))}; kept what the page had]", file=sys.stderr)
    out = [Page(pg.num, pg.label, text[pg.num], md=True) if text.get(pg.num) else pg for pg in out]
    return Doc(p, total or len(out), out, markdown)


def _page_images(path: Path, pages: list[int], password: str | None) -> dict[int, bytes]:
    """Page number -> rendered image, blank pages left out."""
    if not pages:
        return {}
    shots = LiteParse(password=password, quiet=True).screenshot(str(path), page_numbers=pages)
    return {s.page_num: s.image_bytes for s in shots if not s.is_solid_fill}


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
        if pg.num is None:  # a pageless format (.docx/.pptx via docling): no marker to give
            return ""
        label = f" · {pg.label}" if pg.label and pg.label != str(pg.num) else ""
        return f"<!-- page {pg.num}{label} -->\n\n"

    def body(pg: Page) -> str:
        text = pg.text.strip("\n")
        if not text.strip():
            return "(no text on this page)"
        return text.strip() if doc.markdown or pg.md else _fence(text)

    md5 = hashlib.md5(doc.path.read_bytes()).hexdigest()
    empty = sum(1 for pg in doc.pages if not pg.text.strip())
    blank = f" · {empty} without text" if empty else ""
    head = f"<!-- source: {doc.path.name} · {doc.total} pages{blank} · md5 {md5} -->"
    return "\n\n".join([head, *(f"{mark(pg)}{body(pg)}" for pg in doc.pages)])


# --- deep: docling, for layouts liteparse can't read (two-column prose) and figures ---

_WORKER = Path(__file__).with_name("_docling_worker.py")
_DESCRIBE_PROMPT = (
    "This figure is from a technical document. Describe it for someone who cannot see it: what "
    "it shows, and every label, value, terminal and connection that is visible. Transcribe any "
    "text exactly. No preamble. Under 120 words."
)


def _page_range(pages: str | None) -> list[int] | None:
    """`--pages` for docling, which takes one contiguous range: "5" or "5-9"."""
    if not pages:
        return None
    first, _, last = pages.partition("-")
    if not (first.strip().isdigit() and (not last or last.strip().isdigit())):
        raise ValueError(f"--deep takes one page range like '5' or '5-9', not {pages!r}")
    return [int(first), int(last or first)]


def deep(
    path: str | Path,
    pages: str | None = None,
    assets_dir: Path | None = None,
    describe: bool = False,
) -> tuple[Doc, int]:
    """Convert with docling — layout model + TableFormer, so two-column pages read in order —
    run in its own uv environment (torch + ~1 GB of models never enter ko's). Figures go to
    `assets_dir` and are linked in place (else a `<!-- figure -->` placeholder); `describe`
    adds a Gemini description under each (GEMINI_API_KEY, `[doc] describe_model`).
    Returns (Doc, figure count). Slow: ~3–4 s a page, and the first run downloads models."""
    import json
    import shutil
    import subprocess

    from . import config

    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(p)
    uv = shutil.which("uv")
    if not uv:
        raise RuntimeError("--deep runs docling via uv, which isn't on PATH — https://docs.astral.sh/uv/")
    args = {
        "path": str(p.resolve()),
        "pages": _page_range(pages),
        "ocr": p.suffix.lower() != ".pdf" or bool(pages_needing_ocr(p)),  # ko's rule: OCR only if a page lacks text
        "assets_dir": str(assets_dir.resolve()) if assets_dir else None,
        "describe": None,
    }
    if describe:
        config.require_key("GEMINI_API_KEY", "Get one at https://aistudio.google.com/apikey (figure descriptions use Gemini).")
        args["describe"] = {
            "model": config.get("doc", "describe_model", "gemini-3.8-flash"),
            "prompt": _DESCRIBE_PROMPT,
        }
    r = subprocess.run(
        [uv, "run", "--no-project", "--quiet", "--with", "docling>=2.133", "python", str(_WORKER), json.dumps(args)],
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode != 0:
        tail = "\n".join(r.stderr.strip().splitlines()[-6:])
        raise RuntimeError(f"docling failed on {p.name}:\n{tail}")
    out = json.loads(r.stdout)
    if describe:
        d = out["described"]
        if d["tried"] and d["got"] < d["tried"]:
            why = f" — {d['errors'][0]}" if d["errors"] else ""
            raise RuntimeError(f"Gemini described {d['got']} of {d['tried']} figures{why}")
    doc_pages = [Page(pg["num"], None, pg["md"]) for pg in out["pages"]]
    if assets_dir is not None:
        _tidy_figures(doc_pages, assets_dir)
    return Doc(p, out["total"], doc_pages, markdown=True), out["figures"]


_FIG_REF = re.compile(r"!\[[^\]]*\]\(([^)\s]+\.png)\)")


def _tidy_figures(pages: list[Page], assets_dir: Path) -> None:
    """docling names figures by content hash (`image_000004_00358c…png`). Rename each to its
    page — `p018-fig1.png`, so a reader knows where it came from — and keep ONE file per
    distinct image: a logo repeated on every page is saved once and linked from each."""
    first_of: dict[str, str] = {}  # image md5 → the name its first appearance got
    for pg in pages:
        n = 0

        def rename(m: re.Match, pg: Page = pg) -> str:
            nonlocal n
            src = assets_dir / Path(m.group(1)).name
            if not src.is_file():
                return m.group(0)
            digest = hashlib.md5(src.read_bytes()).hexdigest()
            if digest in first_of:
                src.unlink()  # a repeat (header logo): link the copy already kept
                name = first_of[digest]
            else:
                n += 1
                name = f"p{pg.num or 0:03d}-fig{n}.png"
                src.rename(assets_dir / name)
                first_of[digest] = name
            return f"![{Path(name).stem}]({assets_dir.name}/{name})"

        pg.text = _FIG_REF.sub(rename, pg.text)


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
