"""Google Drive primitives: the Doc round-trip pieces the Docs API can't do, and a folder mirror.

Doc workflow (used by `ko gdocs`):
- `push_markdown` — turn a Markdown file into a real, formatted Google Doc (Drive converts it).
- `export_markdown` — pull a Doc back as proper Markdown (tables and all).
- `list_comments` / `reply_comment` — review comments live in the Drive API, not the Docs API.
- `ensure_folder` — find-or-create a folder so proposals don't litter My Drive root.

Folder mirror (`ko drive ls|pull|push`): `walk` a folder recursively, `pull` it into a local dir,
`push` local edits back. A manifest (`.ko-drive.json` in the local dir) records what each file
looked like at the last sync — id, remote md5/modifiedTime, local md5/size — so both sides can
be compared to a common base. That is what makes "both sides changed" a detectable conflict
rather than a silent clobber (an mtime stamp alone can't tell the two apart). Never deletes on
either side; refuses conflicts unless forced; Google-native docs export down (docx/xlsx/pptx)
but never push up (a re-import would drop comments — `ko gdocs push` is the md→Doc path).

Scope is the broad `drive` (`drive.readonly` on read tokens) since 2026-09-10; per-folder
scoping is a service-account profile (`-a <name>`). Shared drives: every `files.*` call passes
`supportsAllDrives` (and `list` also `includeItemsFromAllDrives`). `export`/`comments`/`replies`
have no such flag and need none.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import mimetypes
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import NoReturn

from googleapiclient.errors import HttpError
from googleapiclient.http import (
    MediaFileUpload,
    MediaInMemoryUpload,
    MediaIoBaseDownload,
)

from ko.google_auth import (
    GoogleError,
    id_from_url,
    raise_for_status,
    service,
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
        hint="Is it shared with this account (`-a` picks one; a service account only sees what's "
        "shared with its client_email), and did you re-run `ko google auth` since the Drive "
        "scope widened (2026-09)?",
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


def push_markdown(
    md_text: str, title: str, folder_id: str | None = None, update_id: str | None = None
) -> str:
    """Markdown -> a Google Doc; return its doc ID.

    Default: create a NEW Doc (`folder_id` places it in a folder, else My Drive root). With
    `update_id`, instead **re-import into that existing Doc in place** — same id, same URL, sharing
    preserved — which is what you want for an iterated proposal/reference. Caveat: an update replaces
    the whole body, so any manual shading and existing comment anchors are dropped (for a doc under
    live review, edit with `gdocs.replace` instead). Drive converts `text/markdown` server-side;
    images embed as base64 and don't round-trip cleanly — fine for text, not image-heavy decks.
    """
    media = MediaInMemoryUpload(_md_bytes(md_text), mimetype=MARKDOWN_MIME, resumable=False)
    svc = service("drive", readonly=False)
    if update_id:
        did = _doc_id(update_id)
        try:
            svc.files().update(
                fileId=did, media_body=media, fields="id", supportsAllDrives=True
            ).execute(num_retries=3)
        except HttpError as e:
            _handle(e, did)
        return did
    body: dict = {"name": title, "mimeType": DOC_MIME}
    if folder_id:
        body["parents"] = [_folder_id(folder_id)]
    try:
        created = (
            svc.files()
            .create(body=body, media_body=media, fields="id", supportsAllDrives=True)
            .execute(num_retries=3)
        )
    except HttpError as e:
        _handle(e, f"create doc '{title}'")
    return created["id"]


def diff_summary(old: str, new: str) -> tuple[str, list[str]]:
    """Compare two Markdown strings for the overwrite sanity-check. Returns (summary, diff_lines):
    summary like '~37% of the content changed'; diff_lines a unified diff ready to print. The percent
    is character-level (difflib SequenceMatcher) so it's robust to line re-wrapping — unlike a +/-
    line count, which a hard-wrapped source vs the Doc's reflowed export would wildly inflate. Pure
    (stdlib difflib), so unit-testable without any Doc."""
    pct = round((1 - difflib.SequenceMatcher(None, old, new).ratio()) * 100)
    diff = list(difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm=""))
    return f"~{pct}% of the content changed", diff


def export_markdown(doc: str) -> str:
    """Export a Google Doc as Markdown text (tables included)."""
    did = _doc_id(doc)
    try:
        data = (
            service("drive").files().export(fileId=did, mimeType=MARKDOWN_MIME).execute(num_retries=3)
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
    svc = service("drive")
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
    """Post a reply on a comment thread; return the flattened new reply. Needs the write grant."""
    did = _doc_id(doc)
    try:
        r = (
            service("drive", readonly=False)
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
    """Find-or-create a Drive folder by name; return its ID. `parent_id` nests it; else root."""
    parent = _folder_id(parent_id) if parent_id else None
    # escape the name for a Drive query string literal — an apostrophe would otherwise
    # break the query (or inject a clause)
    safe_name = name.replace("\\", "\\\\").replace("'", "\\'")
    q = (
        f"mimeType='{FOLDER_MIME}' and name='{safe_name}' and trashed=false"
        + (f" and '{parent}' in parents" if parent else "")
    )
    svc = service("drive", readonly=False)
    try:
        found = (
            svc.files()
            .list(
                q=q,
                spaces="drive",
                fields="files(id,name)",
                pageSize=1,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
            .execute(num_retries=3)
        )
        files = found.get("files", [])
        if files:
            return files[0]["id"]
        body: dict = {"name": name, "mimeType": FOLDER_MIME}
        if parent:
            body["parents"] = [parent]
        created = (
            svc.files().create(body=body, fields="id", supportsAllDrives=True).execute(num_retries=3)
        )
    except HttpError as e:
        _handle(e, f"folder '{name}'")
    return created["id"]


# --- folder mirror: walk / pull / push ---------------------------------------------------

MANIFEST = ".ko-drive.json"
SHORTCUT_MIME = "application/vnd.google-apps.shortcut"
NATIVE_PREFIX = "application/vnd.google-apps."
# Google-native types can't be downloaded raw — export to the Office equivalent.
EXPORTS = {
    "application/vnd.google-apps.document": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".docx",
    ),
    "application/vnd.google-apps.spreadsheet": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xlsx",
    ),
    "application/vnd.google-apps.presentation": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".pptx",
    ),
}
_LIST_FIELDS = "nextPageToken, files(id, name, mimeType, size, modifiedTime, md5Checksum)"
_FILE_FIELDS = "id, name, mimeType, size, modifiedTime, md5Checksum"
_ALL_DRIVES = {"supportsAllDrives": True}


@dataclass
class DriveFile:
    id: str
    name: str
    mime: str
    modified: datetime
    size: int | None  # None for Google-native files (export size is unknown until fetched)
    md5: str | None  # Drive's md5Checksum; None for Google-native files
    rel: Path  # path relative to the root folder, export suffix applied
    parent: str  # id of the folder it sits in

    @property
    def native(self) -> bool:
        return self.mime.startswith(NATIVE_PREFIX)

    @property
    def exported(self) -> bool:
        return self.mime in EXPORTS


@dataclass
class Tree:
    """One recursive listing. `folders` maps rel folder path → id (Path() is the root)."""

    root: str
    files: list[DriveFile] = field(default_factory=list)
    folders: dict[Path, str] = field(default_factory=dict)
    duplicates: list[DriveFile] = field(default_factory=list)  # same rel path twice; first wins
    unsupported: list[DriveFile] = field(default_factory=list)  # shortcuts, native w/o export

    def by_rel(self) -> dict[Path, DriveFile]:
        return {f.rel: f for f in self.files}


def folder_name(folder: str, *, readonly: bool = True) -> str:
    """A folder's display name (for the default local dir name)."""
    fid = _folder_id(folder)
    try:
        meta = (
            service("drive", readonly)
            .files()
            .get(fileId=fid, fields="name", **_ALL_DRIVES)
            .execute(num_retries=3)
        )
    except HttpError as e:
        _handle(e, f"folder {fid}")
    return meta["name"]


