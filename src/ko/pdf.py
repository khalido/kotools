"""Markdown → print-ready PDF via WeasyPrint. Local, no models, no service.

Ported from ops/scripts/md_to_pdf.py, which was written for client handover docs. Its look
survives as the `report` style; its two footguns did not (see `load_brand` and the
`.page-break` note below).

**Styles.** A style is a named bundle of page furniture, not a whole stylesheet:

    note    typography + a page footer                (default — the quick PDF)
    report  + letterhead, Contents, numbered sections (the ops handover look)
    bare    typography only                           (a canvas for --css)

plus any `~/.config/ko/pdf/<name>.css`, which layers on `note`. Defaults follow Quarto,
where `toc` and `number-sections` are both opt-in: most documents are a page of notes, and
a Contents block listing two headings is furniture, not navigation.

Numbered sections are tied to the Contents block rather than being separately switchable —
the TOC prints "N." prefixes that have to match the body's CSS counter, so splitting them
is a way to render a document whose two halves disagree.

WeasyPrint needs pango/cairo, which on macOS come from Homebrew in a directory dyld does
not search. dyld reads its search path at process start, so no in-process fix exists
(ctypes preloading does not satisfy cffi's dlopen-by-leafname). The HTML is therefore built
here and handed to a short-lived child interpreter with the path set — see `_write_pdf`.
Zero setup for the caller, which matters because agents call this too.
"""

from __future__ import annotations

import datetime as dt
import html as html_lib
import os
import re
import subprocess
import sys
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from ko import config, dirs

# --- Brand -------------------------------------------------------------------------

# Deliberately impersonal: ko is public. Real identity lives in a private brand.yml
# (https://posit-dev.github.io/brand-yml/) pointed at by `[pdf] brand`. This flat dict is
# the runtime shape; brand.yml is flattened into it.
BRAND = {
    "name": "yourbrand.dev",
    "tagline": "",
    "email": "",
    "phone": "",
    "site": "",
    "abn": "",
    "accent": "#3b82f6",
}


def _flatten_brand(byml: dict) -> dict:
    """Map a brand.yml structure to the flat fields the letterhead uses. Contact fields
    (email/phone/abn) and `tagline` ride along in `meta` as tool extensions — brand.yml's
    core schema doesn't define them, so other consumers ignore them; `color.primary`
    resolves through `color.palette`."""
    meta = byml.get("meta") or {}
    links = meta.get("links") or {}
    color = byml.get("color") or {}
    palette = color.get("palette") or {}

    def role(name: str):  # a semantic colour → its palette hex, or a literal, or None
        v = color.get(name)
        return palette.get(v, v) if v else None

    site = meta.get("site") or links.get("home")
    if isinstance(site, str):
        site = site.split("://", 1)[-1].rstrip("/")
    out = {
        "name": meta.get("name"),
        "tagline": meta.get("tagline"),
        "email": meta.get("email"),
        "phone": meta.get("phone"),
        "abn": meta.get("abn"),
        "site": site,
        "accent": role("primary") or role("link"),
    }
    return {k: v for k, v in out.items() if v is not None}


def brand_path() -> Path | None:
    """Where the brand.yml lives: KO_BRAND env > `[pdf] brand` in config.toml > nowhere."""
    raw = config.setting("KO_BRAND", "pdf", "brand")
    return Path(raw).expanduser() if raw else None


def load_brand(path: Path | None = None) -> dict:
    """Generic defaults merged with a brand.yml. `path` (the --brand flag) wins over the
    configured one. Raises FileNotFoundError if a path was given but doesn't exist — the
    ops script ignored a bad path and shipped a placeholder letterhead instead."""
    import yaml

    brand = dict(BRAND)
    src = path or brand_path()
    if src:
        brand.update(_flatten_brand(yaml.safe_load(src.read_text()) or {}))
    return brand


def is_placeholder(brand: dict) -> bool:
    """True when no brand.yml resolved, so anything showing the brand would print the
    generic default. Worth a stderr note: the output looks finished but isn't."""
    return brand.get("name") == BRAND["name"]


