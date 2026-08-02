"""Offline tests for ko.llm — no model calls (plus one live smoke test, skipped without a key)."""

import json
import os

import pytest
from typer.testing import CliRunner

from ko import llm
from ko.cli import app

runner = CliRunner()


def test_default_model_env_override(monkeypatch):
    monkeypatch.setenv("KO_DEFAULT_MODEL", "openrouter:anthropic/claude-sonnet-4")
    assert llm.default_model() == "openrouter:anthropic/claude-sonnet-4"
    monkeypatch.delenv("KO_DEFAULT_MODEL")
    assert llm.default_model() == llm.FALLBACK_MODEL


def test_available_models_filters_by_env_keys(monkeypatch):
    for var in set(llm.PROVIDER_KEYS.values()):
        monkeypatch.delenv(var, raising=False)
    assert llm.available_models() == []
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    models = llm.available_models()
    assert models and all(m.startswith("google:") for m in models)
    assert llm.available_models("google:gemini")  # prefix filtering works


def test_openrouter_cache_surfaces_only_with_key(monkeypatch, tmp_path):
    monkeypatch.setenv("KO_CACHE_DIR", str(tmp_path))
    for var in set(llm.PROVIDER_KEYS.values()):
        monkeypatch.delenv(var, raising=False)
    assert llm.cached_openrouter_models() == []  # no cache yet
    (tmp_path / "openrouter_models.json").write_text(
        '{"models": ["openrouter:anthropic/claude-sonnet-4", "openrouter:openai/gpt-5"]}'
    )
    assert len(llm.cached_openrouter_models()) == 2
    # cached catalog is hidden until OPENROUTER_API_KEY is set
    assert not any(m.startswith("openrouter:") for m in llm.available_models())
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    assert llm.available_models("openrouter:") == [
        "openrouter:anthropic/claude-sonnet-4",
        "openrouter:openai/gpt-5",
    ]


def test_refresh_openrouter_noop_without_key(monkeypatch, tmp_path):
    monkeypatch.setenv("KO_CACHE_DIR", str(tmp_path))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert llm.refresh_openrouter_models() == []  # no key → no network, no cache
    assert not (tmp_path / "openrouter_models.json").exists()


def test_refresh_force_bypasses_ttl(monkeypatch, tmp_path):
    import httpx

    monkeypatch.setenv("KO_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    (tmp_path / "openrouter_models.json").write_text('{"models": ["openrouter:old/model"]}')

    calls = {"n": 0}

    class FakeResp:
        def json(self):
            return {"data": [{"id": "new/model"}]}

    def fake_get(url, timeout=0):
        calls["n"] += 1
        return FakeResp()

    monkeypatch.setattr(httpx, "get", fake_get)

    # fresh cache → no fetch without force
    assert llm.refresh_openrouter_models() == ["openrouter:old/model"]
    assert calls["n"] == 0
    # force re-fetches despite a fresh cache
    assert llm.refresh_openrouter_models(force=True) == ["openrouter:new/model"]
    assert calls["n"] == 1


def test_refresh_records_network_failure(monkeypatch, tmp_path):
    import httpx

    monkeypatch.setenv("KO_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    (tmp_path / "openrouter_models.json").write_text('{"models": ["openrouter:old/model"]}')

    def boom(url, timeout=0):
        raise httpx.ConnectError("no network")

    monkeypatch.setattr(httpx, "get", boom)
    # a forced refresh that can't reach OpenRouter serves the old cache and records the miss,
    # instead of looking identical to a success (the real item-3 defect: silent failure)
    assert llm.refresh_openrouter_models(force=True) == ["openrouter:old/model"]
    assert llm.last_refresh_hit_network is False


def test_cache_age_none_when_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("KO_CACHE_DIR", str(tmp_path))
    assert llm.openrouter_cache_age() is None


def test_models_cli_warns_when_stale(monkeypatch, tmp_path):
    import os as _os
    import time as _time

    monkeypatch.setenv("KO_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    cache = tmp_path / "openrouter_models.json"
    cache.write_text('{"models": ["openrouter:x/y"]}')
    old = _time.time() - 10 * 86400  # 10 days ago — past the 7-day staleness threshold
    _os.utime(cache, (old, old))
    # within-TTL check would refetch a >TTL cache; keep it offline so it falls back to cache
    monkeypatch.setattr(llm, "refresh_openrouter_models", lambda *a, **k: None)
    r = runner.invoke(app, ["models"])
    assert r.exit_code == 0
    assert "cached 10 days ago" in r.stderr
    assert "ko models --refresh" in r.stderr


# --- --schema: JSON Schema constrained output --------------------------------


def test_load_schema_rejects_bad_json(tmp_path):
    f = tmp_path / "s.json"
    f.write_text("{not json")
    with pytest.raises(ValueError, match="not valid JSON"):
        llm.load_schema(f)


def test_load_schema_rejects_non_object_schema(tmp_path):
    f = tmp_path / "s.json"
    f.write_text('{"type": "string"}')  # a valid JSON object, but not an *object* schema
    with pytest.raises(ValueError):
        llm.load_schema(f)


def test_load_schema_accepts_object_schema(tmp_path):
    f = tmp_path / "s.json"
    schema = {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}
    f.write_text(json.dumps(schema))
    assert llm.load_schema(f) == schema


def test_cli_schema_bad_file_exits_2(tmp_path):
    f = tmp_path / "s.json"
    f.write_text('["a list is not an object schema"]')
    r = runner.invoke(app, ["llm", "--schema", str(f), "extract"])
    assert r.exit_code == 2  # usage error, before any model call


@pytest.mark.skipif(
    not os.environ.get("OPENROUTER_API_KEY"),
    reason="OPENROUTER_API_KEY not set; skipping live --schema smoke test",
)
def test_cli_schema_live_smoke(tmp_path):
    # tiny live call on the basic tier: a one-field object schema → valid JSON on stdout
    f = tmp_path / "s.json"
    f.write_text(
        json.dumps(
            {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]}
        )
    )
    r = runner.invoke(app, ["llm", "--schema", str(f), "Return the word hello in the answer field"])
    assert r.exit_code == 0
    obj = json.loads(r.stdout)  # stdout is valid JSON matching the schema
    assert "answer" in obj and isinstance(obj["answer"], str)
