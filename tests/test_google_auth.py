"""Offline tests for the shared Google-API helpers (errors, http mapping, id extraction)."""

from __future__ import annotations

import pytest

from ko import google_auth as g


class _Resp:
    def __init__(self, status):
        self.status = status


class _FakeHttpError(Exception):
    def __init__(self, status):
        self.resp = _Resp(status)


def test_id_from_url_both_kinds_and_bare():
    assert g.id_from_url("https://docs.google.com/spreadsheets/d/AB-1_2/edit#gid=0", "spreadsheets") == "AB-1_2"
    assert g.id_from_url("https://docs.google.com/document/d/XY_9/edit", "document") == "XY_9"
    assert g.id_from_url("https://drive.google.com/drive/folders/F-1_2/x", "folder") == "F-1_2"
    assert g.id_from_url("  BARE_id  ", "spreadsheets") == "BARE_id"  # bare id, trimmed


class _FakeCreds:
    """A stand-in for a loaded, non-expired Credentials with a known granted-scope set."""
    def __init__(self, scopes):
        self.scopes = scopes
        self.valid = True
        self.expired = False
        self.refresh_token = "r"


def _stub_load(monkeypatch, tmp_path, scopes):
    """Point _load_cached at an existing token file, and make the parser return _FakeCreds(scopes)."""
    monkeypatch.setenv("KO_STATE_DIR", str(tmp_path))
    (tmp_path / "google_token.json").write_text("{}")  # presence check only; parsing is stubbed
    monkeypatch.setattr(g.Credentials, "from_authorized_user_file", lambda *a, **k: _FakeCreds(scopes))


def test_readwrite_token_serves_readonly_reads(monkeypatch, tmp_path):
    """Regression: a read+write token must satisfy a readonly request WITHOUT re-auth.
    (Loading it under a narrower scope set used to fail refresh and pop a browser — the hang.)"""
    _stub_load(monkeypatch, tmp_path, g.SCOPES_READWRITE)
    assert g._load_cached(readonly=True, account=g.DEFAULT_ACCOUNT) is not None
    assert g._load_cached(readonly=False, account=g.DEFAULT_ACCOUNT) is not None


def test_readonly_token_rejected_for_write(monkeypatch, tmp_path):
    """A readonly-only token can't serve a write op — returns None so the caller re-auths to upgrade."""
    _stub_load(monkeypatch, tmp_path, g.SCOPES_READONLY)
    assert g._load_cached(readonly=True, account=g.DEFAULT_ACCOUNT) is not None  # reads fine
    assert g._load_cached(readonly=False, account=g.DEFAULT_ACCOUNT) is None  # write -> upgrade


def test_only_narrow_drive_scope_is_requested():
    """The privacy contract: drive.file and NOTHING broader. Guards against a future widen."""
    for scopes in (g.SCOPES_READONLY, g.SCOPES_READWRITE):
        drive = [s for s in scopes if "drive" in s]
        assert drive == ["https://www.googleapis.com/auth/drive.file"], drive


def test_raise_for_status_maps_status():
    nf, pd = g.GoogleNotFound, g.GooglePermissionDenied
    with pytest.raises(pd):
        g.raise_for_status(_FakeHttpError(403), "x", not_found=nf, permission=pd, hint="h")
    with pytest.raises(nf):
        g.raise_for_status(_FakeHttpError(404), "x", not_found=nf, permission=pd, hint="h")
    with pytest.raises(_FakeHttpError):  # any other status re-raised unchanged
        g.raise_for_status(_FakeHttpError(500), "x", not_found=nf, permission=pd, hint="h")


def test_error_hierarchy():
    assert issubclass(g.GoogleNotFound, g.GoogleError)
    assert issubclass(g.GooglePermissionDenied, g.GoogleError)
    assert issubclass(g.GoogleError, RuntimeError)