def walk(folder: str, *, readonly: bool = True) -> Tree:
    """Every file under a folder, recursively; subfolders become path parts. Shortcuts and
    Google-native types with no Office export are listed under `unsupported`, not `files`."""
    svc = service("drive", readonly)
    root = _folder_id(folder)
    tree = Tree(root=root, folders={Path(): root})
    seen: set[Path] = set()

    def _walk(fid: str, prefix: Path) -> None:
        token = None
        while True:
            try:
                resp = (
                    svc.files()
                    .list(
                        q=f"'{fid}' in parents and trashed = false",
                        fields=_LIST_FIELDS,
                        orderBy="createdTime",  # Drive promises no order; keeps first-wins stable
                        pageSize=200,
                        pageToken=token,
                        includeItemsFromAllDrives=True,
                        **_ALL_DRIVES,
                    )
                    .execute(num_retries=3)
                )
            except HttpError as e:
                _handle(e, f"folder {fid}")
            for raw in resp.get("files", []):
                if raw["mimeType"] == FOLDER_MIME:
                    sub = prefix / _safe_name(raw["name"])
                    tree.folders[sub] = raw["id"]
                    _walk(raw["id"], sub)
                    continue
                f = _drive_file(raw, prefix, fid)
                if f.mime == SHORTCUT_MIME or (f.native and not f.exported):
                    tree.unsupported.append(f)
                elif f.rel in seen:
                    tree.duplicates.append(f)
                else:
                    seen.add(f.rel)
                    tree.files.append(f)
            token = resp.get("nextPageToken")
            if not token:
                return

    _walk(root, Path())
    tree.files.sort(key=lambda f: str(f.rel))
    return tree


