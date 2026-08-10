"""Offline tests for the shared Google-API helpers (errors, http mapping, id extraction)."""

from __future__ import annotations

import json

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


def _sa_key(tmp_path, email="bot@proj.iam.gserviceaccount.com"):
    p = tmp_path / "key.json"
    p.write_text(json.dumps({"type": "service_account", "client_email": email, "private_key": "PEM"}))
    return p


def test_register_service_account_copies_key_and_returns_email(monkeypatch, tmp_path):
    monkeypatch.setenv("KO_STATE_DIR", str(tmp_path / "state"))
    email = g.register_service_account(_sa_key(tmp_path), "sfl-app")
    assert email == "bot@proj.iam.gserviceaccount.com"
    dest = tmp_path / "state" / "google_sa_sfl-app.json"
    # State, NOT the dotfile-synced config dir: an SA key authenticates by itself anywhere.
    assert dest.exists() and not (tmp_path / "cfg" / "google_sa_sfl-app.json").exists()
    assert (dest.stat().st_mode & 0o777) == 0o600  # owner-only from creation, no chmod window
    assert g.list_accounts() == ["sfl-app"]


def test_register_service_account_rejects_non_sa_json(monkeypatch, tmp_path):
    monkeypatch.setenv("KO_STATE_DIR", str(tmp_path / "state"))
    bad = tmp_path / "oauth_client.json"  # the classic mixup: an OAuth *client* file
    bad.write_text(json.dumps({"installed": {"client_id": "x"}}))
    with pytest.raises(g.AuthError):
        g.register_service_account(bad, "x")
    assert not (tmp_path / "state" / "google_sa_x.json").exists()


def test_logout_removes_the_sa_key_too(monkeypatch, tmp_path):
    """Leaving the key behind would report 'signed out' on an account that still authenticates."""
    monkeypatch.setenv("KO_STATE_DIR", str(tmp_path / "state"))
    g.register_service_account(_sa_key(tmp_path), "bot")
    assert g.logout("bot") is True
    assert not g.sa_file("bot").exists()
    assert g.logout("bot") is False  # nothing left to remove


def test_get_credentials_prefers_sa_profile(monkeypatch, tmp_path):
    """A registered key wins over the OAuth token path — no browser, scopes still per-mode."""
    monkeypatch.setenv("KO_STATE_DIR", str(tmp_path / "state"))
    g.register_service_account(_sa_key(tmp_path), "bot")
    seen = {}

    def fake_from_file(path, scopes=None):
        seen["path"], seen["scopes"] = path, scopes
        return _FakeCreds(scopes)

    monkeypatch.setattr(g.service_account.Credentials, "from_service_account_file", fake_from_file)
    g.get_credentials.cache_clear()
    g.get_credentials(readonly=True, account="bot")
    assert seen["path"].endswith("google_sa_bot.json")
    assert seen["scopes"] == g.SCOPES_READONLY
    g.get_credentials.cache_clear()
    g.get_credentials(readonly=False, account="bot")
    assert seen["scopes"] == g.SCOPES_READWRITE
    g.get_credentials.cache_clear()


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


def test_cli_registers_and_unregisters_a_service_account(monkeypatch, tmp_path):
    """End-to-end through the CLI: register, appear in `accounts`, unregister via --logout."""
    from typer.testing import CliRunner

    from ko.cli import app

    monkeypatch.setenv("KO_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("KO_CONFIG_DIR", str(tmp_path / "cfg"))
    runner = CliRunner()
    key = _sa_key(tmp_path)

    r = runner.invoke(app, ["gsheets", "-a", "bot", "auth", "--service-account", str(key)])
    assert r.exit_code == 0
    assert "bot@proj.iam.gserviceaccount.com" in r.stdout
    assert "PEM" not in r.stdout  # never echo key material

    r = runner.invoke(app, ["gsheets", "-a", "bot", "accounts"])
    assert "bot" in r.stdout and "service account" in r.stdout

    # --logout wins over a --service-account passed in the same breath, and clears the key
    r = runner.invoke(app, ["gsheets", "-a", "bot", "auth", "--logout", "--service-account", str(key)])
    assert r.exit_code == 0 and "Signed out" in r.stdout
    assert not (tmp_path / "state" / "google_sa_bot.json").exists()
