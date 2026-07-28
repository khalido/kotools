"""Markdown → print-ready PDF via WeasyPrint. Local, no models, no service.

Ported from ops/scripts/md_to_pdf.py, which was written for client handover docs and is
the pattern here: A4, generous margins, numbered sections, an auto Contents block, a
letterhead on page 1 and a `brand · title (Printed date)  N / M` footer on every page.

Two things changed in the port, both because the ops script had the same footgun twice:
identity had to be passed as `--brand path` on *every* invocation, and forgetting it
silently shipped a PDF whose footer read "yourbrand.dev". Here the brand resolves through
ko's normal config chain (`--brand` > front-matter > KO_BRAND > `[pdf] brand`), so it is
set once, and a still-unresolved brand warns on stderr rather than printing quietly into
the document.

Left behind deliberately: the resume stylesheet. It is one document's bespoke layout, not
a generic renderer's job — `--bare --css my.css` covers that case with the sheet living
next to the CV.

WeasyPrint needs pango/cairo/gobject, which on macOS come from Homebrew in a directory
dyld does not search. Since dyld reads its search path at process start, no in-process
fix exists (ctypes preloading does not satisfy cffi's dlopen-by-leafname), so the HTML is
built here and handed to a short-lived child interpreter with the path set — see
`_write_pdf`. Zero setup for the caller, which matters because agents call this too.
"""

from __future__ import annotations

import datetime as dt
import html as html_lib
import os
import re
import subprocess
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

from ko import config

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
    configured one. Raises FileNotFoundError if a path was given but doesn't exist — a
    silently-ignored brand file is how the placeholder used to ship."""
    import yaml

    brand = dict(BRAND)
    src = path or brand_path()
    if src:
        brand.update(_flatten_brand(yaml.safe_load(src.read_text()) or {}))
    return brand


def is_placeholder(brand: dict) -> bool:
    """True when no brand.yml resolved, so the letterhead/footer would print the generic
    default. Worth a stderr note: the output looks finished but isn't."""
    return brand.get("name") == BRAND["name"]


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

# --bare: same page geometry, no footer boxes (nothing populates them).
BARE_PAGE_CSS = "@page { size: A4; margin: 2cm; }\n"

# Typography only — everything a plain document needs, and all `--bare` keeps.
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

# Document chrome: numbered sections, letterhead, Contents, footer runner. Dropped by
# --bare so a custom sheet gets a clean canvas.
DOC_CSS = """
body { counter-reset: section; }
h2 { counter-increment: section; }
h2::before { content: counter(section) ". "; color: #888; font-weight: 500; }

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

/* Pulled out of flow and rendered in @bottom-left on every page via element(footerLeft).
   A running element rather than a `content:` string so the "(Printed …)" half can be
   fainter than the title. */
.page-footer-runner {
    position: running(footerLeft);
    font-family: -apple-system, system-ui, sans-serif;
    font-size: 8pt;
    color: #888;
}
.page-footer-runner .dim { color: #bcbcbc; }

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


# --- Markdown ----------------------------------------------------------------------

FRONT_MATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)
HEADING_RE = re.compile(r"^(#{1,3})\s+(.+?)\s*$")
H2_TAG_RE = re.compile(r"<h2>(.*?)</h2>", re.DOTALL)
SLUG_NON_WORD_RE = re.compile(r"[^\w\s-]")
SLUG_WS_RE = re.compile(r"[\s_-]+")


def split_front_matter(text: str) -> tuple[dict, str]:
    """Split a leading YAML front-matter block (--- … ---) from the body. Recognised keys:
    `title`, `date`, `author` (the last two land in the PDF's own metadata), `brand` (a
    path, relative to the document), `style: bare`, plus any BRAND key as an inline
    override. Everything else is carried but unused."""
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


def render_html(md_text: str) -> str:
    """Markdown → HTML with tables, strikethrough and footnotes, and an id on every <h2>
    so the Contents anchors land."""
    from markdown_it import MarkdownIt
    from mdit_py_plugins.footnote import footnote_plugin

    md = MarkdownIt("commonmark", {"html": True, "linkify": True, "typographer": True})
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

# Runs in the child: HTML on stdin, base_url and output path as argv.
_CHILD = (
    "import sys\n"
    "from weasyprint import HTML\n"
    "HTML(string=sys.stdin.read(), base_url=sys.argv[1]).write_pdf(sys.argv[2])\n"
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
    dirs = [p for p in ("/opt/homebrew/lib", "/usr/local/lib") if Path(p).is_dir()]
    if not dirs:
        return env
    existing = env.get("DYLD_FALLBACK_LIBRARY_PATH", "")
    env["DYLD_FALLBACK_LIBRARY_PATH"] = ":".join([*dirs, existing] if existing else dirs)
    return env


def _write_pdf(html: str, out: Path, base_url: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [sys.executable, "-c", _CHILD, str(base_url), str(out)],
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
    date: str = "",
    author: str = "",
    toc: bool = True,
    letterhead: bool = True,
    bare: bool = False,
    extra_css: str = "",
) -> str:
    """The full HTML document. Separated from `_write_pdf` so the markup is testable
    without WeasyPrint (and its native libraries) present."""
    _, headings = title_and_toc(md_text)
    body = render_html(md_text)
    if bare:
        page_css, sheets, runner = BARE_PAGE_CSS, BASE_CSS, ""
    else:
        page_css, sheets = PAGE_CSS, BASE_CSS + DOC_CSS
        if toc and headings:
            # Right after the first </h1>, so the title stays on top.
            h1_end = body.find("</h1>")
            at = h1_end + len("</h1>") if h1_end != -1 else 0
            body = body[:at] + "\n" + _render_toc(headings) + body[at:]
        if letterhead:
            body = _letterhead_html(brand) + "\n" + body
        # Has to sit in the body — position: running() pulls it into the margin box.
        runner = (
            f'<div class="page-footer-runner">{html_lib.escape(brand["name"])} · '
            f'{html_lib.escape(title)} '
            f'<span class="dim">(Printed {_printed_on()})</span></div>'
        )
        sheets += f".letterhead {{ border-bottom-color: {brand['accent']}; }}\n"

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
        f"<style>{page_css}{sheets}{extra_css}</style></head><body>\n"
        f"{runner}\n{body}\n</body></html>"
    )


def render(
    input_path: Path,
    out: Path | None = None,
    *,
    toc: bool = True,
    letterhead: bool = True,
    bare: bool = False,
    css: tuple[Path, ...] = (),
    brand_file: Path | None = None,
) -> Path:
    """Render a markdown file to PDF and return the path written."""
    front, md_text = split_front_matter(input_path.read_text())
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
        date=str(front.get("date") or ""),
        author=str(front.get("author") or brand.get("name") or ""),
        toc=toc,
        letterhead=letterhead,
        bare=bare or front.get("style") == "bare",
        extra_css="\n".join(p.read_text() for p in css),
    )
    out = out or default_out(input_path)
    _write_pdf(html, out, input_path.resolve().parent)
    return out
