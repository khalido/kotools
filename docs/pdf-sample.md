---
title: PDF sample — every element the renderer handles
date: 2026-07-28
author: ko pdf
---
# PDF sample

The fixture behind `tests/test_pdf.py`'s style smoke test. Every element below is
something `ko pdf` claims to render, so a regression shows up as a visibly wrong page
rather than a passing test. Render it in each style to compare:

```sh
for s in $(ko pdf --help | grep -o 'note\|report\|bare' | sort -u); do
  ko pdf docs/pdf-sample.md --style "$s" -o "/tmp/sample-$s.pdf"
done
```

## Text and inline marks

Body copy at 10pt with a comfortable measure. **Bold**, *italic*, ***both***,
~~struck through~~, `inline code`, and a [link out to the WeasyPrint docs][wp] which
should wrap rather than run off the page: <https://doc.courtbouillon.org/weasyprint/stable/>

A second paragraph, to check spacing between them. Punctuation is left alone on purpose:
"quotes" stay straight and `--flags` stay as two hyphens, because these documents are full
of command-line flags in prose. Bare URLs are not auto-linked either — use `<angles>` or a
[labelled link](https://example.com).

> A blockquote, for the pull-out that every status doc ends up needing.
> It runs to a second line to show the left rule continuing.

[wp]: https://doc.courtbouillon.org/weasyprint/stable/

## Lists

- Bullet, first level
- Another bullet
  - Nested, second level
  - With a sibling
- Back to the first level

1. Ordered item
2. Second item
3. Third item, long enough to wrap onto a second line so the hanging indent is visible
   against the left margin

## Code

Fenced blocks are highlighted by Pygments when the language is known:

```python
def render(path: Path, style: Style) -> Path:
    """Markdown in, PDF out."""
    html = build_html(path.read_text(), style=style)
    return write_pdf(html, path.with_suffix(".pdf"))
```

```sh
ko pdf notes.md --style report -o handover.pdf
```

An unknown language falls back to a plain block rather than failing:

```notalanguage
these <characters> & "entities" must survive escaping
```

## Tables

| Style | Furniture | Use it for |
|---|---|---|
| `note` | Page footer | A quick PDF — the default |
| `report` | Letterhead, Contents, numbered sections | Something you hand to someone |
| `bare` | Nothing | A canvas for a named style |

## Footnotes and page breaks

Footnotes collect at the end of the document[^1], with the reference marks linked[^2].

[^1]: The footnote block is set smaller and dimmer than body copy.
[^2]: Backref arrows are hidden — they mean nothing on paper.

<div class="page-break"></div>

## After a forced break

This heading follows `<div class="page-break"></div>`, so it must start a new page in
every style. If it doesn't, the page-break rule has regressed.

Under `report` this section is numbered and listed in the Contents; under `note` and
`bare` it is neither.
