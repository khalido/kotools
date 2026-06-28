# kotools-test — Markdown ↔ Google Docs reference

`ko gdocs push` converts this Markdown into a Google Doc **server-side** (Drive `files.create` with
`text/markdown`); `ko gdocs export` pulls it back as Markdown. This file is the living reference for
**what actually survives that round-trip** — push it, eyeball the Doc, export it, compare. The
Markdown in git stays the source of truth.

References:
* Google — [Use Markdown in Google Docs](https://support.google.com/docs/answer/12014036?hl=en#zippy=%2Cconvert-markdown-to-google-docs-content-on-paste)
* Google's own lists are **incomplete** (tables aren't mentioned but *do* convert), so trust this
  empirically-tested file over the docs for the `ko` workflow.

Regenerate / re-test with:

```
ko gdocs push docs/gdocs-markdown.md --title kotools-test
ko gdocs export <doc-id>        # compare against this file
```

Canonical test Doc — reused for ongoing checks as Google Docs adds Markdown features:
**kotools-test**, id `17ZstQgHvwCSglcTUKuX28mBiQ672nPeKIyD370VIE5g`.

## Basic Markdown — all of this converts

Everything you'd reach for in a proposal. Headings map to Google Docs' **Heading 1 / 2 / 3** styles
(this file: `#` title, `##` sections, `###` sub-parts).

### Inline styles

**bold**, *italic*, ***bold italic***, `inline code`, [a link](https://khalido.dev), and
~~strikethrough~~ — all convert to native character formatting.

### Lists

Two markdown paths, one quirk: Google Docs' **editor** setting (*Tools → Preferences →
Automatically detect Markdown*) only turns `*` into a list — a `-` stays literal. `ko gdocs push`
(the Drive import path) accepts both, but **`*` is the safe common denominator**, so prefer it.
Indent sub-items to nest: **2 spaces** under a bullet, **3 spaces** under a numbered item (to clear
the `N. `). Both nest correctly — verified.

Bulleted, with one level of sub-bullets:

* Bulleted item
* With a **bold** word and an *italic* one
  * Sub-bullet, one level in
  * Another sub-bullet
* Back at the top level

Numbered, with one level of sub-bullets:

1. First step
   * Sub-point under step one
   * Another sub-point
2. Second step
3. Third step

### Task lists

Checkbox syntax converts to a real Google Docs checklist (round-trips):

- [ ] An unchecked task
- [x] A checked task

### Horizontal rule

A `---` becomes a real horizontal line and round-trips:

---

## Tables — convert to native Docs tables

Not listed in Google's Markdown docs, but a pipe table converts to a **native Google Docs table**.
Confirmed survivors: the table structure, **bold inside a cell**, and **column alignment** (`--:`
right-align comes back):

| Item            | Qty | Unit price | Line total |
| --------------- | --: | ---------: | ---------: |
| Ultrasound unit |   2 |    $45,000 |    $90,000 |
| Service plan    |   1 |     $8,000 |     $8,000 |
| **Total**       |     |            |   $101,600 |

## What does NOT survive

Avoid these in a Doc-bound proposal — they're dropped or mangled on conversion:

* **Fenced code blocks** — ` ```lang ` blocks do **not** become a monospaced code block. They
  flatten to plain paragraphs (one blank line inserted between each source line) and punctuation
  gets backslash-escaped (`->` → `\->`). If you need code in a Doc, expect plain, slightly mangled
  text. (Demonstrated below.)
* **Blockquotes** — the `>` marker is lost; the text becomes a normal paragraph.
* **Images** — a pushed image embeds as base64 and won't export cleanly. Fine to skip for text.

Code-block specimen (check how this renders — it will *not* be a code block):

```python
def margin(cost: float, price: float) -> float:
    """Gross margin as a fraction of price."""
    return (price - cost) / price
```

Blockquote specimen (comes back as plain text):

> This blockquote comes back as a normal paragraph, not a quote.