# --- Styles ------------------------------------------------------------------------


@dataclass(frozen=True)
class Style:
    """A named bundle of page furniture. `toc` also switches on numbered sections."""

    name: str
    letterhead: bool = False
    toc: bool = False
    footer: bool = True
    css: str = ""  # appended last, so a user style overrides the built-in sheets

    @property
    def shows_brand(self) -> bool:
        return self.letterhead or self.footer


BUILTIN_STYLES = {
    "note": Style("note"),
    "report": Style("report", letterhead=True, toc=True),
    "bare": Style("bare", footer=False),
}

DEFAULT_STYLE = "note"


def user_style_dir() -> Path:
    """`~/.config/ko/pdf/` — drop `<name>.css` in here and it becomes `--style <name>`."""
    return dirs.config_dir() / "pdf"


def style_names() -> list[str]:
    """Built-in styles first, then whatever the user has dropped in their style dir."""
    extra = sorted(p.stem for p in user_style_dir().glob("*.css")) if user_style_dir().is_dir() else []
    return list(BUILTIN_STYLES) + [n for n in extra if n not in BUILTIN_STYLES]


def resolve_style(name: str | None) -> Style:
    """A style by name: built-in, else `~/.config/ko/pdf/<name>.css` layered on `note`.
    An unknown name raises ValueError rather than quietly falling back — a typo'd style
    that renders the default look is indistinguishable from the style not working."""
    name = name or config.setting("KO_PDF_STYLE", "pdf", "style") or DEFAULT_STYLE
    if name in BUILTIN_STYLES:
        return BUILTIN_STYLES[name]
    path = user_style_dir() / f"{name}.css"
    if path.is_file():
        return replace(BUILTIN_STYLES["note"], name=name, css=path.read_text())
    raise ValueError(f"unknown style {name!r} — available: {', '.join(style_names())}")


# --- Stylesheets -------------------------------------------------------------------

PAGE_CSS = """
@page {
  size: A4;
  margin: 2cm 2cm 2.4cm 2cm;
  @bottom-left { content: element(footerLeft); }
  @bottom-right {
    content: counter(page) " / " counter(pages);
    font-family: -apple-system, system-ui, sans-serif;
    font-size: 8pt;
    color: #888;
  }
}
"""

# No footer boxes — nothing would populate them.
PAGE_CSS_NO_FOOTER = "@page { size: A4; margin: 2cm; }\n"

# Typography only: what every style gets.
BASE_CSS = """
body {
    font-family: -apple-system, "Helvetica Neue", "Segoe UI", Arial, sans-serif;
    font-size: 10pt;
    line-height: 1.42;
    color: #1a1a1a;
    margin: 0;
}

h1 {
    font-size: 16pt;
    margin-top: 0;
    margin-bottom: 0.4em;
    border-bottom: 2px solid #333;
    padding-bottom: 0.25em;
    page-break-after: avoid;
}
h2 {
    font-size: 13pt;
    margin-top: 1.4em;
    margin-bottom: 0.4em;
    border-bottom: 1px solid #ccc;
    padding-bottom: 0.2em;
    page-break-after: avoid;
}
h3 { font-size: 12pt; margin-top: 1.2em; margin-bottom: 0.4em; page-break-after: avoid; }
h4 {
    font-size: 10.5pt;
    font-weight: 600;
    margin-top: 1.1em;
    margin-bottom: 0.3em;
    page-break-after: avoid;
}

p { margin: 0.5em 0; }
ul, ol { margin: 0.5em 0; padding-left: 1.5em; }
li { margin: 0.2em 0; }
li > p { margin: 0.2em 0; }

a { color: #1a5490; text-decoration: none; word-break: break-word; }

code {
    font-family: "SF Mono", Menlo, Consolas, monospace;
    font-size: 9.5pt;
    background: #f4f4f4;
    padding: 0.1em 0.35em;
    border-radius: 3px;
}

pre {
    font-family: "SF Mono", Menlo, Consolas, monospace;
    font-size: 8pt;
    background: #f8f8f8;
    border: 1px solid #e0e0e0;
    border-radius: 4px;
    padding: 0.7em 0.9em;
    line-height: 1.4;
    page-break-inside: avoid;
    overflow: hidden;
    white-space: pre;
}
pre code { background: transparent; padding: 0; font-size: inherit; border-radius: 0; }

table {
    width: 100%;
    border-collapse: collapse;
    margin: 1em 0;
    font-size: 9pt;
    page-break-inside: avoid;
}
th, td {
    border: 1px solid #d0d0d0;
    padding: 0.35em 0.6em;
    text-align: left;
    vertical-align: top;
}
th { background: #f0f0f0; font-weight: 600; }

blockquote {
    border-left: 3px solid #ccc;
    margin: 1em 0;
    padding: 0.1em 1em;
    color: #555;
}

hr { border: none; border-top: 1px solid #ddd; margin: 2em 0; }
strong { font-weight: 600; }

/* Put <div class="page-break"></div> in the markdown to force a page here. Replaces the
   ops script's per-document `h2#some-slug { page-break-before: always }` rule, which
   silently stopped working whenever that heading was reworded. */
.page-break { break-before: page; }

/* Footnotes (`[^1]`) — small dim block at the doc end; print hides the backref arrows. */
sup.footnote-ref a { color: #1a5490; text-decoration: none; }
.footnotes {
    margin-top: 1.2em;
    padding-top: 0.5em;
    border-top: 1px solid #e2e2e2;
    font-size: 8pt;
    color: #555;
}
.footnotes ol { margin: 0.2em 0; padding-left: 1.2em; }
.footnote-backref { display: none; }
"""

