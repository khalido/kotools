"""Offline tests for ko.logs — structured local logging, isolated via KO_STATE_DIR/KO_CONFIG_DIR."""

from __future__ import annotations

import pytest


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("KO_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("KO_CONFIG_DIR", str(tmp_path / "config"))  # no config.toml -> defaults
    from ko import logs

    logs._reset()  # loguru is a process-global singleton — isolate between tests
    yield logs
    logs._reset()


def test_command_event_round_trips_and_marks_errors(isolated):
    logs = isolated
    logs.command_event("exa search", 123, 0)
    logs.command_event("gsheets get", 50, 1, error="SheetsError")
    events = logs.recent(10)
    assert len(events) == 2
    err = next(e for e in events if e["level"] == "error")
    assert err["cmd"] == "gsheets get" and err["exit_code"] == 1 and err["error"] == "SheetsError"
    ok = next(e for e in events if e["level"] == "info")
    assert ok["cmd"] == "exa search" and ok["exit_code"] == 0
    assert "ko_version" in ok and ok["event"] == "command"


def test_errors_only_filter(isolated):
    logs = isolated
    logs.command_event("a", 1, 0)
    logs.command_event("b", 1, 2, error="Usage")
    assert [e["cmd"] for e in logs.recent(10, errors_only=True)] == ["b"]


def test_disabled_writes_nothing(isolated, monkeypatch):
    logs = isolated
    monkeypatch.setattr(
        logs.config, "get",
        lambda section, key, default=None: False if (section, key) == ("logs", "enabled") else default,
    )
    logs.command_event("x", 1, 0)
    assert logs.recent(10) == []


def test_logging_never_raises(isolated, monkeypatch):
    logs = isolated
    # even if writing blows up, the command must not break
    monkeypatch.setattr(logs, "log_dir", lambda: (_ for _ in ()).throw(OSError("boom")))
    logs.command_event("x", 1, 0)  # should swallow and return None