def _safe_name(name: str) -> str:
    """A Drive name as one local path component: Drive allows `/` (and `..`) in names."""
    name = name.replace("/", "_")
    return "_" if name in ("", ".", "..") else name


def _drive_file(raw: dict, prefix: Path, parent: str) -> DriveFile:
    suffix = EXPORTS.get(raw["mimeType"], (None, ""))[1]
    return DriveFile(
        id=raw["id"],
        name=raw["name"],
        mime=raw["mimeType"],
        modified=datetime.fromisoformat(raw["modifiedTime"]),
        size=int(raw["size"]) if "size" in raw else None,
        md5=raw.get("md5Checksum"),
        rel=prefix / (_safe_name(raw["name"]) + suffix),
        parent=parent,
    )


# -- manifest: the common base both sides are compared against --


@dataclass
class Entry:
    """What one file looked like at the last sync."""

    id: str
    modified: str  # remote modifiedTime, ISO
    remote_md5: str | None  # None for exported Google-native files
    md5: str  # md5 of the LOCAL bytes after that sync
    size: int  # local size after that sync


@dataclass
class Manifest:
    folder: str
    files: dict[str, Entry] = field(default_factory=dict)  # rel (posix str) → Entry

    @classmethod
    def load(cls, dest: Path) -> Manifest | None:
        p = dest / MANIFEST
        if not p.exists():
            return None
        raw = json.loads(p.read_text())
        return cls(raw["folder"], {k: Entry(**v) for k, v in raw.get("files", {}).items()})

    def save(self, dest: Path) -> None:
        payload = {"folder": self.folder, "files": {k: vars(v) for k, v in sorted(self.files.items())}}
        tmp = dest / (MANIFEST + ".tmp")
        tmp.write_text(json.dumps(payload, indent=1) + "\n")
        tmp.replace(dest / MANIFEST)

    def get(self, rel: Path) -> Entry | None:
        return self.files.get(rel.as_posix())

    def put(self, rel: Path, entry: Entry) -> None:
        self.files[rel.as_posix()] = entry


def _md5(path: Path) -> str:
    h = hashlib.md5()  # content fingerprint to match Drive's md5Checksum, not security
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _stamp(path: Path, modified: datetime) -> None:
    ts = modified.timestamp()
    os.utime(path, (ts, ts))


def _local_changed(path: Path, entry: Entry) -> bool:
    """Has the local file changed since the sync recorded in `entry`? Size or the stamped
    mtime differing is the cheap signal; only a moved mtime with the same size pays for a hash."""
    st = path.stat()
    if st.st_size != entry.size:
        return True
    if abs(st.st_mtime - datetime.fromisoformat(entry.modified).timestamp()) < 1:
        return False
    return _md5(path) != entry.md5


def _remote_changed(f: DriveFile, entry: Entry) -> bool:
    if f.md5 and entry.remote_md5:
        return f.md5 != entry.remote_md5
    return f.modified.isoformat() != entry.modified


def _entry_for(f: DriveFile, path: Path) -> Entry:
    st = path.stat()
    return Entry(
        id=f.id,
        modified=f.modified.isoformat(),
        remote_md5=f.md5,
        md5=_md5(path),
        size=st.st_size,
    )


# -- pull --


@dataclass
class PullResult:
    added: list[DriveFile] = field(default_factory=list)
    updated: list[DriveFile] = field(default_factory=list)
    skipped: list[DriveFile] = field(default_factory=list)
    local_edits: list[DriveFile] = field(default_factory=list)  # changed here only; left alone
    conflicts: list[DriveFile] = field(default_factory=list)  # changed on both sides; refused
    unsupported: list[DriveFile] = field(default_factory=list)
    duplicates: list[DriveFile] = field(default_factory=list)
    local_only: list[Path] = field(default_factory=list)


