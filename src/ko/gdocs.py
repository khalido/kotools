"""Google Docs reader/writer — minimal primitives over the Docs v1 API.

Read a tab as plain text or light markdown; append text; find/replace — whole-doc or
scoped to one tab. Addresses docs by ID or URL.
Reads use the read-only scope, writes the read+write one (one `ko gsheets auth` covers both).

Deliberately minimal: full structural editing (tables, styles, inline formatting, images) is
the web editor's job. This is the "read it / append a note / swap some text" surface.

For Markdown round-tripping (push a `.md` to a formatted Doc, export a Doc to real Markdown) and
reading review comments, see `gdrive.py` — those are Drive-API features the Docs API can't do.
"""

from __future__ import annotations

from dataclasses import dataclass

from googleapiclient.errors import HttpError

from ko.google_auth import GoogleError, get_docs_service, id_from_url, raise_for_status


@dataclass
class DocInfo:
    id: str
    title: str


@dataclass
class TabInfo:
    """One tab in a Doc. `nesting_level` 0 = a top-level tab, 1 = a sub-tab, etc."""

    index: int
    title: str
    tab_id: str
    nesting_level: int


class DocsError(GoogleError):
    pass


class DocsNotFound(DocsError):
    pass


class DocsPermissionDenied(DocsError):
    pass


def _handle(e: HttpError, context: str) -> None:
    raise_for_status(
        e,
        context,
        not_found=DocsNotFound,
        permission=DocsPermissionDenied,
        hint="Is the doc accessible to the signed-in account, and for a write did you `ko gdocs auth`?",
    )


def doc_id(value: str) -> str:
    """Accept a Google Docs URL or a bare document ID; return the ID."""
    return id_from_url(value, "document")


def _para_text(para: dict, markdown: bool) -> str:
    """One paragraph's text. With markdown=True, map heading styles to `#` and bullets to `- `."""
    text = "".join(e.get("textRun", {}).get("content", "") for e in para.get("elements", []))
    if not markdown:
        return text
    style = para.get("paragraphStyle", {}).get("namedStyleType", "")
    if style.startswith("HEADING_"):
        try:
            level = int(style.split("_")[1])
        except ValueError:
            level = 1
        return "#" * level + " " + text.lstrip()
    if "bullet" in para:  # presence marks a list item; the dict can be empty
        return "- " + text.lstrip()
    return text


def get_info(doc: str) -> DocInfo:
    """The doc's title + id."""
    did = doc_id(doc)
    try:
        d = get_docs_service().documents().get(documentId=did, fields="title,documentId").execute(num_retries=3)
    except HttpError as e:
        _handle(e, did)
    return DocInfo(id=d["documentId"], title=d.get("title", ""))


def _body_text(content: list[dict], markdown: bool) -> str:
    """A body's `content` list → text. markdown=True adds `#` headings and `-` bullets.
    Tables are noted but not extracted — this is for reading prose. Pure; unit-testable."""
    parts: list[str] = []
    for el in content:
        if "paragraph" in el:
            parts.append(_para_text(el["paragraph"], markdown))
        elif "table" in el:
            parts.append("[table omitted]\n")
    return "".join(parts)


# --- tabs -----------------------------------------------------------------
# A Doc can have several tabs (one per client/topic in a shared doc). The Docs API exposes them
# only when `documents.get(includeTabsContent=True)` — without it the response carries a single
# implicit `body`. Tabs nest (a sub-tab under a tab); `tabProperties.nestingLevel` reports depth.

_TAB_PROPS = "tabProperties(tabId,title,index,nestingLevel)"


def _tabs_fields_mask(depth: int = 6) -> str:
    """A `fields` mask fetching only tab properties (not body content) down `depth` nesting levels —
    keeps `list_tabs` a tiny request. Also grabs `title` for the single-implicit-tab fallback."""
    node = _TAB_PROPS
    for _ in range(depth):
        node = f"{_TAB_PROPS},childTabs({node})"
    return f"title,tabs({node})"


