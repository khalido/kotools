"""Offline tests for ko.pdf. Everything up to the PDF bytes is pure — `build_html` exists
so the markup can be checked without WeasyPrint's native libraries; the one end-to-end
render skips when pango/cairo aren't installed."""

from __future__ import annotations

import pytest

from ko import pdf

DOC = """---
title: Front matter wins
date: 2026-07-28
author: Ko
---
# Heading one

Body text.

## First section

## Second & Third

```
## not a heading
```
"""


def test_front_matter_split():
    front, body = pdf.split_front_matter(DOC)
    assert front["title"] == "Front matter wins" and front["author"] == "Ko"
    assert body.startswith("# Heading one")


def test_front_matter_absent_passes_through():
    front, body = pdf.split_front_matter("# Plain\n")
    assert front == {} and body == "# Plain\n"


def test_title_and_toc_skips_fenced_headings():
    _, body = pdf.split_front_matter(DOC)
    title, toc = pdf.title_and_toc(body)
    assert title == "Heading one"
    assert [text for _, text in toc] == ["First section", "Second & Third"]


def test_toc_anchors_match_the_rendered_heading_ids():
    # The bug this guards: rendered HTML carries "&amp;", and slugifying that leaks the
    # entity name into the id ("second-amp-third") while the TOC href says "second-third".
    _, body = pdf.split_front_matter(DOC)
    _, toc = pdf.title_and_toc(body)
    html = pdf.render_html(body)
    for slug, _ in toc:
        assert f'id="{slug}"' in html


def test_build_html_doc_chrome_present():
    _, body = pdf.split_front_matter(DOC)
    html = pdf.build_html(body, brand=dict(pdf.BRAND), title="T", date="2026-07-28", author="Ko")
    assert "nav class=\"toc\"" in html and "letterhead" in html
    assert "page-footer-runner" in html and "counter(page)" in html
    assert 'content="2026-07-28"' in html and 'name="author" content="Ko"' in html


def test_build_html_bare_drops_every_bit_of_chrome():
    _, body = pdf.split_front_matter(DOC)
    html = pdf.build_html(body, brand=dict(pdf.BRAND), title="T", bare=True)
    for chrome in ("nav class=\"toc\"", "letterhead", "page-footer-runner", "counter(section)"):
        assert chrome not in html


def test_extra_css_lands_last_so_it_wins():
    html = pdf.build_html("# T\n", brand=dict(pdf.BRAND), title="T", extra_css="body{color:red}")
    assert html.index("body{color:red}") > html.index("@page")


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


def test_brand_path_prefers_env_over_config(monkeypatch, tmp_path):
    monkeypatch.setenv("KO_BRAND", str(tmp_path / "b.yml"))
    assert pdf.brand_path() == tmp_path / "b.yml"
    monkeypatch.delenv("KO_BRAND")
    monkeypatch.setattr(pdf.config, "get", lambda s, k: None)
    assert pdf.brand_path() is None


def test_default_out_prefers_a_sibling_pdf_dir(tmp_path):
    (tmp_path / "pdf").mkdir()
    assert pdf.default_out(tmp_path / "doc.md") == tmp_path / "pdf" / "doc.pdf"


def test_default_out_uses_the_docs_tree(tmp_path):
    deep = tmp_path / "docs" / "lab" / "discovery"
    deep.mkdir(parents=True)
    assert pdf.default_out(deep / "state.md") == tmp_path / "docs" / "pdf" / "state.pdf"


def test_default_out_falls_back_to_beside_the_input(tmp_path):
    assert pdf.default_out(tmp_path / "doc.md") == tmp_path / "doc.pdf"


def test_child_env_only_touches_dyld_on_macos(monkeypatch):
    monkeypatch.setattr(pdf.sys, "platform", "linux")
    assert "DYLD_FALLBACK_LIBRARY_PATH" not in pdf._child_env()


def test_render_end_to_end(tmp_path, monkeypatch):
    # Neutralise the real `[pdf] brand` / $KO_BRAND — otherwise this test renders with
    # whatever brand.yml the machine happens to configure, and fails outright where that
    # path doesn't exist.
    monkeypatch.delenv("KO_BRAND", raising=False)
    monkeypatch.setattr(pdf.config, "get", lambda s, k: None)
    src = tmp_path / "doc.md"
    src.write_text(DOC)
    try:
        out = pdf.render(src, brand_file=None)
    except RuntimeError as e:
        pytest.skip(f"WeasyPrint native libs unavailable: {e}")
    assert out == tmp_path / "doc.pdf"
    assert out.read_bytes().startswith(b"%PDF-")