NUMBERING_CSS = """
body { counter-reset: section; }
h2 { counter-increment: section; }
h2::before { content: counter(section) ". "; color: #888; font-weight: 500; }
"""

LETTERHEAD_CSS = """
.letterhead {
    border-bottom: 2px solid #61afef;
    padding-bottom: 0.55em;
    margin-bottom: 1.5em;
}
.letterhead .lh-brand {
    font-family: "SF Mono", Menlo, Consolas, monospace;
    font-size: 15pt;
    font-weight: 700;
    color: #2f7fc0;
    letter-spacing: -0.01em;
}
.letterhead .lh-tag { font-size: 9.5pt; color: #444; margin-top: 0.15em; }
.letterhead .lh-contact { font-size: 8.5pt; color: #8a8a8a; margin-top: 0.2em; }
"""

# Pulled out of flow and rendered in @bottom-left on every page via element(footerLeft).
# A running element rather than a `content:` string so "(Printed …)" can be fainter.
FOOTER_CSS = """
.page-footer-runner {
    position: running(footerLeft);
    font-family: -apple-system, system-ui, sans-serif;
    font-size: 8pt;
    color: #888;
}
.page-footer-runner .dim { color: #bcbcbc; }
"""

TOC_CSS = """
nav.toc {
    margin: 1.5em 0 2em 0;
    padding: 0.8em 1em;
    background: #fafafa;
    border: 1px solid #e8e8e8;
    border-radius: 4px;
    page-break-inside: avoid;
}
nav.toc h2 {
    font-size: 11pt;
    border: none;
    margin: 0 0 0.4em 0;
    padding: 0;
    color: #555;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    counter-increment: none;  /* don't number the TOC's own "Contents" h2 */
}
nav.toc h2::before { content: none; }  /* and don't prepend "N. " to it */
nav.toc ol {
    margin: 0;
    padding-left: 0;
    list-style: none;
    font-size: 9.5pt;
    line-height: 1.6;
    column-count: 2;
    column-gap: 2.8em;
}
nav.toc li { margin: 0.1em 0; break-inside: avoid; }
nav.toc a { color: #333; }
"""


@lru_cache(maxsize=1)
def _highlight_css() -> str:
    """Pygments' style definitions, scoped to .highlight. `friendly` is a light theme that
    survives being printed — the dark themes turn into ink."""
    from pygments.formatters import HtmlFormatter

    return HtmlFormatter(style="friendly").get_style_defs(".highlight")


