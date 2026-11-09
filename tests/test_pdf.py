"""Offline tests for ko.pdf. Everything up to the PDF bytes is pure — `build_html` exists
so the markup can be checked without WeasyPrint's native libraries; the one end-to-end
render skips when pango/cairo aren't installed."""

from __future__ import annotations

from pathlib import Path

import pytest

from ko import frontmatter, pdf

DOC = """---
title: Front matter wins
date: 2026-07-28
author: Ko
---
# Heading one

Body text.

```python
def hello(name):
    return name
```

## First section

## Second & Third

```
## not a heading
```
"""

NOTE = pdf.BUILTIN_STYLES["note"]
REPORT = pdf.BUILTIN_STYLES["report"]
BARE = pdf.BUILTIN_STYLES["bare"]


@pytest.fixture
def body():
    return frontmatter.split(DOC)[1]


@pytest.fixture(autouse=True)
def _no_machine_config(monkeypatch):
    """Neutralise the real `[pdf] brand`/`[pdf] style` and $KO_BRAND — otherwise these
    render with whatever the machine happens to configure, and the brand test fails
    outright where that path doesn't exist."""
    monkeypatch.delenv("KO_BRAND", raising=False)
    monkeypatch.delenv("KO_PDF_STYLE", raising=False)
    monkeypatch.setattr(pdf.config, "get", lambda s, k: None)


def test_front_matter_split():
    front, rest = frontmatter.split(DOC)
    assert front["title"] == "Front matter wins" and front["author"] == "Ko"
    assert rest.startswith("# Heading one")


def test_front_matter_absent_passes_through():
    front, rest = frontmatter.split("# Plain\n")
    assert front == {} and rest == "# Plain\n"


def test_title_and_toc_skips_fenced_headings(body):
    title, toc = pdf.title_and_toc(body)
    assert title == "Heading one"
    assert [text for _, text in toc] == ["First section", "Second & Third"]


def test_toc_anchors_match_the_rendered_heading_ids(body):
    # The bug this guards: rendered HTML carries "&amp;", and slugifying that leaks the
    # entity name into the id ("second-amp-third") while the TOC href says "second-third".
    _, toc = pdf.title_and_toc(body)
    html = pdf.render_html(body)
    for slug, _ in toc:
        assert f'id="{slug}"' in html


def test_fenced_code_is_syntax_highlighted(body):
    html = pdf.render_html(body)
    assert '<pre class="highlight">' in html and 'class="k"' in html  # pygments keyword span


def test_unknown_language_falls_back_to_a_plain_block():
    html = pdf.render_html("```notalanguage\nx = 1\n```\n")
    assert "<pre>" in html and "highlight" not in html


# --- styles ---


def test_default_style_is_the_quick_one():
    style = pdf.resolve_style(None)
    assert style.name == "note"
    assert style.footer and not style.toc and not style.letterhead


def test_note_style_has_a_footer_but_no_chrome(body):
    html = pdf.build_html(body, brand=dict(pdf.BRAND), title="T", style=NOTE)
    assert "page-footer-runner" in html and "counter(page)" in html
    for chrome in ('nav class="toc"', "letterhead", "counter(section)"):
        assert chrome not in html


def test_report_style_adds_letterhead_contents_and_numbering(body):
    html = pdf.build_html(body, brand=dict(pdf.BRAND), title="T", style=REPORT)
    assert 'nav class="toc"' in html and "letterhead" in html
    assert "counter(section)" in html and "page-footer-runner" in html


def test_bare_style_drops_every_bit_of_chrome(body):
    html = pdf.build_html(body, brand=dict(pdf.BRAND), title="T", style=BARE)
    for chrome in ('nav class="toc"', "letterhead", "page-footer-runner", "counter(section)"):
        assert chrome not in html
    assert "@bottom-right" not in html  # no footer boxes either


def test_numbering_only_ships_with_the_contents_block(body):
    # They're tied on purpose: the TOC prints "N." prefixes that must match the body's
    # CSS counter, so a document can't have one without the other and stay consistent.
    for style in (NOTE, REPORT, BARE):
        html = pdf.build_html(body, brand=dict(pdf.BRAND), title="T", style=style)
        assert ('nav class="toc"' in html) == ("counter(section)" in html)


def test_metadata_lands_in_the_head(body):
    html = pdf.build_html(
        body, brand=dict(pdf.BRAND), title="T", style=NOTE, date="2026-07-28", author="Ko"
    )
    assert 'content="2026-07-28"' in html and 'name="author" content="Ko"' in html


def test_user_style_css_lands_last_so_it_wins(tmp_path, monkeypatch):
    styles = tmp_path / "pdf"
    styles.mkdir()
    (styles / "red.css").write_text("body{color:red}")
    monkeypatch.setattr(pdf, "user_style_dir", lambda: styles)
    html = pdf.build_html("# T\n", brand=dict(pdf.BRAND), title="T", style=pdf.resolve_style("red"))
    assert html.index("body{color:red}") > html.index("@page")


def test_user_style_layers_its_css_on_the_note_chrome(tmp_path, monkeypatch):
    styles = tmp_path / "pdf"
    styles.mkdir()
    (styles / "serif.css").write_text("body{font-family:Georgia}")
    monkeypatch.setattr(pdf, "user_style_dir", lambda: styles)
    style = pdf.resolve_style("serif")
    assert style.name == "serif" and style.footer and not style.letterhead
    assert style.css == "body{font-family:Georgia}"
    assert "serif" in pdf.style_names()


def test_unknown_style_raises_and_lists_the_real_ones():
    with pytest.raises(ValueError, match="unknown style 'nope'"):
        pdf.resolve_style("nope")


