"""Google Drive primitives — the few Drive-API features the Docs API can't do.

`ko` requests only the narrow `drive.file` scope: it can touch **files it creates or that you
open by ID, and nothing else** — it cannot browse or search your Drive. That's enough for a
Markdown↔Doc proposal workflow:

- `push_markdown` — turn a Markdown file into a real, formatted Google Doc (Drive converts it).
- `export_markdown` — pull a Doc back as proper Markdown (tables and all — higher fidelity than
  `gdocs.get_text`, which is light/lossy).
- `list_comments` — read review comments + replies off a Doc (comments live in the Drive API,
  not the Docs API).
- `ensure_folder` — find-or-create a folder so proposals don't litter My Drive root.

**drive.file caveat:** because the scope only covers app-created/opened files, `export_markdown`
and `list_comments` work on docs **`ko` pushed** (or that you explicitly opened by ID through it),
not on arbitrary pre-existing docs someone else made — those return 404. That's the deliberate
price of not granting a broad Drive scope. See `google_auth.py`.
"""

from __future__ import annotations

from typing import NoReturn

from googleapiclient.errors import HttpError
from googleapiclient.http import MediaInMemoryUpload

from ko.google_auth import (
    GoogleError,
    get_drive_service,
    id_from_url,
    raise_for_status,
)

DOC_MIME = "application/vnd.google-apps.document"
FOLDER_MIME = "application/vnd.google-apps.folder"
MARKDOWN_MIME = "text/markdown"


class DriveError(GoogleError):
    pass


class DriveNotFound(DriveError):
    pass


class DrivePermissionDenied(DriveError):
    pass


def _handle(e: HttpError, context: str) -> NoReturn:
    raise_for_status(
        e,
        context,
        not_found=DriveNotFound,
        permission=DrivePermissionDenied,
        # drive.file only sees files ko made/opened — the usual cause of a 404/403 here.
        hint="Did `ko` create this doc (drive.file can't see docs made elsewhere), and did you "
        "re-run `ko gsheets auth` after the Drive scope was added?",
    )


def _doc_id(value: str) -> str:
    """Accept a Google Docs URL or bare ID; return the ID."""
    return id_from_url(value, "document")


def _folder_id(value: str) -> str:
    """Accept a Drive folder URL (`/folders/<id>`) or bare ID; return the ID."""
    return id_from_url(value, "folder")


def _md_bytes(md_text: str) -> bytes:
    """Markdown text → upload bytes (UTF-8). Split out so it's testable without auth."""
    return md_text.encode("utf-8")


def _flatten_comment(raw: dict) -> dict:
    """One raw Drive comment → a clean, flat dict. Pure (no I/O) so it's unit-testable.

    Drive nests author under {author:{displayName}}, the highlighted text under
    {quotedFileContent:{value}}, and replies as a list of the same shape.
    """

    def author(node: dict) -> str:
        return (node.get("author") or {}).get("displayName", "")

    return {
        "id": raw.get("id", ""),
        "author": author(raw),
        "created_time": raw.get("createdTime", ""),
        "quoted_text": (raw.get("quotedFileContent") or {}).get("value", ""),
        "content": raw.get("content", ""),
        "resolved": bool(raw.get("resolved", False)),
        "replies": [
            {
                "author": author(r),
                "created_time": r.get("createdTime", ""),
                "content": r.get("content", ""),
            }
            for r in raw.get("replies", [])
        ],
    }


def push_markdown(md_text: str, title: str, folder_id: str | None = None) -> str:
    """Create a NEW Google Doc from Markdown; return its doc ID.

    Drive converts `text/markdown` media into a native Doc when the target mimeType is a Doc.
    `folder_id` (if given) places it in that folder, else My Drive root. Images embed as base64
    and don't round-trip cleanly — fine for text proposals, not image-heavy decks.
    """
    body: dict = {"name": title, "mimeType": DOC_MIME}
    if folder_id:
        body["parents"] = [_folder_id(folder_id)]
    media = MediaInMemoryUpload(_md_bytes(md_text), mimetype=MARKDOWN_MIME, resumable=False)
    try:
        created = (
            get_drive_service(readonly=False)
            .files()
            .create(body=body, media_body=media, fields="id")
            .execute(num_retries=3)
        )
    except HttpError as e:
        _handle(e, f"create doc '{title}'")
    return created["id"]


def export_markdown(doc: str) -> str:
    """Export a Google Doc as Markdown text (tables included — supersedes gdocs.get_text)."""
    did = _doc_id(doc)
    try:
        data = (
            get_drive_service().files().export(fileId=did, mimeType=MARKDOWN_MIME).execute(num_retries=3)
        )
    except HttpError as e:
        _handle(e, did)
    return data.decode("utf-8") if isinstance(data, bytes) else str(data)


_COMMENT_FIELDS = (
    "nextPageToken,comments(id,author/displayName,createdTime,quotedFileContent/value,"
    "content,resolved,replies(author/displayName,createdTime,content))"
)


def list_comments(doc: str, include_resolved: bool = False) -> list[dict]:
    """All comments on a Doc (paged), each flattened with its replies. Resolved ones are
    dropped unless `include_resolved`. Drive *requires* an explicit `fields` here."""
    did = _doc_id(doc)
    svc = get_drive_service()
    out: list[dict] = []
    page_token: str | None = None
    try:
        while True:
            resp = (
                svc.comments()
                .list(fileId=did, fields=_COMMENT_FIELDS, pageSize=100, pageToken=page_token)
                .execute(num_retries=3)
            )
            for raw in resp.get("comments", []):
                comment = _flatten_comment(raw)
                if comment["resolved"] and not include_resolved:
                    continue
                out.append(comment)
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
    except HttpError as e:
        _handle(e, did)
    return out


def reply_comment(doc: str, comment_id: str, text: str) -> dict:
    """Post a reply on a comment thread; return the flattened new reply. Needs the write grant
    (and drive.file only lets you reply on docs `ko` created)."""
    did = _doc_id(doc)
    try:
        r = (
            get_drive_service(readonly=False)
            .replies()
            .create(
                fileId=did,
                commentId=comment_id,
                fields="id,author/displayName,createdTime,content",
                body={"content": text},
            )
            .execute(num_retries=3)
        )
    except HttpError as e:
        _handle(e, f"{did} comment {comment_id}")
    return {
        "id": r.get("id", ""),
        "author": (r.get("author") or {}).get("displayName", ""),
        "created_time": r.get("createdTime", ""),
        "content": r.get("content", ""),
    }


def ensure_folder(name: str, parent_id: str | None = None) -> str:
    """Find-or-create a Drive folder by name; return its ID. Only sees folders `ko` itself made
    (drive.file), which is exactly the set we'd want to reuse. `parent_id` nests it; else root."""
    parent = _folder_id(parent_id) if parent_id else None
    q = (
        f"mimeType='{FOLDER_MIME}' and name='{name}' and trashed=false"
        + (f" and '{parent}' in parents" if parent else "")
    )
    svc = get_drive_service(readonly=False)
    try:
        found = (
            svc.files()
            .list(q=q, spaces="drive", fields="files(id,name)", pageSize=1)
            .execute(num_retries=3)
        )
        files = found.get("files", [])
        if files:
            return files[0]["id"]
        body: dict = {"name": name, "mimeType": FOLDER_MIME}
        if parent:
            body["parents"] = [parent]
        created = svc.files().create(body=body, fields="id").execute(num_retries=3)
    except HttpError as e:
        _handle(e, f"folder '{name}'")
    return created["id"]