# --- Markdown ----------------------------------------------------------------------

FRONT_MATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)
HEADING_RE = re.compile(r"^(#{1,3})\s+(.+?)\s*$")
H2_TAG_RE = re.compile(r"<h2>(.*?)</h2>", re.DOTALL)
SLUG_NON_WORD_RE = re.compile(r"[^\w\s-]")
SLUG_WS_RE = re.compile(r"[\s_-]+")


def split_front_matter(text: str) -> tuple[dict, str]:
    """Split a leading YAML front-matter block (--- … ---) from the body. Recognised keys:
    `title`, `date`, `author` (the last two land in the PDF's own metadata), `style` (a
    style name), `brand` (a path, relative to the document), plus any BRAND key as an
    inline override. Everything else is carried but unused."""
    m = FRONT_MATTER_RE.match(text)
    if not m:
        return {}, text
    import yaml

    data = yaml.safe_load(m.group(1))
    return (data if isinstance(data, dict) else {}), text[m.end() :]


def _slugify(text: str) -> str:
    return SLUG_WS_RE.sub("-", SLUG_NON_WORD_RE.sub("", text.lower())).strip("-")


def title_and_toc(md_text: str) -> tuple[str, list[tuple[str, str]]]:
    """The first H1 as the document title, plus (slug, text) per H2 for the Contents
    block. Headings inside fenced code blocks don't count."""
    title = ""
    toc: list[tuple[str, str]] = []
    in_fence = False
    for line in md_text.splitlines():
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = HEADING_RE.match(line)
        if not m:
            continue
        level, text = len(m.group(1)), m.group(2).strip()
        if level == 1 and not title:
            title = text
        elif level == 2:
            toc.append((_slugify(text), text))
    return title, toc


def _highlight(code: str, lang: str, _attrs) -> str:
    """markdown-it highlight hook → Pygments. Returning "" lets markdown-it fall back to
    its own escaped <pre><code>, which is what an unknown or absent language should get:
    a code block that renders plainly beats one that raises."""
    if not lang:
        return ""
    from pygments import highlight
    from pygments.formatters import HtmlFormatter
    from pygments.lexers import get_lexer_by_name
    from pygments.util import ClassNotFound

    try:
        lexer = get_lexer_by_name(lang)
    except ClassNotFound:
        return ""
    body = highlight(code, lexer, HtmlFormatter(nowrap=True))
    return f'<pre class="highlight"><code>{body}</code></pre>'


def render_html(md_text: str) -> str:
    """Markdown → HTML with tables, strikethrough, footnotes and highlighted code, and an
    id on every <h2> so the Contents anchors land."""
    from markdown_it import MarkdownIt
    from mdit_py_plugins.footnote import footnote_plugin

    md = MarkdownIt(
        "commonmark",
        {"html": True, "linkify": True, "typographer": True, "highlight": _highlight},
    )
    md.enable("table").enable("strikethrough")
    md.use(footnote_plugin)
    html = md.render(md_text)
    # markdown-it-py has no heading-anchor plugin in its base, so a regex rewrite is
    # cheaper than another dep. unescape() first: rendered HTML carries "&amp;", and
    # slugifying that leaks the entity name into the anchor ("&" → "amp").
    return H2_TAG_RE.sub(
        lambda m: f'<h2 id="{_slugify(html_lib.unescape(m.group(1)))}">{m.group(1)}</h2>',
        html,
    )


def _render_toc(toc: list[tuple[str, str]]) -> str:
    """Contents entries with explicit "N. " prefixes matching the CSS counter on the
    body's h2s. The list's own bullets are hidden so the numbers don't double up."""
    items = "\n".join(
        f'<li><a href="#{slug}">{n}. {text}</a></li>' for n, (slug, text) in enumerate(toc, 1)
    )
    return f'<nav class="toc"><h2>Contents</h2><ol>\n{items}\n</ol></nav>'


