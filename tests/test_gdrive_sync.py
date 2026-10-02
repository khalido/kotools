"""Offline tests for the `ko drive` folder mirror (walk / pull / push) — a fake Drive service
stands in for `files().list()`, and the media transfer helpers are stubbed so the tests exercise
the classification logic (what gets added / updated / refused) rather than googleapiclient."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ko import gdrive

FOLDER = gdrive.FOLDER_MIME
DOC = "application/vnd.google-apps.document"
T1 = "2026-09-01T10:00:00.000Z"
T2 = "2026-09-02T10:00:00.000Z"


def _md5(b: bytes) -> str:
    return hashlib.md5(b).hexdigest()


class _Req:
    def __init__(self, payload):
        self.payload = payload

    def execute(self, num_retries=0):
        return self.payload


class FakeDrive:
    """A Drive folder tree: {folder_id: [raw file dicts]}. Binary files carry `content` (bytes)
    which sets size + md5Checksum, the way Drive would report them."""

    def __init__(self, tree: dict[str, list[dict]], page_size: int = 200):
        self.tree = tree
        self.page_size = page_size
        self.created: list[dict] = []

    def files(self):
        return self

    def list(self, q, fields, orderBy, pageSize, pageToken, includeItemsFromAllDrives, supportsAllDrives):
        fid = q.split("'")[1]
        items = [self._raw(r) for r in self.tree.get(fid, [])]
        start = int(pageToken or 0)
        page = items[start : start + self.page_size]
        nxt = str(start + self.page_size) if start + self.page_size < len(items) else None
        return _Req({"files": page, **({"nextPageToken": nxt} if nxt else {})})

    def get(self, fileId, fields, supportsAllDrives):
        return _Req({"name": {"root": "Acme Client Files"}.get(fileId, fileId)})

    def create(self, body, fields, supportsAllDrives, media_body=None):
        self.created.append(body)
        return _Req({"id": f"new-{len(self.created)}"})

    @staticmethod
    def _raw(r: dict) -> dict:
        out = {k: v for k, v in r.items() if k != "content"}
        if "content" in r:
            out["size"] = str(len(r["content"]))
            out["md5Checksum"] = _md5(r["content"])
        return out


def _bin(fid, name, content: bytes, modified=T1):
    return {"id": fid, "name": name, "mimeType": "text/plain", "modifiedTime": modified, "content": content}


@pytest.fixture
def drive(monkeypatch):
    """Two binary files, one subfolder with a file, one Google Doc, one shortcut, one dupe."""
    fake = FakeDrive(
        {
            "root": [
                _bin("f1", "readme.txt", b"hello"),
                _bin("f2", "notes.txt", b"notes"),
                {"id": "sub", "name": "Data", "mimeType": FOLDER, "modifiedTime": T1},
                {"id": "d1", "name": "Brief", "mimeType": DOC, "modifiedTime": T1},
                {"id": "s1", "name": "Link", "mimeType": gdrive.SHORTCUT_MIME, "modifiedTime": T1},
                _bin("f2b", "notes.txt", b"other notes"),  # same name twice in one folder
            ],
            "sub": [_bin("f3", "q1/report.csv", b"a,b\n1,2\n")],  # a "/" in a Drive name
        }
    )
    monkeypatch.setattr(gdrive, "service", lambda api, readonly=True, account=None: fake)
    contents = {"f1": b"hello", "f2": b"notes", "f3": b"a,b\n1,2\n", "d1": b"DOCX-BYTES"}

    def fake_download(svc, f, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(contents[f.id])
        gdrive._stamp(dest, f.modified)

    monkeypatch.setattr(gdrive, "_download", fake_download)
    fake.contents = contents
    return fake


def test_walk_flattens_subfolders_and_sorts_out_the_odd_cases(drive):
    tree = gdrive.walk("root")
    rels = [f.rel.as_posix() for f in tree.files]
    assert rels == ["Brief.docx", "Data/q1_report.csv", "notes.txt", "readme.txt"]
    assert tree.folders == {Path(): "root", Path("Data"): "sub"}
    assert [f.id for f in tree.unsupported] == ["s1"]  # shortcut: listed, never followed
    assert [f.id for f in tree.duplicates] == ["f2b"]  # first wins, the twin is reported
    doc = tree.by_rel()[Path("Brief.docx")]
    assert doc.exported and doc.md5 is None and doc.size is None


def test_walk_pages(drive):
    drive.page_size = 2
    assert len(gdrive.walk("root").files) == 4


def test_pull_then_pull_again_is_a_noop(drive, tmp_path):
    dest = tmp_path / "acme"
    res = gdrive.pull("root", dest)
    assert [f.rel.as_posix() for f in res.added] == ["Brief.docx", "Data/q1_report.csv", "notes.txt", "readme.txt"]
    assert (dest / "readme.txt").read_bytes() == b"hello"
    man = json.loads((dest / gdrive.MANIFEST).read_text())
    assert man["folder"] == "root"
    assert man["files"]["readme.txt"]["remote_md5"] == _md5(b"hello")
    assert man["files"]["Brief.docx"]["remote_md5"] is None  # exported: modifiedTime is the signal
    again = gdrive.pull("root", dest)
    assert not again.added and not again.updated and len(again.skipped) == 4


def test_pull_dry_run_touches_nothing(drive, tmp_path):
    dest = tmp_path / "acme"
    res = gdrive.pull("root", dest, dry_run=True)
    assert len(res.added) == 4 and not dest.exists()


def test_pull_remote_change_updates_local_edit_stays_both_conflicts(drive, tmp_path):
    dest = tmp_path / "acme"
    gdrive.pull("root", dest)
    # remote changed: readme edited on Drive
    drive.tree["root"][0] = _bin("f1", "readme.txt", b"hello v2", T2)
    drive.contents["f1"] = b"hello v2"
    # local edit only: notes edited here
    (dest / "notes.txt").write_bytes(b"notes + my edit")
    # both: report edited on Drive AND here
    drive.tree["sub"][0] = _bin("f3", "q1/report.csv", b"a,b\n9,9\n", T2)
    (dest / "Data/q1_report.csv").write_bytes(b"a,b\nmine\n")

    res = gdrive.pull("root", dest)
    assert [f.rel.as_posix() for f in res.updated] == ["readme.txt"]
    assert (dest / "readme.txt").read_bytes() == b"hello v2"
    assert [f.rel.as_posix() for f in res.local_edits] == ["notes.txt"]
    assert (dest / "notes.txt").read_bytes() == b"notes + my edit"  # untouched
    assert [f.rel.as_posix() for f in res.conflicts] == ["Data/q1_report.csv"]
    assert (dest / "Data/q1_report.csv").read_bytes() == b"a,b\nmine\n"  # refused, local kept

    drive.contents["f3"] = b"a,b\n9,9\n"
    forced = gdrive.pull("root", dest, force=True)
    assert [f.rel.as_posix() for f in forced.updated] == ["Data/q1_report.csv"]
    assert (dest / "Data/q1_report.csv").read_bytes() == b"a,b\n9,9\n"  # Drive won


def test_pull_adopts_identical_untracked_files_and_refuses_different_ones(drive, tmp_path):
    # a dir that predates the manifest (or lost it): matching bytes are adopted silently,
    # differing bytes are a conflict — we can't know which side is newer
    dest = tmp_path / "acme"
    dest.mkdir()
    (dest / "readme.txt").write_bytes(b"hello")
    (dest / "notes.txt").write_bytes(b"something else")
    res = gdrive.pull("root", dest)
    assert "readme.txt" in [f.rel.as_posix() for f in res.skipped]
    assert [f.rel.as_posix() for f in res.conflicts] == ["notes.txt"]
    man = json.loads((dest / gdrive.MANIFEST).read_text())
    assert "readme.txt" in man["files"] and "notes.txt" not in man["files"]


def test_pull_reports_local_only_and_never_deletes(drive, tmp_path):
    dest = tmp_path / "acme"
    gdrive.pull("root", dest)
    (dest / "scratch.md").write_text("mine")
    res = gdrive.pull("root", dest)
    assert res.local_only == [Path("scratch.md")]
    assert (dest / "scratch.md").exists()


# -- push --


@pytest.fixture
def pushed(drive, tmp_path, monkeypatch):
    """A pulled mirror plus an upload stub that records calls and answers like Drive would."""
    dest = tmp_path / "acme"
    gdrive.pull("root", dest)
    uploads: list[dict] = []

    def fake_upload(svc, path, *, parent=None, file_id=None):
        data = path.read_bytes()
        uploads.append({"path": path.relative_to(dest).as_posix(), "parent": parent, "file_id": file_id})
        return {
            "id": file_id or f"up-{len(uploads)}",
            "name": path.name,
            "mimeType": "text/plain",
            "modifiedTime": T2,
            "size": str(len(data)),
            "md5Checksum": _md5(data),
        }

    monkeypatch.setattr(gdrive, "_upload", fake_upload)
    drive.uploads = uploads
    return dest


def test_push_nothing_changed_uploads_nothing(drive, pushed):
    res = gdrive.push(pushed)
    assert not res.created and not res.updated and len(res.skipped) == 3
    assert res.native == [Path("Brief.docx")]  # exported docs never go back up
    assert drive.uploads == []


def test_push_updates_in_place_and_creates_new_with_subfolders(drive, pushed):
    (pushed / "notes.txt").write_bytes(b"notes v2")
    (pushed / "Data/q2").mkdir()
    (pushed / "Data/q2/new.csv").write_bytes(b"x")
    res = gdrive.push(pushed)
    assert res.updated == [Path("notes.txt")] and res.created == [Path("Data/q2/new.csv")]
    by_path = {u["path"]: u for u in drive.uploads}
    assert by_path["notes.txt"]["file_id"] == "f2"  # same id → sharing/links survive
    assert by_path["Data/q2/new.csv"]["parent"] == "new-1"  # q2 was created under Data
    assert drive.created == [{"name": "q2", "mimeType": FOLDER, "parents": ["sub"]}]
    man = json.loads((pushed / gdrive.MANIFEST).read_text())
    assert man["files"]["notes.txt"]["remote_md5"] == _md5(b"notes v2")
    assert man["files"]["Data/q2/new.csv"]["id"] == "up-1"  # creates go first
    # and the mirror is now clean: a second push is a no-op
    assert not gdrive.push(pushed).updated


def test_push_conflict_is_all_or_nothing(drive, pushed):
    (pushed / "notes.txt").write_bytes(b"notes v2")  # clean local edit
    drive.tree["root"][0] = _bin("f1", "readme.txt", b"hello v2", T2)  # remote edit
    (pushed / "readme.txt").write_bytes(b"hello mine")  # ...and a local one → conflict
    res = gdrive.push(pushed)
    assert res.conflicts == [Path("readme.txt")] and res.updated == [Path("notes.txt")]
    assert drive.uploads == []  # the clean edit did NOT go up either
    forced = gdrive.push(pushed, force=True)
    assert sorted(u["path"] for u in drive.uploads) == ["notes.txt", "readme.txt"]
    assert forced.updated == [Path("notes.txt"), Path("readme.txt")]


def test_push_remote_only_change_says_pull_first(drive, pushed):
    drive.tree["root"][0] = _bin("f1", "readme.txt", b"hello v2", T2)
    res = gdrive.push(pushed)
    assert res.remote_edits == [Path("readme.txt")] and drive.uploads == []


def test_push_file_removed_on_drive_is_a_conflict_not_a_resurrection(drive, pushed):
    drive.tree["root"] = [r for r in drive.tree["root"] if r["id"] != "f1"]
    res = gdrive.push(pushed)
    assert res.conflicts == [Path("readme.txt")] and drive.uploads == []
    forced = gdrive.push(pushed, force=True)
    assert forced.created == [Path("readme.txt")]


def test_push_dry_run_uploads_nothing(drive, pushed):
    (pushed / "notes.txt").write_bytes(b"notes v2")
    res = gdrive.push(pushed, dry_run=True)
    assert res.updated == [Path("notes.txt")] and drive.uploads == []


def test_push_needs_a_folder_when_there_is_no_manifest(drive, tmp_path):
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    (fresh / "a.txt").write_bytes(b"a")
    with pytest.raises(gdrive.DriveError, match="name the Drive folder"):
        gdrive.push(fresh)
    res = gdrive.push(fresh, "root", dry_run=True)
    assert res.created == [Path("a.txt")]


def test_pull_refuses_a_folder_that_disagrees_with_the_manifest(drive, pushed):
    # pulling folder B into a dir pulled from A would diff B against A's base — refuse
    drive.tree["other"] = [_bin("o1", "other.txt", b"o")]
    drive.contents["o1"] = b"o"
    with pytest.raises(gdrive.DriveError, match="was pulled from folder root"):
        gdrive.pull("other", pushed)
    res = gdrive.pull("other", pushed, force=True)
    assert [f.rel.as_posix() for f in res.added] == ["other.txt"]
    assert json.loads((pushed / gdrive.MANIFEST).read_text())["folder"] == "other"


def test_push_refuses_a_folder_that_disagrees_with_the_manifest(drive, pushed):
    drive.tree["other"] = []
    with pytest.raises(gdrive.DriveError, match="was pulled from folder root"):
        gdrive.push(pushed, "other")


def test_local_changed_fast_path_skips_the_hash_when_size_and_mtime_match(tmp_path, monkeypatch):
    p = tmp_path / "f"
    p.write_bytes(b"abc")
    mod = datetime.fromisoformat(T1)
    gdrive._stamp(p, mod)
    entry = gdrive.Entry(id="x", modified=mod.isoformat(), remote_md5=None, md5=_md5(b"abc"), size=3)
    monkeypatch.setattr(gdrive, "_md5", lambda path: pytest.fail("hashed on the fast path"))
    assert gdrive._local_changed(p, entry) is False
    os.utime(p, (mod.timestamp() + 60, mod.timestamp() + 60))  # touched, same bytes
    monkeypatch.setattr(gdrive, "_md5", lambda path: _md5(b"abc"))
    assert gdrive._local_changed(p, entry) is False
    p.write_bytes(b"abd")
    monkeypatch.setattr(gdrive, "_md5", lambda path: _md5(b"abd"))
    assert gdrive._local_changed(p, entry) is True


# -- cli --


def test_cli_push_without_tty_or_yes_exits_2(drive, pushed, monkeypatch):
    from ko.cli import app

    (pushed / "notes.txt").write_bytes(b"notes v2")
    monkeypatch.setattr("builtins.open", lambda *a, **k: (_ for _ in ()).throw(OSError("no tty")))
    r = CliRunner().invoke(app, ["drive", "push", str(pushed)])
    assert r.exit_code == 2, r.output
    assert drive.uploads == []


def test_cli_pull_json_and_conflict_exit(drive, tmp_path):
    from ko.cli import app

    dest = tmp_path / "acme"
    r = CliRunner().invoke(app, ["drive", "pull", "root", str(dest), "--json"])
    assert r.exit_code == 0, r.output
    out = json.loads(r.stdout)
    assert out["added"] == ["Brief.docx", "Data/q1_report.csv", "notes.txt", "readme.txt"]
    # re-sync by dir alone, with a conflict → exit 1 and the row names it
    drive.tree["root"][0] = _bin("f1", "readme.txt", b"hello v2", T2)
    (dest / "readme.txt").write_bytes(b"mine")
    r = CliRunner().invoke(app, ["drive", "pull", str(dest)])
    assert r.exit_code == 1
    assert "conflict\treadme.txt" in r.stdout


def test_cli_ls_json_keeps_notes_on_stderr(drive):
    from ko.cli import app

    r = CliRunner().invoke(app, ["drive", "ls", "root", "--json"])
    assert r.exit_code == 0 and len(json.loads(r.stdout)) == 4
    assert "skipped Link" in r.stderr  # the shortcut note survives --json
    drive.tree["empty"] = []
    r = CliRunner().invoke(app, ["drive", "ls", "empty", "--json"])
    assert r.exit_code == 0 and r.stdout.strip() == "[]" and "empty" in r.stderr
