"""Offline tests for opt-in telemetry: off loads no OTel SDK, on exports spans (in memory,
no network), enabled without a key fails loud, content stays out unless opted in."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from ko import config, models, telemetry
from ko.config import MissingKeyError

_REAL_ENABLED = telemetry.enabled  # conftest switches it off per test; these tests need it
SECRET = "zebra-quokka-7731"  # a prompt word that must never reach a span by default


@pytest.fixture
def cfg(monkeypatch, tmp_path):
    """Write a temp config.toml; undo any instrumentation afterwards."""
    from pydantic_ai import Agent

    def write(toml: str):
        (tmp_path / "config.toml").write_text(toml)
        monkeypatch.setenv("KO_CONFIG_DIR", str(tmp_path))
        config._data.cache_clear()

    monkeypatch.setenv("KO_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(telemetry, "enabled", _REAL_ENABLED)
    monkeypatch.setattr(telemetry, "_provider", None)
    yield write
    Agent.instrument_all(False)
    config._data.cache_clear()


@pytest.fixture
def memory_exporter(monkeypatch):
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    exporter = InMemorySpanExporter()
    monkeypatch.setattr(telemetry, "_exporter", lambda key, host: exporter)
    return exporter


def _run_test_model(prompt: str) -> None:
    from pydantic_ai import Agent
    from pydantic_ai.models.test import TestModel

    Agent(instructions=f"system {SECRET}").run_sync(prompt, model=models.resolve(TestModel()))
    telemetry._provider.force_flush()


def _dump(spans) -> str:
    return json.dumps([{"name": s.name, "attrs": dict(s.attributes or {}),
                        "events": [dict(e.attributes or {}) for e in s.events]} for s in spans], default=str)


def test_off_loads_no_otel_sdk(tmp_path):
    """Default config: a model resolve (the telemetry hook) imports neither the SDK nor the
    exporter. pydantic_ai itself imports the OTel API, so only the heavy halves are checked."""
    code = (
        "import sys; from ko import models, telemetry; from pydantic_ai.models.test import TestModel;"
        "models.resolve(TestModel()); assert telemetry.setup() is False;"
        "print(' '.join(m for m in sys.modules if m.startswith(('opentelemetry.sdk', 'opentelemetry.exporter'))))"
    )
    env = {"KO_CONFIG_DIR": str(tmp_path), "KO_STATE_DIR": str(tmp_path), "PATH": "/usr/bin:/bin"}
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True, env=env)
    assert out.stdout.strip() == ""


def test_suite_ignores_a_real_enabled_config(monkeypatch, tmp_path):
    """The conftest guard: an enabled, keyless config.toml must not break other tests."""
    (tmp_path / "config.toml").write_text("[telemetry]\nenabled = true\n")
    monkeypatch.setenv("KO_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("POSTHOG_API_KEY", raising=False)
    config._data.cache_clear()
    assert models.resolve("test") == "test"
    config._data.cache_clear()


def test_off_emits_nothing(cfg, memory_exporter):
    cfg("")
    from pydantic_ai import Agent
    from pydantic_ai.models.test import TestModel

    Agent().run_sync("hi", model=models.resolve(TestModel()))
    assert telemetry._provider is None
    assert memory_exporter.get_finished_spans() == ()


def test_enabled_without_key_fails_loud(cfg, monkeypatch):
    cfg("[telemetry]\nenabled = true\n")
    monkeypatch.delenv("POSTHOG_API_KEY", raising=False)
    with pytest.raises(MissingKeyError, match="POSTHOG_API_KEY"):
        models.resolve("test")
    assert telemetry._provider is None


def test_on_exports_gen_ai_spans_without_content(cfg, monkeypatch, memory_exporter):
    cfg("[telemetry]\nenabled = true\n")
    monkeypatch.setenv("POSTHOG_API_KEY", "phc_test")
    _run_test_model(f"hello {SECRET}")
    spans = memory_exporter.get_finished_spans()
    assert any(k.startswith("gen_ai.") for s in spans for k in (s.attributes or {}))
    assert any("gen_ai.usage.input_tokens" in (s.attributes or {}) for s in spans)
    assert SECRET not in _dump(spans)
    assert telemetry.setup() is True  # idempotent: same provider
    assert len(spans) == len(memory_exporter.get_finished_spans())


def test_include_content_opt_in(cfg, monkeypatch, memory_exporter):
    cfg("[telemetry]\nenabled = true\ninclude_content = true\n")
    monkeypatch.setenv("POSTHOG_API_KEY", "phc_test")
    _run_test_model(f"hello {SECRET}")
    assert SECRET in _dump(memory_exporter.get_finished_spans())


def test_exporter_targets_posthog_ai_endpoint(cfg, monkeypatch):
    """The only PostHog wiring checkable offline: the signal URL as given (the exporter uses
    an explicit endpoint verbatim), project token as Bearer, a short timeout."""
    from opentelemetry.exporter.otlp.proto.http import trace_exporter

    cfg('[telemetry]\nposthog_host = "https://eu.i.posthog.com/"\n')
    monkeypatch.delenv("POSTHOG_HOST", raising=False)
    seen = {}
    monkeypatch.setattr(trace_exporter, "OTLPSpanExporter", lambda **kw: seen.update(kw))
    telemetry._exporter("phc_test", telemetry.host())
    assert seen["endpoint"] == "https://eu.i.posthog.com/i/v0/ai/otel"
    assert seen["headers"] == {"Authorization": "Bearer phc_test"}
    assert seen["timeout"] == telemetry._EXPORT_TIMEOUT_S