def test_style_from_config_when_no_flag(monkeypatch):
    monkeypatch.setattr(pdf.config, "get", lambda s, k: "report" if (s, k) == ("pdf", "style") else None)
    assert pdf.resolve_style(None).name == "report"


# --- brand ---


def test_flatten_brand_maps_brand_yml_shape():
    flat = pdf._flatten_brand(
        {
            "meta": {
                "name": "khalido.dev",
                "tagline": "tag",
                "email": "a@b.c",
                "links": {"home": "https://khalido.dev/"},
            },
            "color": {"palette": {"accent": "#61afef"}, "primary": "accent"},
        }
    )
    assert flat["name"] == "khalido.dev" and flat["accent"] == "#61afef"
    assert flat["site"] == "khalido.dev"  # scheme and trailing slash stripped
    assert "phone" not in flat  # absent keys don't overwrite defaults with None


def test_placeholder_detection():
    assert pdf.is_placeholder(dict(pdf.BRAND))
    assert not pdf.is_placeholder({**pdf.BRAND, "name": "khalido.dev"})


def test_only_brand_showing_styles_are_worth_warning_about():
    assert NOTE.shows_brand and REPORT.shows_brand and not BARE.shows_brand


def test_brand_path_prefers_env_over_config(monkeypatch, tmp_path):
    monkeypatch.setenv("KO_BRAND", str(tmp_path / "b.yml"))
    assert pdf.brand_path() == tmp_path / "b.yml"
    monkeypatch.delenv("KO_BRAND")
    assert pdf.brand_path() is None


def test_a_bare_brand_name_is_an_identity_in_the_brands_dir(monkeypatch, tmp_path):
    """`--brand syntech` picks which identity to send as, without a path in the command."""
    monkeypatch.setenv("KO_CONFIG_DIR", str(tmp_path))
    assert pdf.brand_path("syntech") == tmp_path / "brands" / "syntech.yml"


def test_a_brand_ref_that_looks_like_a_path_stays_one(monkeypatch, tmp_path):
    """The escape hatch: a brand.yml living outside the config dir is still addressable."""
    monkeypatch.setenv("KO_CONFIG_DIR", str(tmp_path))
    assert pdf.brand_path(tmp_path / "one-off.yml") == tmp_path / "one-off.yml"
    assert pdf.brand_path("~/b.yml") == Path.home() / "b.yml"


# --- output paths + environment ---


def test_default_out_prefers_a_sibling_pdf_dir(tmp_path):
    (tmp_path / "pdf").mkdir()
    assert pdf.default_out(tmp_path / "doc.md") == tmp_path / "pdf" / "doc.pdf"


def test_default_out_falls_back_to_beside_the_input(tmp_path):
    assert pdf.default_out(tmp_path / "doc.md") == tmp_path / "doc.pdf"


def test_default_out_does_not_climb_to_an_enclosing_docs_dir(tmp_path):
    # The ops script wrote docs/lab/discovery/x.md to docs/pdf/ — two levels up, into a
    # directory it created. Surprising, and unused: every real call passes -o.
    deep = tmp_path / "docs" / "lab" / "discovery"
    deep.mkdir(parents=True)
    assert pdf.default_out(deep / "state.md") == deep / "state.pdf"


def test_child_env_only_touches_dyld_on_macos(monkeypatch):
    monkeypatch.setattr(pdf.sys, "platform", "linux")
    assert "DYLD_FALLBACK_LIBRARY_PATH" not in pdf._child_env()


def test_render_end_to_end(tmp_path):
    src = tmp_path / "doc.md"
    src.write_text(DOC)
    try:
        out, brand = pdf.render(src)
    except RuntimeError as e:
        pytest.skip(f"WeasyPrint native libs unavailable: {e}")
    assert out == tmp_path / "doc.pdf"
    assert out.read_bytes().startswith(b"%PDF-")
    assert pdf.is_placeholder(brand)  # nothing configured in this test env


def test_render_honours_front_matter_style(tmp_path):
    src = tmp_path / "doc.md"
    src.write_text("---\nstyle: bare\n---\n# T\n\ntext\n")
    try:
        out, _ = pdf.render(src)
    except RuntimeError as e:
        pytest.skip(f"WeasyPrint native libs unavailable: {e}")
    assert out.exists()


SAMPLE = Path(__file__).parent.parent / "docs" / "pdf-sample.md"


@pytest.mark.parametrize("style", list(pdf.BUILTIN_STYLES))
def test_sample_document_renders_in_every_style(style, tmp_path):
    """The sample exercises every element the renderer claims to handle, so a style that
    breaks on one of them (a table, a highlighted block, a forced page break) fails here
    rather than in a document someone is about to send."""
    try:
        out, _ = pdf.render(SAMPLE, tmp_path / f"{style}.pdf", style=style)
    except RuntimeError as e:
        pytest.skip(f"WeasyPrint native libs unavailable: {e}")
    assert out.read_bytes().startswith(b"%PDF-")
    assert out.stat().st_size > 10_000  # a blank page is ~2 KB; this one has content


def test_unknown_brand_name_errors_listing_available(monkeypatch, tmp_path):
    """The --style contract's twin: a typo'd brand must be distinguishable from 'no brand'."""
    monkeypatch.setenv("KO_CONFIG_DIR", str(tmp_path))
    (tmp_path / "brands").mkdir()
    (tmp_path / "brands" / "syntech.yml").write_text("name: Syntech")
    with pytest.raises(ValueError, match="syntech"):
        pdf.load_brand("syntch")


def test_empty_brand_ref_means_unbranded_not_config_fallback(monkeypatch, tmp_path):
    monkeypatch.setenv("KO_BRAND", str(tmp_path / "b.yml"))
    assert pdf.brand_path("") is None  # --brand "" = render unbranded, ignore the config chain
