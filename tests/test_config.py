"""Offline tests for config.setting()/setting_source() and telemetry gating."""

from __future__ import annotations

from ko import config, telemetry


def _with_config(monkeypatch, tmp_path, toml: str):
    """Point config at a temp config.toml and clear its cache."""
    (tmp_path / "config.toml").write_text(toml)
    monkeypatch.setenv("KO_CONFIG_DIR", str(tmp_path))
    config._data.cache_clear()


def test_setting_env_wins(monkeypatch, tmp_path):
    _with_config(monkeypatch, tmp_path, '[llm]\nmodel = "config:model"\n')
    monkeypatch.setenv("KO_DEFAULT_MODEL", "env:model")
    assert config.setting("KO_DEFAULT_MODEL", "llm", "model", "fallback") == "env:model"
    assert config.setting_source("KO_DEFAULT_MODEL", "llm", "model") == "env"
    config._data.cache_clear()


def test_setting_config_over_default(monkeypatch, tmp_path):
    _with_config(monkeypatch, tmp_path, '[llm]\nmodel = "config:model"\n')
    monkeypatch.delenv("KO_DEFAULT_MODEL", raising=False)
    assert config.setting("KO_DEFAULT_MODEL", "llm", "model", "fallback") == "config:model"
    assert config.setting_source("KO_DEFAULT_MODEL", "llm", "model") == "config"
    config._data.cache_clear()


def test_setting_falls_back_to_default(monkeypatch, tmp_path):
    _with_config(monkeypatch, tmp_path, "")
    monkeypatch.delenv("KO_DEFAULT_MODEL", raising=False)
    assert config.setting("KO_DEFAULT_MODEL", "llm", "model", "fallback") == "fallback"
    assert config.setting_source("KO_DEFAULT_MODEL", "llm", "model") == "default"
    config._data.cache_clear()


# --- telemetry gating (never touches OTel when off) ---


def test_telemetry_off_by_default(monkeypatch, tmp_path):
    _with_config(monkeypatch, tmp_path, "")
    monkeypatch.setattr(telemetry, "_active", False)
    monkeypatch.setattr(telemetry, "_instrument", _boom)
    assert telemetry.setup() is False
    config._data.cache_clear()


def test_telemetry_enabled_but_no_key(monkeypatch, tmp_path):
    _with_config(monkeypatch, tmp_path, "[telemetry]\nenabled = true\n")
    monkeypatch.delenv("POSTHOG_API_KEY", raising=False)
    monkeypatch.setattr(telemetry, "_active", False)
    monkeypatch.setattr(telemetry, "_instrument", _boom)
    assert telemetry.setup() is False
    config._data.cache_clear()


def test_telemetry_enabled_with_key_instruments(monkeypatch, tmp_path):
    _with_config(monkeypatch, tmp_path, "[telemetry]\nenabled = true\n")
    monkeypatch.setenv("POSTHOG_API_KEY", "phc_test")
    monkeypatch.setattr(telemetry, "_active", False)
    calls = []
    monkeypatch.setattr(
        telemetry, "_instrument", lambda key, host, inc: calls.append((key, host, inc))
    )
    assert telemetry.setup() is True
    assert calls == [("phc_test", "https://us.i.posthog.com", False)]
    # idempotent — second call doesn't re-instrument
    assert telemetry.setup() is True
    assert len(calls) == 1
    config._data.cache_clear()


def test_telemetry_include_content_opt_in(monkeypatch, tmp_path):
    _with_config(
        monkeypatch, tmp_path, "[telemetry]\nenabled = true\ninclude_content = true\n"
    )
    monkeypatch.setenv("POSTHOG_API_KEY", "phc_test")
    monkeypatch.setattr(telemetry, "_active", False)
    calls = []
    monkeypatch.setattr(
        telemetry, "_instrument", lambda key, host, inc: calls.append((key, host, inc))
    )
    telemetry.setup()
    assert calls[0][2] is True
    config._data.cache_clear()


def _boom(*a, **k):
    raise AssertionError("_instrument must not be called when telemetry is gated off")
