# kotools-test — Markdown ↔ Google Docs reference

What survives the Markdown ↔ Google Docs round-trip, tested by pushing this file and comparing the export. `ko gdocs push` converts Markdown to a Doc **server-side** (Drive `files.create` with `text/markdown`); `ko gdocs get` pulls it back. The Markdown in git stays the source of truth. (`ko gd` is a shortcut for `ko gdocs`.)

**If you're reading this in Google Docs:** it's generated from `docs/gdocs-markdown.md` in the [kotools](https://github.com/khalido/kotools) repo. Edit the Markdown there, not this Doc, then re-push. The table shading below was applied with `ko gdocs shade-table` (Markdown can't set cell colours). See the References at the bottom.

**Authoring:** soft-wrap. One line per paragraph, a blank line between paragraphs. The Doc handles display wrapping, and the `push`/`get` diff stays clean (hard-wrapped source reflows on export, so every paragraph looks changed).

Re-test with:

`ko gdocs push docs/gdocs-markdown.md <doc-id>` — re-imports into the same Doc, keeping its URL and sharing (omit the id to create a fresh Doc). It shows a diff and asks to confirm; `--force` skips.

`ko gdocs get <doc-id>` — compare the result against this file.

## Basic Markdown — all of this converts

Headings map to Google Docs' **Heading 1 / 2 / 3** styles (this file: `#` title, `##` sections, `###` sub-parts).

### Inline styles

**bold**, *italic*, ***bold italic***, `inline code`, [a link](https://khalido.dev), and ~~strikethrough~~ — all convert to native character formatting.

### Lists

**Use `*` for lists, not `-`.** The Docs editor (*Tools → Preferences → Automatically detect Markdown*) only turns `*` into a list; `-` stays literal. `ko gdocs push` accepts both, but only `*` reliably converts. Indent sub-items to nest: **2 spaces** under a bullet, **3 spaces** under a numbered item (to clear the `N. `). Both nest correctly.

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

* [ ] An unchecked task
* [x] A checked task

### Horizontal rule

A `---` becomes a real horizontal line and round-trips:

---

## Tables — convert to native Docs tables

Google's Markdown docs don't list tables, but a pipe table converts to a **native Google Docs table**. Survivors: the table structure, **bold inside a cell**, and **column alignment** (`--:` right-align comes back):

| Item            | Qty | Unit price | Line total |
| --------------- | --: | ---------: | ---------: |
| Ultrasound unit |   2 |    $45,000 |    $90,000 |
| Service plan    |   1 |     $8,000 |     $8,000 |
| **Total**       |     |            |   $101,600 |

Cell **background shading** isn't Markdown. After a push, run `ko gdocs shade-table <doc>`: it shades the header row by default; add `--cols -1` for a totals column, or `--all-tables` for every table's header. The header and Total rows here were shaded that way. A push clears shading, so re-run `shade-table` after every re-push.

## What does NOT survive

Avoid these in a Doc-bound file. Conversion drops or mangles them:

* **Fenced code blocks** — ` ```lang ` blocks do **not** become a monospaced code block. They flatten to plain paragraphs (a blank line between each source line) and punctuation gets backslash-escaped (`->` → `\->`). Specimen below.
* **Blockquotes** — the `>` marker is lost; the text becomes a normal paragraph.
* **Images** — a pushed image embeds as base64 and won't export cleanly. Fine to skip for text.

Code-block specimen (it will *not* render as a code block):

```python
def margin(cost: float, price: float) -> float:
    """Gross margin as a fraction of price."""
    return (price - cost) / price
```

Blockquote specimen (comes back as plain text):

> This blockquote comes back as a normal paragraph, not a quote.

## Tabs — one doc, many tabs

A Google Doc can hold several **tabs** (the left-rail list); a per-client shared doc keeps one tab per client or topic. `get` and `push` work on the whole doc by default. `--tab` scopes `get`, `append` and `replace` to one tab.

```
ko gdocs tabs <doc>                       # list: index, title, tabId (TSV; sub-tabs indented). --json too.
ko gdocs add-tab <doc> "Acme"             # create a tab; prints its tabId. Refuses a duplicate title.
ko gdocs get <doc> --tab Acme             # that tab's text (title or tabId, case-insensitive exact)
ko gdocs append <doc> "note" --tab Acme   # append to the end of one tab
ko gdocs replace <doc> "Q2" "Q3" --tab Acme   # find/replace scoped to one tab
```

Sharp edges:

* **`--tab` matches** a tab title (case-insensitive, exact) or a raw `tabId` (e.g. `t.0`). A miss lists the available tabs and exits 1.
* **`get --tab`** can't use Drive's Markdown export (whole-doc only), so it walks the tab's Docs-API body. Headings become `#`, bullets `-`, and tables are noted as `[table omitted]`. Use `get` without `--tab` for the lossless, table-aware export.
* **`add-tab`** uses the Docs API's `addDocumentTab` request (verified against the live API, 2026-07). It refuses a duplicate title with a clean error. It creates a top-level tab at the end; fill it with `append --tab`.
* **Multi-tab docs refuse a bare `append` or `replace`.** A bare `append` would land in the first tab only. A bare `replace` would rewrite every client's tab. Pass `--tab`, or `--all-tabs` for `replace`.
* **`replace --tab`** scopes the replacement with the Docs API's `tabsCriteria`. It first lists open comments and **refuses** if the replaced text is (or contains) a comment's quoted anchor, since the edit would orphan the thread. `--force` proceeds anyway. The comment list isn't tab-scoped by the API, so a same-worded quote in another tab can over-warn. `--force` is the escape hatch.
* **`push` can't target a tab.** Drive's Markdown import is whole-doc; the API has no per-tab import. Edit within a tab with `append --tab` / `replace --tab`.

## References

* [Use Markdown in Google Docs](https://support.google.com/docs/answer/12014036?hl=en#zippy=%2Cconvert-markdown-to-google-docs-content-on-paste) — the **editor-side** Markdown behaviour (where only `*` makes a list).
* [Google Docs API — REST reference](https://developers.google.com/workspace/docs/api/reference/rest) — the SDK `ko` drives under the hood: `replace` and `shade-table` use the Docs API; `push`/`get` use the Drive API's Markdown conversion.
* [Tables how-to](https://developers.google.com/workspace/docs/api/how-tos/tables) — table cell/row styling (the basis for `shade-table`).
* [google-api-python-client](https://github.com/googleapis/google-api-python-client) — the Python SDK the tool wraps.
* Google's own Markdown list is **incomplete** (tables aren't mentioned but *do* convert). For the `ko` workflow, trust this tested file over the docs.

Canonical test Doc, reused as Google Docs adds Markdown features: **kotools-test**, id `1VoxeRR_N5toaYIIxcZZxWXrjnE4q0BNvDR6V0ayh6VQ`. Re-push with `ko gdocs push docs/gdocs-markdown.md <that-id>` to keep the id stable, then re-apply shading.