def _walk_tabs(tabs: list[dict], level: int = 0) -> list[TabInfo]:
    """Flatten the (possibly nested) `tabs` tree into TabInfo, depth-first in tab order. Pure.
    Uses the API's `nestingLevel` when present, else the recursion depth."""
    out: list[TabInfo] = []
    for t in tabs:
        tp = t.get("tabProperties", {})
        nl = tp.get("nestingLevel")
        out.append(
            TabInfo(
                index=tp.get("index", 0),
                title=tp.get("title", ""),
                tab_id=tp.get("tabId", ""),
                nesting_level=nl if nl is not None else level,
            )
        )
        out += _walk_tabs(t.get("childTabs", []), level + 1)
    return out


def _tab_listing(tabs: list[TabInfo]) -> str:
    """Human-readable 'title (tabId), …' for error messages. Pure."""
    return ", ".join(f"{t.title!r} ({t.tab_id})" for t in tabs) or "(none)"


def _resolve_tab_id(tabs: list[TabInfo], tab: str) -> str:
    """Resolve a `--tab` value to a tabId: an exact tabId passes through; else a case-insensitive
    exact title match. Raises DocsNotFound (miss) / DocsError (ambiguous title) listing the tabs. Pure."""
    for t in tabs:
        if t.tab_id == tab:
            return t.tab_id
    matches = [t for t in tabs if t.title.casefold() == tab.casefold()]
    if len(matches) == 1:
        return matches[0].tab_id
    if len(matches) > 1:
        raise DocsError(
            f"tab title {tab!r} is ambiguous ({len(matches)} tabs share it) — pass the tabId. "
            f"Tabs: {_tab_listing(tabs)}"
        )
    raise DocsNotFound(f"no tab matching {tab!r} — available: {_tab_listing(tabs)}")


def _find_tab_node(tabs: list[dict], tab_id: str) -> dict | None:
    """The raw tab dict with this tabId anywhere in the (nested) tree, else None. Pure."""
    for t in tabs:
        if t.get("tabProperties", {}).get("tabId") == tab_id:
            return t
        found = _find_tab_node(t.get("childTabs", []), tab_id)
        if found is not None:
            return found
    return None


def list_tabs(doc: str) -> list[TabInfo]:
    """All tabs of a Doc, flattened depth-first (index, title, tabId, nesting_level). A doc with no
    tabs field (a single implicit tab) returns one TabInfo named after the doc, tab_id ''."""
    did = doc_id(doc)
    try:
        document = (
            get_docs_service()
            .documents()
            .get(documentId=did, includeTabsContent=True, fields=_tabs_fields_mask())
            .execute(num_retries=3)
        )
    except HttpError as e:
        _handle(e, did)
    tabs = _walk_tabs(document.get("tabs", []))
    if not tabs:  # older API / doc with no explicit tabs → one implicit tab
        return [TabInfo(index=0, title=document.get("title", ""), tab_id="", nesting_level=0)]
    return tabs


def resolve_tab(doc: str, tab: str) -> str:
    """A `--tab` value (title or raw tabId) → its tabId, validated against the live doc."""
    return _resolve_tab_id(list_tabs(doc), tab)


def get_tab_text(doc: str, tab: str, markdown: bool = True) -> str:
    """One tab's body as text (markdown=True adds `#` headings, `-` bullets). Drive's Markdown
    export can't scope to a tab, so this walks the tab's Docs-API body — lighter than `get`."""
    did = doc_id(doc)
    try:
        document = (
            get_docs_service().documents().get(documentId=did, includeTabsContent=True).execute(num_retries=3)
        )
    except HttpError as e:
        _handle(e, did)
    raw_tabs = document.get("tabs", [])
    if not raw_tabs:
        raise DocsNotFound(f"doc {did} has no tabs (single-tab doc — use `get` without --tab)")
    tab_id = _resolve_tab_id(_walk_tabs(raw_tabs), tab)
    node = _find_tab_node(raw_tabs, tab_id)
    if node is None:  # unreachable — _resolve_tab_id already validated — but keep it honest
        raise DocsNotFound(f"tab {tab_id!r} vanished from doc {did}")
    return _body_text(node.get("documentTab", {}).get("body", {}).get("content", []), markdown)