def _download(svc, f: DriveFile, dest: Path) -> None:
    if f.exported:
        req = svc.files().export_media(fileId=f.id, mimeType=EXPORTS[f.mime][0])
    else:
        req = svc.files().get_media(fileId=f.id, **_ALL_DRIVES)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".ko-tmp")
    with tmp.open("wb") as fh:  # streamed: a client folder can hold a video
        dl = MediaIoBaseDownload(fh, req)
        done = False
        while not done:
            _, done = dl.next_chunk(num_retries=3)
    tmp.replace(dest)
    _stamp(dest, f.modified)


def pull(folder: str, dest: Path, *, dry_run: bool = False, force: bool = False) -> PullResult:
    """Mirror a Drive folder into `dest`: download new + remotely-changed files, skip unchanged,
    leave local-only edits alone, refuse both-sides-changed unless `force` (then remote wins).
    Never deletes. A file present locally with no manifest entry is adopted if its bytes match
    Drive, else treated as a conflict."""
    svc = service("drive", readonly=True)
    tree = walk(folder)
    man = Manifest.load(dest) or Manifest(tree.root)
    if man.folder != tree.root and not force:
        raise DriveError(
            f"{dest} was pulled from folder {man.folder}, not {tree.root} — --force re-homes it"
        )
    man.folder = tree.root
    res = PullResult(unsupported=tree.unsupported, duplicates=tree.duplicates)
    if not dry_run:
        dest.mkdir(parents=True, exist_ok=True)
    try:
        _apply_pull(svc, tree, dest, man, res, dry_run=dry_run, force=force)
    finally:
        if not dry_run:
            _prune(man, {f.rel for f in tree.files}, dest)
            man.save(dest)
    known = {f.rel for f in tree.files}
    if dest.exists():
        res.local_only = sorted(
            p.relative_to(dest)
            for p in dest.rglob("*")
            if p.is_file() and not p.name.startswith(".") and p.relative_to(dest) not in known
        )
    return res


def _apply_pull(svc, tree: Tree, dest: Path, man: Manifest, res: PullResult, *, dry_run: bool, force: bool) -> None:
    for f in tree.files:
        target = dest / f.rel
        entry = man.get(f.rel)
        if not target.exists():
            action = "add"
        elif entry is None:
            if f.md5 and _md5(target) == f.md5:
                action = "adopt"
            else:
                action = "conflict"
        else:
            local, remote = _local_changed(target, entry), _remote_changed(f, entry)
            if not remote:
                action = "local" if local else "skip"
            else:
                action = "conflict" if local else "update"
        if action == "conflict" and force:
            action = "update"
        match action:
            case "skip":
                res.skipped.append(f)
            case "local":
                res.local_edits.append(f)
            case "conflict":
                res.conflicts.append(f)
            case "adopt":
                res.skipped.append(f)
                if not dry_run:
                    _stamp(target, f.modified)
                    man.put(f.rel, _entry_for(f, target))
            case "add" | "update":
                (res.added if action == "add" else res.updated).append(f)
                if not dry_run:
                    try:
                        _download(svc, f, target)
                    except HttpError as e:
                        _handle(e, str(f.rel))
                    man.put(f.rel, _entry_for(f, target))


def _prune(man: Manifest, remote: set[Path], dest: Path) -> None:
    """Drop entries for files gone from BOTH sides — a stale base helps nobody."""
    for key in list(man.files):
        if Path(key) not in remote and not (dest / key).exists():
            del man.files[key]


# -- push --


@dataclass
class PushResult:
    created: list[Path] = field(default_factory=list)
    updated: list[Path] = field(default_factory=list)
    skipped: list[Path] = field(default_factory=list)
    remote_edits: list[Path] = field(default_factory=list)  # changed on Drive only; pull first
    conflicts: list[Path] = field(default_factory=list)  # both sides, or removed on Drive; refused
    native: list[Path] = field(default_factory=list)  # exported Docs/Sheets/Slides; never pushed


def _local_files(src: Path) -> list[Path]:
    return sorted(
        p.relative_to(src)
        for p in src.rglob("*")
        if p.is_file() and not any(part.startswith(".") for part in p.relative_to(src).parts)
    )


