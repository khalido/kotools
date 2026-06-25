"""Google Docs reader/writer — minimal primitives over the Docs v1 API.

Read a doc as plain text or light markdown; append text to the end; find/replace across
the whole doc; create a new doc. Addresses docs by ID or URL — no Drive scope needed.
Reads use the read-only scope, writes the read+write one (one `ko gsheets auth` covers both).

Deliberately minimal: full structural editing (tables, styles, inline formatting, images) is
the web editor's job. This is the "read it / append a note / swap some text" surface.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from googleapiclient.errors import HttpError

from ko.google_auth import get_docs_service


@dataclass
class DocInfo:
    id: str
    title: str


class DocsError(RuntimeError):
    pass


class DocsNotFound(DocsError):
    pass


class DocsPermissionDenied(DocsError):
    pass


def _handle(e: HttpError, context: str) -> None:
    if e.resp.status == 403:
        raise DocsPermissionDenied(
            f"Permission denied for {context}. Is the doc accessible to the signed-in Google "
            f"account — and for a write, did you `ko gsheets auth` (read+write)?"
        ) from e
    if e.resp.status == 404:
        raise DocsNotFound(f"Not found: {context}") from e
    raise e


_DOC_URL_RE = re.compile(r"/document/d/([a-zA-Z0-9_-]+)")


def doc_id(value: str) -> str:
    """Accept a Google Docs URL or a bare document ID; return the ID."""
    m = _DOC_URL_RE.search(value)
    return m.group(1) if m else value.strip()


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


def create_doc(title: str) -> str:
    """Create a new Google Doc; return its ID. Lands in My Drive root."""
    try:
        result = get_docs_service(readonly=False).documents().create(body={"title": title}).execute(num_retries=3)
    except HttpError as e:
        _handle(e, f"create doc '{title}'")
    return result["documentId"]
