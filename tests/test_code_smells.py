"""The 2026-11-09 code-smell pass: shared error exit, atomic writes, gates that live with the code."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
import typer

from ko import _cli_shared, config, dirs, gmail, google_auth, logs, x


@pytest.mark.parametrize(
    "exc,code",
    [
        (config.MissingKeyError("FOO_API_KEY is not set"), "config"),
        (config.ConfigError("bad [llm] table"), "config"),
        (google_auth.AuthError("'work' isn't signed in", fix="ko google auth work"), "auth"),
        (RuntimeError("upstream 503"), "error"),
    ],
)
def test_die_from_labels_the_cause(exc, code, capsys):
    logs.reset()
    with pytest.raises(typer.Exit) as e:
        _cli_shared._die_from(exc, as_json=True)
    assert e.value.exit_code == 1
    err = json.loads(capsys.readouterr().err)
    assert err["code"] == code and logs.get("error") == code
    if code == "auth":
        assert err["fix"] == "ko google auth work"


def test_die_from_lets_our_bugs_crash():
    with pytest.raises(TypeError):
        _cli_shared._die_from(TypeError("missing 1 required positional argument"))


def test_emit_json_takes_any_shape_and_never_crashes_on_dates(capsys):
    from dataclasses import dataclass

    @dataclass
    class Row:
        day: date
        path: Path

    _cli_shared._emit_json([Row(date(2026, 11, 9), Path("/tmp/a"))])
    assert json.loads(capsys.readouterr().out) == [{"day": "2026-11-09", "path": "/tmp/a"}]
    _cli_shared._emit_json({"n": 1})
    assert json.loads(capsys.readouterr().out) == {"n": 1}


def test_write_atomic_makes_the_folder_and_leaves_no_tmp(tmp_path):
    p = tmp_path / "a" / "b.json"
    dirs.write_atomic(p, "{}")
    assert p.read_text() == "{}" and list(p.parent.iterdir()) == [p]


def test_send_draft_refuses_without_the_gate(monkeypatch):
    """The gate used to live only in the CLI; any other caller could send."""
    monkeypatch.setattr(gmail, "send_allowed", lambda: False)
    monkeypatch.setattr(gmail, "_compose_service", lambda: pytest.fail("reached the API"))
    with pytest.raises(gmail.GmailError, match="sending is disabled"):
        gmail.send_draft("d1")


def test_my_address_follows_the_account(monkeypatch):
    """The cached address used to be one per process, so `-a` showed the wrong account's."""
    addrs = {"home": "me@home", "work": "me@work"}
    current = {"a": "home"}

    class _Svc:
        def users(self):
            return self

        def getProfile(self, userId):
            return self

        def execute(self, num_retries):
            return {"emailAddress": addrs[current["a"]]}

    monkeypatch.setattr(gmail, "_PROFILE", {})
    monkeypatch.setattr(gmail, "active_account", lambda: current["a"])
    monkeypatch.setattr(gmail, "service", lambda name: _Svc())
    assert gmail.my_address() == "me@home"
    current["a"] = "work"
    assert gmail.my_address() == "me@work"


def test_x_page_size_notes_the_floor(capsys):
    assert x._page_size(2, 10) == 10
    assert "bills each; showing 2" in capsys.readouterr().err
    assert x._page_size(50, 10) == 50 and capsys.readouterr().err == ""
    assert x._page_size(500, 10) == 100


def test_brief_raises_a_missing_key_instead_of_hiding_it():
    from ko import brief

    def needs_key():
        raise config.MissingKeyError("HN_KEY is not set")

    with pytest.raises(config.MissingKeyError):
        brief._try("Hacker News", needs_key)
    assert brief._try("Hacker News", lambda: 1 / 0)[1].startswith("(Hacker News:")