def add_tab(doc: str, title: str) -> str:
    """Create a new tab titled `title`; return its tabId. The Docs API's `addDocumentTab` request
    (verified against the live API 2026-07). Duplicate titles are legal in Docs but un-addressable by name here, so this refuses with a
    clean error if one already matches (case-insensitive)."""
    did = doc_id(doc)
    existing = list_tabs(doc)
    if any(t.title.casefold() == title.casefold() for t in existing):
        raise DocsError(f"a tab titled {title!r} already exists — titles must be unique. Tabs: {_tab_listing(existing)}")
    try:
        result = (
            get_docs_service(readonly=False)
            .documents()
            .batchUpdate(
                documentId=did,
                body={"requests": [{"addDocumentTab": {"tabProperties": {"title": title}}}]},
            )
            .execute(num_retries=3)
        )
    except HttpError as e:
        _handle(e, did)
    return result.get("replies", [{}])[0].get("addDocumentTab", {}).get("tabProperties", {}).get("tabId", "")


def find_orphan_comments(comments: list[dict], find_text: str) -> list[dict]:
    """Open comments whose quoted anchor overlaps `find_text` (crude substring, either direction) —
    a tab-scoped replace would orphan them. Pure; `comments` come from `gdrive.list_comments`."""
    if not find_text:
        return []
    hits: list[dict] = []
    for c in comments:
        q = c.get("quoted_text", "")
        if q and (find_text in q or q in find_text):
            hits.append(c)
    return hits


def append_text(doc: str, text: str, tab: str | None = None) -> int:
    """Append text to the end of the doc (or of a single tab if `tab` is a title/tabId). Returns
    the number of characters inserted."""
    did = doc_id(doc)
    svc = get_docs_service(readonly=False)
    try:
        if tab is None:
            document = svc.documents().get(documentId=did, fields="body(content(endIndex))").execute(num_retries=3)
            end = document["body"]["content"][-1]["endIndex"]
            location = {"index": max(1, end - 1)}  # body starts at 1; insert before the final newline
        else:
            document = svc.documents().get(documentId=did, includeTabsContent=True).execute(num_retries=3)
            raw_tabs = document.get("tabs", [])
            if not raw_tabs:
                raise DocsNotFound(f"doc {did} has no tabs — append without --tab")
            tab_id = _resolve_tab_id(_walk_tabs(raw_tabs), tab)
            node = _find_tab_node(raw_tabs, tab_id)
            end = node["documentTab"]["body"]["content"][-1]["endIndex"]
            location = {"index": max(1, end - 1), "tabId": tab_id}
        svc.documents().batchUpdate(
            documentId=did,
            body={"requests": [{"insertText": {"location": location, "text": text}}]},
        ).execute(num_retries=3)
    except HttpError as e:
        _handle(e, did)
    return len(text)


def replace_text(
    doc: str, find: str, replace: str, match_case: bool = False, tab_ids: list[str] | None = None
) -> int:
    """Replace every occurrence of `find` with `replace`. Returns how many were changed. With
    `tab_ids`, scope the replacement to those tabs (`tabsCriteria`); else the whole doc."""
    did = doc_id(doc)
    request: dict = {
        "replaceAllText": {
            "containsText": {"text": find, "matchCase": match_case},
            "replaceText": replace,
        }
    }
    if tab_ids:
        request["replaceAllText"]["tabsCriteria"] = {"tabIds": tab_ids}
    try:
        result = (
            get_docs_service(readonly=False)
            .documents()
            .batchUpdate(documentId=did, body={"requests": [request]})
            .execute(num_retries=3)
        )
    except HttpError as e:
        _handle(e, did)
    return result.get("replies", [{}])[0].get("replaceAllText", {}).get("occurrencesChanged", 0)