def _letterhead_html(brand: dict) -> str:
    """Page-1 letterhead. The contact line composes from whatever identity fields exist."""
    contact = " · ".join(
        x
        for x in (
            brand.get("email"),
            brand.get("phone"),
            brand.get("site"),
            f"ABN {brand['abn']}" if brand.get("abn") else None,
        )
        if x
    )
    return (
        '<div class="letterhead">'
        f'<div class="lh-brand">{html_lib.escape(brand["name"])}</div>'
        f'<div class="lh-tag">{html_lib.escape(brand.get("tagline") or "")}</div>'
        f'<div class="lh-contact">{html_lib.escape(contact)}</div>'
        "</div>"
    )


def default_out(input_path: Path) -> Path:
    """Where the PDF lands when -o is omitted: a sibling `pdf/` dir if one exists, else
    `<docs>/pdf/` if the input is anywhere under a `docs/` tree, else beside the input.
    Generated artifacts stay out of the source folder without anyone passing a path."""
    pdf_name = input_path.with_suffix(".pdf").name
    sibling = input_path.resolve().parent / "pdf"
    if sibling.is_dir():
        return sibling / pdf_name
    parts = input_path.resolve().parts
    if "docs" in parts:
        docs_idx = len(parts) - 1 - parts[::-1].index("docs")
        return Path(*parts[: docs_idx + 1]) / "pdf" / pdf_name
    return input_path.with_suffix(".pdf")


# --- Render ------------------------------------------------------------------------

# Runs in the child: HTML on stdin, then base_url / output path / pdf_variant as argv.
# pdf_tags is always on — it is what makes the PDF navigable by a screen reader, costs
# only structure metadata, and WeasyPrint 69 specifically improved its validity.
_CHILD = (
    "import sys\n"
    "from weasyprint import HTML\n"
    "HTML(string=sys.stdin.read(), base_url=sys.argv[1]).write_pdf(\n"
    "    sys.argv[2], pdf_tags=True, pdf_variant=(sys.argv[3] or None))\n"
)


def native_libs_ok() -> bool:
    """Whether WeasyPrint's native deps look installed — for `ko doctor`. Only meaningful
    on macOS, where they arrive via Homebrew at a known path and their absence is the one
    setup step `ko pdf` can't do for you. Elsewhere the distro package puts them on the
    loader's own search path, so assume yes rather than guess at a filename."""
    if sys.platform != "darwin":
        return True
    return any(
        (Path(d) / "libpango-1.0.0.dylib").exists() for d in ("/opt/homebrew/lib", "/usr/local/lib")
    )


def _printed_on() -> str:
    """Today in `[cal] timezone` — read directly rather than importing gcal, which would
    drag the Google client libraries into every `ko pdf` run for one date."""
    tz = config.get("cal", "timezone") or "Australia/Sydney"
    return dt.datetime.now(ZoneInfo(tz)).date().isoformat()


def _child_env() -> dict[str, str]:
    """Environment for the render child. On macOS, WeasyPrint's native deps (pango, cairo,
    gobject) live in Homebrew's lib dir, which dyld doesn't search; dyld reads its search
    path once at process start, so this has to be set for a *new* process — hence a child
    rather than an in-process import."""
    env = dict(os.environ)
    if sys.platform != "darwin":
        return env
    lib_dirs = [p for p in ("/opt/homebrew/lib", "/usr/local/lib") if Path(p).is_dir()]
    if not lib_dirs:
        return env
    existing = env.get("DYLD_FALLBACK_LIBRARY_PATH", "")
    env["DYLD_FALLBACK_LIBRARY_PATH"] = ":".join([*lib_dirs, existing] if existing else lib_dirs)
    return env


def _write_pdf(html: str, out: Path, base_url: Path, variant: str = "") -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [sys.executable, "-c", _CHILD, str(base_url), str(out), variant],
        input=html.encode(),
        capture_output=True,
        env=_child_env(),
        check=False,  # returncode is inspected below to reshape the error
    )
    if proc.returncode:
        err = proc.stderr.decode(errors="replace").strip()
        if "external libraries" in err or "cannot load library" in err:
            raise RuntimeError(
                "WeasyPrint can't load pango/cairo — install them with "
                "`brew install pango` (macOS) or your distro's equivalent"
            )
        raise RuntimeError(f"PDF render failed: {err.splitlines()[-1] if err else 'no output'}")


