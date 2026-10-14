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


def test_drive_scope_is_broad_and_read_matches_token_kind():
    """Widened 2026-09-10 for `ko drive` (folder mirror + push): read token gets drive.readonly,
    write token gets full drive, and the per-file drive.file is gone from both."""
    ro = [s for s in g.SCOPES_READONLY if "drive" in s]
    rw = [s for s in g.SCOPES_READWRITE if "drive" in s]
    assert ro == ["https://www.googleapis.com/auth/drive.readonly"], ro
    assert rw == ["https://www.googleapis.com/auth/drive"], rw


def test_old_drive_file_token_forces_reconsent(monkeypatch, tmp_path):
    """A token granted before the widen (drive.file only) would 403 on every listing —
    _load_cached must return None so the caller re-runs consent, read or write."""
    old = [s.replace("drive.readonly", "drive.file") for s in g.SCOPES_READONLY]
    _stub_load(monkeypatch, tmp_path, old)
    assert g._load_cached(readonly=True, account=g.DEFAULT_ACCOUNT) is None
    old_rw = [s if s != "https://www.googleapis.com/auth/drive" else s + ".file" for s in g.SCOPES_READWRITE]
    _stub_load(monkeypatch, tmp_path, old_rw)
    assert g._load_cached(readonly=False, account=g.DEFAULT_ACCOUNT) is None


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
    """End-to-end through the CLI: register, appear in `accounts`, unregister via logout."""
    from typer.testing import CliRunner

    from ko.cli import app

    monkeypatch.setenv("KO_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("KO_CONFIG_DIR", str(tmp_path / "cfg"))
    runner = CliRunner()
    key = _sa_key(tmp_path)

    r = runner.invoke(app, ["google", "auth", "bot", "--service-account", str(key)])
    assert r.exit_code == 0, r.output
    assert "bot@proj.iam.gserviceaccount.com" in r.stdout
    assert "PEM" not in r.stdout  # never echo key material

    r = runner.invoke(app, ["google", "accounts"])
    assert "* bot" in r.stdout and "service account" in r.stdout  # first account → the default

    r = runner.invoke(app, ["google", "logout", "bot"])
    assert r.exit_code == 0 and "Signed out" in r.stdout
    assert not (tmp_path / "state" / "google_sa_bot.json").exists()


# --- who is signed in: scope description + account status ---


def test_describe_scopes_reads_the_grant_back():
    assert g.describe_scopes(g.SCOPES_READWRITE) == (
        "read+write: sheets, docs, calendar, gmail (read + drafts), drive · read-only: youtube"
    )
    ro = g.describe_scopes(g.SCOPES_READONLY)
    assert ro.startswith("read-only: sheets, docs, calendar, gmail, drive, youtube")
    assert "re-consent" in ro  # a read-only token can't write: say so up front
    # a pre-widen token: per-file drive, flagged as stale rather than reported as "drive"
    old = [s.replace("/drive", "/drive.file") for s in g.SCOPES_READWRITE]
    assert "drive: per-file only" in g.describe_scopes(old) and "re-consent" in g.describe_scopes(old)
    assert "missing: drive" not in g.describe_scopes(old)
    # a token missing an API entirely
    partial = [s for s in g.SCOPES_READWRITE if "gmail" not in s]
    assert "missing: gmail" in g.describe_scopes(partial)
    # a write token from before YouTube was added: still writes, says how to add the new API
    pre_yt = [s for s in g.SCOPES_READWRITE if "youtube" not in s]
    assert g.describe_scopes(pre_yt).endswith("missing: youtube · re-consent with `ko google auth` to add them")
    assert g.describe_scopes([]).startswith("missing: sheets, docs, calendar, gmail, drive, youtube")


def test_account_status_reads_local_files_only(monkeypatch, tmp_path):
    """The listing must never start a consent flow: a stashed email is used as-is, a token
    without one goes through account_email (which refuses to run the browser)."""
    monkeypatch.setenv("KO_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(g, "_run_flow", lambda *a, **k: pytest.fail("browser flow started"))
    (tmp_path / "google_token_work.json").write_text(
        json.dumps({"scopes": g.SCOPES_READONLY, "email": "ko@example.com", "refresh_token": "r"})
    )
    st = g.account_status("work")
    assert (st.kind, st.email, st.scopes) == ("oauth", "ko@example.com", g.SCOPES_READONLY)
    assert st.path == tmp_path / "google_token_work.json"
    assert g.account_status("nobody").kind == "none"
    # no stashed email + unloadable token → None, no browser, nothing written
    (tmp_path / "google_token_old.json").write_text(json.dumps({"scopes": []}))
    assert g.account_status("old").email is None
    assert "email" not in json.loads((tmp_path / "google_token_old.json").read_text())


def test_account_email_learns_and_stashes_from_the_api(monkeypatch, tmp_path):
    monkeypatch.setenv("KO_STATE_DIR", str(tmp_path))
    tf = tmp_path / "google_token_work.json"
    tf.write_text(json.dumps({"scopes": g.SCOPES_READWRITE, "refresh_token": "r"}))
    monkeypatch.setattr(g, "_load_cached", lambda readonly, account: object())
    monkeypatch.setattr(g, "_lookup_email", lambda creds: "ko@example.com")
    assert g.account_email("work") == "ko@example.com"
    assert json.loads(tf.read_text())["email"] == "ko@example.com"  # stashed for next time
    monkeypatch.setattr(g, "_lookup_email", lambda creds: pytest.fail("looked up twice"))
    assert g.account_email("work") == "ko@example.com"


def test_cli_accounts_lists_address_and_grant(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from ko.cli import app

    monkeypatch.setenv("KO_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("KO_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("KO_GOOGLE_ACCOUNT", "work")
    (tmp_path / "google_token_work.json").write_text(
        json.dumps({"scopes": g.SCOPES_READONLY, "email": "ko@example.com"})
    )
    (tmp_path / "google_sa_bot.json").write_text(
        json.dumps({"type": "service_account", "client_email": "bot@proj.iam.gserviceaccount.com"})
    )
    r = CliRunner().invoke(app, ["google", "accounts"])
    assert r.exit_code == 0, r.output
    lines = r.stdout.splitlines()  # sorted by name; `*` marks the active one
    assert lines[0] == "  bot  bot@proj.iam.gserviceaccount.com  (service account)"
    assert "read+write on whatever is shared" in lines[1]
    i = lines.index("* work  ko@example.com")
    assert lines[i + 1].strip().startswith("read-only: sheets, docs, calendar, gmail, drive")


# --- no silent hangs: an implicit consent flow refuses without a terminal ---

_REAL_RUN_FLOW = g._run_flow  # captured at import, before conftest's autouse stub replaces it


def _flow_stub(monkeypatch, calls):
    class _Flow:
        @classmethod
        def from_client_secrets_file(cls, path, scopes):
            calls.append(scopes)
            return cls()

        def run_local_server(self, port=0):
            return _FakeCreds(g.SCOPES_READWRITE)

    monkeypatch.setattr(g, "InstalledAppFlow", _Flow)
    monkeypatch.setattr(g, "_write_token", lambda tf, creds: None)
    monkeypatch.setattr(g, "account_email", lambda account: None)
    monkeypatch.setattr(g, "client_file", lambda account=None: __import__("pathlib").Path(__file__))


@pytest.mark.parametrize(
    "token,readonly,reason",
    [
        (None, True, "isn't signed in"),
        (g.SCOPES_READONLY, False, "read-only grant and this is a write"),
        ([s.replace("drive.readonly", "drive.file") for s in g.SCOPES_READONLY], True, "pre-2026-09 token"),
        (g.SCOPES_READWRITE, False, "expired token that couldn't refresh"),
    ],
)
def test_implicit_flow_fails_fast_without_a_tty(monkeypatch, tmp_path, token, readonly, reason):
    """An agent (no /dev/tty) running a write on a read-only token used to hang for minutes on
    a browser callback nobody would click. Now: a one-line AuthError naming the fix."""
    monkeypatch.setenv("KO_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("KO_GOOGLE_ACCOUNT", g.DEFAULT_ACCOUNT)
    monkeypatch.setattr(g, "_has_tty", lambda: False)
    calls = []
    _flow_stub(monkeypatch, calls)
    if token is not None:
        (tmp_path / "google_token.json").write_text(json.dumps({"scopes": token}))
    with pytest.raises(g.AuthError, match=reason) as exc:
        _REAL_RUN_FLOW(readonly, g.DEFAULT_ACCOUNT)
    assert "ko google auth default" in str(exc.value) and calls == []


def test_implicit_flow_with_a_tty_says_why_then_runs(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("KO_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(g, "_has_tty", lambda: True)
    calls = []
    _flow_stub(monkeypatch, calls)
    (tmp_path / "google_token.json").write_text(json.dumps({"scopes": g.SCOPES_READONLY}))
    _REAL_RUN_FLOW(False, g.DEFAULT_ACCOUNT)
    assert calls == [g.SCOPES_READWRITE]
    assert "read-only grant" in capsys.readouterr().err  # the browser pop-up is explained


def test_explicit_auth_runs_the_flow_even_without_a_tty(monkeypatch, tmp_path):
    monkeypatch.setenv("KO_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(g, "_has_tty", lambda: False)
    calls = []
    _flow_stub(monkeypatch, calls)
    monkeypatch.setattr(g, "_run_flow", _REAL_RUN_FLOW)
    g.authenticate(readonly=False, account=g.DEFAULT_ACCOUNT)
    assert calls == [g.SCOPES_READWRITE]


def test_cli_write_on_readonly_token_exits_clean_not_hangs(monkeypatch, tmp_path, capsys):
    from typer.testing import CliRunner

    from ko.cli import app

    monkeypatch.setenv("KO_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("KO_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("KO_GOOGLE_ACCOUNT", "work")
    monkeypatch.setattr(g, "_has_tty", lambda: False)
    monkeypatch.setattr(g, "_run_flow", _REAL_RUN_FLOW)  # conftest stubs it; we want the real refusal
    (tmp_path / "google_token_work.json").write_text(json.dumps({"scopes": g.SCOPES_READONLY}))
    g.get_credentials.cache_clear()
    # a command whose FIRST call is the write (gdocs push reads the doc first)
    sid = "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms"  # id-shaped, so no alias lookup
    r = CliRunner().invoke(app, ["gsheets", "add-tab", sid, "Q3"])
    assert r.exit_code == 1 and "read-only grant" in r.stderr  # every Google command is netted
    # ...and through the real entry point: one line on stderr, exit 1, no traceback
    from ko.cli import main

    monkeypatch.setattr("sys.argv", ["ko", "gsheets", "add-tab", sid, "Q3"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "read-only grant" in err and "ko google auth work" in err and "Traceback" not in err


# --- ko google: names, the `use` default, rename, and the unknown-account guard ---


def _token(tmp_path, name, email):
    (tmp_path / f"google_token_{name}.json").write_text(
        json.dumps({"scopes": g.SCOPES_READWRITE, "email": email})
    )


def test_use_default_lives_in_settings_and_env_still_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("KO_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("KO_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.delenv("KO_GOOGLE_ACCOUNT", raising=False)
    monkeypatch.setattr(g.config, "get", lambda *a, **k: None)  # no real config.toml default
    _token(tmp_path, "sfl", "k@sfl.com")
    assert g.active_account() == g.DEFAULT_ACCOUNT
    g.use_account("sfl")
    assert g.active_account() == "sfl"
    assert json.loads((tmp_path / "settings.json").read_text()) == {"google": {"account": "sfl"}}
    monkeypatch.setenv("KO_GOOGLE_ACCOUNT", "ne")  # -a / env beats the stored default
    assert g.active_account() == "ne"
    with pytest.raises(g.AuthError, match="signed in: sfl"):
        g.use_account("nope")  # can't default to an account that isn't signed in


def test_unknown_account_errors_instead_of_opening_a_browser(monkeypatch, tmp_path):
    """A typo'd `-a sfll` used to start a consent flow — signing up a new account by accident."""
    monkeypatch.setenv("KO_STATE_DIR", str(tmp_path))
    _token(tmp_path, "sfl", "k@sfl.com")
    with pytest.raises(g.AuthError, match=r"no Google account named 'sfll' \(signed in: sfl\).*ko google auth sfll"):
        g.get_credentials(account="sfll")


def test_rename_moves_token_client_and_default(monkeypatch, tmp_path):
    monkeypatch.setenv("KO_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("KO_CONFIG_DIR", str(tmp_path / "cfg"))
    (tmp_path / "cfg").mkdir()
    (tmp_path / "cfg" / "google_client_work.json").write_text("{}")
    _token(tmp_path, "work", "ko@x.dev")
    _token(tmp_path, "sfl", "k@sfl.com")
    g.use_account("work")
    g.rename_account("work", "kd")
    assert g.list_accounts() == ["kd", "sfl"]
    assert (tmp_path / "cfg" / "google_client_kd.json").exists()
    assert g.active_account() == "kd" and g.account_status("kd").email == "ko@x.dev"
    with pytest.raises(g.AuthError, match="already exists"):
        g.rename_account("kd", "sfl")
    with pytest.raises(g.AuthError, match="bad account name"):
        g.rename_account("kd", "Bad Name")


def test_auth_reconsents_when_a_scope_is_missing(monkeypatch, tmp_path):
    """`ko google auth` is the advertised fix for "missing: gmail.compose" — so a cached token
    that lacks any scope ko asks for must run the flow, not be reused."""
    monkeypatch.setattr(g, "_run_flow", _REAL_RUN_FLOW)
    no_compose = [s for s in g.SCOPES_READWRITE if "compose" not in s]
    _stub_load(monkeypatch, tmp_path, no_compose)
    calls = []
    _flow_stub(monkeypatch, calls)
    g.authenticate(readonly=False, account=g.DEFAULT_ACCOUNT)
    assert calls == [g.SCOPES_READWRITE]
    calls.clear()
    _stub_load(monkeypatch, tmp_path, g.SCOPES_READWRITE)  # complete grant: reused, no browser
    g.authenticate(readonly=False, account=g.DEFAULT_ACCOUNT)
    assert calls == []


def test_rename_and_logout_keep_settings_consistent(monkeypatch, tmp_path):
    from ko import config

    monkeypatch.setenv("KO_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("KO_GOOGLE_ACCOUNT", "other")  # env set: settings must still follow
    _token(tmp_path, "work", "ko@x.dev")
    config.local_set("google", "account", "work")
    config.local_set("cal", "account", "work")
    config.local_set("cal", "calendar", "fam@g")
    g.rename_account("work", "kd")
    assert config.local_get("google", "account") == "kd" and config.local_get("cal", "account") == "kd"
    g.logout("kd")
    assert json.loads((tmp_path / "settings.json").read_text()) == {}  # no dangling default


def test_json_errors_carry_the_fix(monkeypatch, tmp_path):
    """Under --json an agent gets the fix as a field, not buried in prose."""
    from typer.testing import CliRunner

    from ko.cli import app

    monkeypatch.setenv("KO_STATE_DIR", str(tmp_path))
    _token(tmp_path, "sfl", "k@sfl.com")
    r = CliRunner().invoke(app, ["gmail", "-a", "sfll", "--json"])
    assert r.exit_code == 1
    err = json.loads(r.stderr.strip().splitlines()[-1])
    assert err == {
        "error": "no Google account named 'sfll' (signed in: sfl)",
        "code": "auth",
        "fix": "ko google auth sfll",
    }
    r = CliRunner().invoke(app, ["cal", "-a", "sfll"])  # text: the fix rides along in prose
    assert "— run `ko google auth sfll`" in r.stderr
