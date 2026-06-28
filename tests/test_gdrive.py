"""Offline tests for ko.gdrive pure helpers (no live Drive auth)."""

from __future__ import annotations

from ko import gdrive


def test_md_bytes_roundtrips_utf8():
    assert gdrive._md_bytes("# Hi\n— café") == "# Hi\n— café".encode()


def test_doc_and_folder_id_extraction():
    assert gdrive._doc_id("https://docs.google.com/document/d/ABC123/edit") == "ABC123"
    assert gdrive._doc_id("ABC123") == "ABC123"  # bare id passes through
    assert gdrive._folder_id("https://drive.google.com/drive/folders/FID_9/abc") == "FID_9"
    assert gdrive._folder_id("FID_9") == "FID_9"


def test_flatten_comment_full():
    raw = {
        "id": "c1",
        "author": {"displayName": "Ann"},
        "createdTime": "2026-06-28T10:00:00Z",
        "quotedFileContent": {"value": "the pricing line"},
        "content": "can we discount this?",
        "resolved": False,
        "replies": [
            {"author": {"displayName": "Ben"}, "createdTime": "2026-06-28T11:00:00Z", "content": "yes, 10%"},
        ],
    }
    out = gdrive._flatten_comment(raw)
    assert out == {
        "id": "c1",
        "author": "Ann",
        "created_time": "2026-06-28T10:00:00Z",
        "quoted_text": "the pricing line",
        "content": "can we discount this?",
        "resolved": False,
        "replies": [
            {"author": "Ben", "created_time": "2026-06-28T11:00:00Z", "content": "yes, 10%"},
        ],
    }


def test_flatten_comment_tolerates_missing_fields():
    # No author, no quote, no replies — Drive omits keys rather than nulling them.
    out = gdrive._flatten_comment({"id": "c2", "content": "note", "resolved": True})
    assert out["author"] == "" and out["quoted_text"] == "" and out["replies"] == []
    assert out["resolved"] is True


def test_error_hierarchy():
    assert issubclass(gdrive.DriveNotFound, gdrive.DriveError)
    assert issubclass(gdrive.DrivePermissionDenied, gdrive.DriveError)