def build_html(
    md_text: str,
    *,
    brand: dict,
    title: str,
    style: Style,
    date: str = "",
    author: str = "",
    extra_css: str = "",
) -> str:
    """The full HTML document. Separated from `_write_pdf` so the markup is testable
    without WeasyPrint (and its native libraries) present."""
    _, headings = title_and_toc(md_text)
    body = render_html(md_text)
    sheets = BASE_CSS + _highlight_css()
    runner = ""

    if style.toc and headings:
        sheets += NUMBERING_CSS + TOC_CSS
        # Right after the first </h1>, so the title stays on top.
        h1_end = body.find("</h1>")
        at = h1_end + len("</h1>") if h1_end != -1 else 0
        body = body[:at] + "\n" + _render_toc(headings) + body[at:]
    if style.letterhead:
        sheets += LETTERHEAD_CSS + f".letterhead {{ border-bottom-color: {brand['accent']}; }}\n"
        body = _letterhead_html(brand) + "\n" + body
    if style.footer:
        sheets += FOOTER_CSS
        # Has to sit in the body — position: running() pulls it into the margin box.
        runner = (
            f'<div class="page-footer-runner">{html_lib.escape(brand["name"])} · '
            f"{html_lib.escape(title)} "
            f'<span class="dim">(Printed {_printed_on()})</span></div>'
        )

    # date/author land in the PDF's own metadata (WeasyPrint reads these two meta tags),
    # so an export found on disk months later can still identify itself.
    head = ""
    if date:
        head += f'<meta name="dcterms.created" content="{html_lib.escape(date)}">'
    if author:
        head += f'<meta name="author" content="{html_lib.escape(author)}">'
    return (
        '<!DOCTYPE html>\n<html><head><meta charset="utf-8">'
        f"{head}<title>{html_lib.escape(title)}</title>"
        f"<style>{PAGE_CSS if style.footer else PAGE_CSS_NO_FOOTER}{sheets}"
        f"{style.css}{extra_css}</style></head><body>\n"
        f"{runner}\n{body}\n</body></html>"
    )


def render(
    input_path: Path,
    out: Path | None = None,
    *,
    style: str | None = None,
    toc: bool | None = None,
    letterhead: bool | None = None,
    css: tuple[Path, ...] = (),
    brand_file: Path | None = None,
    archival: bool = False,
) -> tuple[Path, dict]:
    """Render a markdown file to PDF. Returns (path written, the brand actually used) —
    the caller needs the brand to warn when it never resolved past the placeholder."""
    front, md_text = split_front_matter(input_path.read_text())
    resolved = resolve_style(style or front.get("style"))
    # Per-run overrides sit on top of whichever style was chosen.
    if toc is not None:
        resolved = replace(resolved, toc=toc)
    if letterhead is not None:
        resolved = replace(resolved, letterhead=letterhead)

    # A document may name its own brand.yml (relative to itself), then override single
    # keys inline — most specific wins.
    brand = load_brand(brand_file)
    if not brand_file and front.get("brand"):
        import yaml

        path = (input_path.parent / str(front["brand"])).expanduser()
        brand.update(_flatten_brand(yaml.safe_load(path.read_text()) or {}))
    brand.update({k: v for k, v in front.items() if k in BRAND and isinstance(v, str)})

    h1_title, _ = title_and_toc(md_text)
    html = build_html(
        md_text,
        brand=brand,
        title=str(front.get("title") or h1_title or input_path.stem),
        style=resolved,
        date=str(front.get("date") or ""),
        author=str(front.get("author") or brand.get("name") or ""),
        extra_css="\n".join(p.read_text() for p in css),
    )
    out = out or default_out(input_path)
    # PDF/A-3b: self-contained and archival. Not the default — it embeds colour profile
    # and font data most documents never need (~8% on a one-pager, more as fonts grow).
    _write_pdf(html, out, input_path.resolve().parent, "pdf/a-3b" if archival else "")
    return out, brand
