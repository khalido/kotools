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
    logs.add(error="SheetsError")
    logs.command_event("gsheets get", 50, 1)
    events = logs.recent(10)
    assert len(events) == 2
    err = next(e for e in events if e["level"] == "error")
    assert err["cmd"] == "gsheets get" and err["exit_code"] == 1 and err["error"] == "SheetsError"
    ok = next(e for e in events if e["level"] == "info")
    assert ok["cmd"] == "exa search" and ok["exit_code"] == 0
    assert "ko_version" in ok and "host" in ok and ok["event"] == "command"


def test_errors_only_filter(isolated):
    logs = isolated
    logs.command_event("a", 1, 0)
    logs.command_event("b", 1, 2)  # usage -> warning, still a failure
    logs.command_event("c", 1, 1)
    assert [e["cmd"] for e in logs.recent(10, errors_only=True)] == ["c", "b"]
    assert [e["level"] for e in logs.recent(10, errors_only=True)] == ["error", "warning"]


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


def test_wide_event_carries_added_fields_once(isolated):
    logs = isolated
    logs.reset()
    logs.add(argv="exa search rust", model="m1", skipped=None)
    logs.inc(input_tokens=10, cost_usd=0.001)
    logs.inc(input_tokens=5, cost_usd=0.002, missing=None)
    logs.add(event="clash", level="clash")  # names log_event owns are dropped, not a crash
    logs.command_event("exa search", 7, 0)
    [e] = logs.recent(10)
    assert e["argv"] == "exa search rust" and e["model"] == "m1"
    assert e["input_tokens"] == 15 and abs(e["cost_usd"] - 0.003) < 1e-12
    assert "skipped" not in e and "missing" not in e
    assert e["event"] == "command" and e["level"] == "info"


def test_long_strings_cut_visibly_and_objects_become_str(isolated):
    logs = isolated
    logs.reset()
    logs.add(error_msg="x" * 500, obj={"a": 1})
    assert len(logs.get("error_msg")) == logs.MAX_STR and logs.get("error_msg").endswith("…")
    assert logs.get("obj") == "{'a': 1}"


def test_run_cost_adds_llm_fields(isolated):
    from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
    from pydantic_ai.usage import RequestUsage

    from ko import llm

    logs = isolated
    logs.reset()
    msgs = [
        ModelResponse(parts=[ToolCallPart("search", {})], model_name="m", provider_details={"cost": 0.01},
                      usage=RequestUsage(input_tokens=100, output_tokens=10)),
        ModelResponse(parts=[TextPart("hi")], model_name="m", provider_details={"cost": 0.02},
                      usage=RequestUsage(input_tokens=50, output_tokens=5)),
    ]
    llm.run_cost(msgs)
    llm.run_cost(msgs[1:])  # a second run in the same command sums on
    assert logs.get("llm_requests") == 3 and logs.get("tool_calls") == 1
    assert logs.get("input_tokens") == 200 and logs.get("cost_source") == "actual"
    assert abs(logs.get("cost_usd") - 0.05) < 1e-12


def test_credentials_in_strings_are_redacted(isolated):
    logs = isolated
    logs.add(
        error_msg="HttpError 403 when requesting https://youtube.googleapis.com/v3/videos?id=x&key=AIzaSECRET returned",
        argv="fetch 'https://s3.example.com/f.pdf?X-Amz-Signature=abc123&x=1'",
        other="401: Authorization: Bearer sk-live.abc/def=",
    )
    assert "AIzaSECRET" not in logs.get("error_msg") and "&key=[redacted] returned" in logs.get("error_msg")
    assert "abc123" not in logs.get("argv") and "&x=1" in logs.get("argv")
    assert logs.get("other").endswith("Bearer [redacted]")
