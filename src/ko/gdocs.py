"""Google Docs reader/writer — minimal primitives over the Docs v1 API.

Read a doc as plain text or light markdown; append text to the end; find/replace across
the whole doc; create a new doc. Addresses docs by ID or URL.
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


def get_text(doc: str, markdown: bool = False) -> str:
    """The doc body as text. markdown=True adds `#` headings and `-` bullets (light, not lossless).
    Tables are noted but not extracted — this is for reading prose."""
    did = doc_id(doc)
    try:
        document = get_docs_service().documents().get(documentId=did).execute(num_retries=3)
    except HttpError as e:
        _handle(e, did)
    parts: list[str] = []
    for el in document.get("body", {}).get("content", []):
        if "paragraph" in el:
            parts.append(_para_text(el["paragraph"], markdown))
        elif "table" in el:
            parts.append("[table omitted]\n")
    return "".join(parts)


def append_text(doc: str, text: str) -> int:
    """Append text to the end of the doc. Returns the number of characters inserted."""
    did = doc_id(doc)
    svc = get_docs_service(readonly=False)
    try:
        document = svc.documents().get(documentId=did, fields="body(content(endIndex))").execute(num_retries=3)
        end = document["body"]["content"][-1]["endIndex"]
        index = max(1, end - 1)  # body starts at 1; insert before the final newline
        svc.documents().batchUpdate(
            documentId=did,
            body={"requests": [{"insertText": {"location": {"index": index}, "text": text}}]},
        ).execute(num_retries=3)
    except HttpError as e:
        _handle(e, did)
    return len(text)


def replace_text(doc: str, find: str, replace: str, match_case: bool = False) -> int:
    """Replace every occurrence of `find` with `replace`. Returns how many were changed."""
    did = doc_id(doc)
    try:
        result = (
            get_docs_service(readonly=False)
            .documents()
            .batchUpdate(
                documentId=did,
                body={
                    "requests": [
                        {
                            "replaceAllText": {
                                "containsText": {"text": find, "matchCase": match_case},
                                "replaceText": replace,
                            }
                        }
                    ]
                },
            )
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


def create_doc(title: str) -> str:
    """Create a new Google Doc; return its ID. Lands in My Drive root."""
    try:
        result = get_docs_service(readonly=False).documents().create(body={"title": title}).execute(num_retries=3)
    except HttpError as e:
        _handle(e, f"create doc '{title}'")
    return result["documentId"]