def _ensure_parent(svc, tree: Tree, rel_dir: Path) -> str:
    """Folder id for a rel dir, creating the chain on Drive as needed (cached in tree.folders)."""
    if rel_dir in tree.folders:
        return tree.folders[rel_dir]
    parent = _ensure_parent(svc, tree, rel_dir.parent)
    body = {"name": rel_dir.name, "mimeType": FOLDER_MIME, "parents": [parent]}
    try:
        created = svc.files().create(body=body, fields="id", **_ALL_DRIVES).execute(num_retries=3)
    except HttpError as e:
        _handle(e, f"create folder {rel_dir}")
    tree.folders[rel_dir] = created["id"]
    return created["id"]


def _upload(svc, path: Path, *, parent: str | None = None, file_id: str | None = None) -> dict:
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    media = MediaFileUpload(str(path), mimetype=mime, resumable=path.stat().st_size > 5 << 20)
    try:
        if file_id:
            req = svc.files().update(fileId=file_id, media_body=media, fields=_FILE_FIELDS, **_ALL_DRIVES)
        else:
            body = {"name": path.name, "parents": [parent]}
            req = svc.files().create(body=body, media_body=media, fields=_FILE_FIELDS, **_ALL_DRIVES)
        return req.execute(num_retries=3)
    except HttpError as e:
        _handle(e, path.name)


def push(src: Path, folder: str | None = None, *, dry_run: bool = False, force: bool = False) -> PushResult:
    """Push local files under `src` to the Drive folder: create what Drive lacks, update in place
    (same id, sharing kept) what changed here, skip unchanged. Both-sides-changed and
    removed-on-Drive are conflicts: with any present, nothing is uploaded (all-or-nothing) unless
    `force`, which makes local win. `folder` defaults to the manifest's; a first push into a
    fresh folder needs it, and a folder that disagrees with the manifest is refused (force
    re-homes). Never deletes on Drive; never pushes over a Google-native doc."""
    man = Manifest.load(src)
    if folder is None:
        if man is None:
            raise DriveError(f"{src} has no {MANIFEST} — name the Drive folder for a first push")
        folder = man.folder
    svc = service("drive", readonly=False)
    tree = walk(folder, readonly=False)
    man = man or Manifest(tree.root)
    if man.folder != tree.root and not force:
        raise DriveError(
            f"{src} was pulled from folder {man.folder}, not {tree.root} — --force re-homes it"
        )
    man.folder = tree.root
    plan = _plan_push(src, tree, man, force=force)
    if plan.conflicts or dry_run:
        return plan
    try:
        _apply_push(svc, src, tree, man, plan)
    finally:
        man.save(src)
    return plan


def _plan_push(src: Path, tree: Tree, man: Manifest, *, force: bool) -> PushResult:
    """Classify every local file without touching Drive. Pure given the tree + manifest."""
    remote = tree.by_rel()
    res = PushResult()
    for rel in _local_files(src):
        path, f, entry = src / rel, remote.get(rel), man.get(rel)
        if f is not None and f.native:
            res.native.append(rel)
            continue
        if f is None:
            action = "conflict" if entry is not None else "create"  # entry, no remote = removed on Drive
        elif entry is None:
            action = "skip" if f.md5 and _md5(path) == f.md5 else "conflict"
        else:
            local, changed_remote = _local_changed(path, entry), _remote_changed(f, entry)
            if not local:
                action = "remote" if changed_remote else "skip"
            else:
                action = "conflict" if changed_remote else "update"
        if action == "conflict" and force:
            action = "create" if f is None else "update"
        {
            "skip": res.skipped,
            "remote": res.remote_edits,
            "conflict": res.conflicts,
            "create": res.created,
            "update": res.updated,
        }[action].append(rel)
    return res


def _apply_push(svc, src: Path, tree: Tree, man: Manifest, plan: PushResult) -> None:
    remote = tree.by_rel()
    for rel in plan.skipped:  # adopt untracked-but-identical files into the manifest
        f = remote.get(rel)
        if f is not None and man.get(rel) is None:
            _stamp(src / rel, f.modified)
            man.put(rel, _entry_for(f, src / rel))
    for rel in plan.created:
        raw = _upload(svc, src / rel, parent=_ensure_parent(svc, tree, rel.parent))
        _record(man, rel, raw, tree, src)
    for rel in plan.updated:
        raw = _upload(svc, src / rel, file_id=remote[rel].id)
        _record(man, rel, raw, tree, src)


def _record(man: Manifest, rel: Path, raw: dict, tree: Tree, src: Path) -> None:
    nf = _drive_file(raw, rel.parent, tree.folders.get(rel.parent, tree.root))
    _stamp(src / rel, nf.modified)
    man.put(rel, _entry_for(nf, src / rel))