def _hex_to_rgb(hex_color: str) -> dict:
    """'#RRGGBB' -> a Docs API rgbColor dict of 0..1 floats. Pure; unit-testable."""
    h = hex_color.lstrip("#")
    if len(h) != 6:
        raise DocsError(f"bad hex color {hex_color!r} — want '#RRGGBB'")
    try:
        r, g, b = (int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))
    except ValueError as e:
        raise DocsError(f"bad hex color {hex_color!r} — want '#RRGGBB'") from e
    return {"red": round(r, 4), "green": round(g, 4), "blue": round(b, 4)}


def _find_tables(document: dict) -> list[dict]:
    """Tables in body order: each {start, rows, cols}. `start` is the tableStartLocation index. Pure."""
    out: list[dict] = []
    for el in document.get("body", {}).get("content", []):
        if "table" in el:
            t = el["table"]
            out.append({"start": el["startIndex"], "rows": t.get("rows", 0), "cols": t.get("columns", 0)})
    return out


def shade_table(
    doc: str,
    rows: list[int] | None = None,
    cols: list[int] | None = None,
    color: str = "#EFEFEF",
    table_index: int | None = 0,
) -> int:
    """Background-shade whole rows and/or columns of a table. `rows`/`cols` are 0-based indices
    (negative ok: -1 = last). Common uses: rows=[0] for a header, cols=[-1] for a totals column.
    `table_index` selects which table (0 = first); pass `None` to apply to **every** table in the
    doc (e.g. shade all headers at once). All shading goes in one batchUpdate. Returns the number of
    ranges styled. The minimal escape hatch for the bit Markdown can't do."""
    did = doc_id(doc)
    svc = get_docs_service(readonly=False)
    try:  # field mask: we only need each element's index + table dimensions
        document = (
            svc.documents()
            .get(documentId=did, fields="body(content(startIndex,table(rows,columns)))")
            .execute(num_retries=3)
        )
    except HttpError as e:
        _handle(e, did)
    tables = _find_tables(document)
    if not tables:
        raise DocsNotFound(f"no tables in doc {did}")
    if table_index is None:
        targets = tables
    elif 0 <= table_index < len(tables):
        targets = [tables[table_index]]
    else:
        raise DocsError(f"table_index {table_index} out of range — doc has {len(tables)} table(s)")
    bg = {"color": {"rgbColor": _hex_to_rgb(color)}}

    def _cell(t: dict, row_index: int, col_index: int, row_span: int, col_span: int) -> dict:
        return {
            "updateTableCellStyle": {
                "tableCellStyle": {"backgroundColor": bg},
                "fields": "backgroundColor",
                "tableRange": {
                    "tableCellLocation": {
                        "tableStartLocation": {"index": t["start"]},
                        "rowIndex": row_index,
                        "columnIndex": col_index,
                    },
                    "rowSpan": row_span,
                    "columnSpan": col_span,
                },
            }
        }

    def _norm(i: int, n: int) -> int:
        return i if i >= 0 else n + i

    requests: list[dict] = []
    for t in targets:
        requests += [_cell(t, _norm(r, t["rows"]), 0, 1, t["cols"]) for r in (rows or [])]
        requests += [_cell(t, 0, _norm(c, t["cols"]), t["rows"], 1) for c in (cols or [])]
    if not requests:
        raise DocsError("nothing to shade — pass rows and/or cols (e.g. --rows 0)")
    try:
        svc.documents().batchUpdate(documentId=did, body={"requests": requests}).execute(num_retries=3)
    except HttpError as e:
        _handle(e, did)
    return len(requests)
