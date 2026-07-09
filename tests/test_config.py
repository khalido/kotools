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


# --- model tiers: config.toml [llm] <tier> overrides the baked pick ---


def test_model_for_baked_defaults(monkeypatch, tmp_path):
    from ko import llm

    _with_config(monkeypatch, tmp_path, "")
    assert llm.model_for("basic") == llm.TIERS["basic"]
    assert llm.model_for("smart") == llm.TIERS["smart"]
    config._data.cache_clear()


def test_model_for_config_override(monkeypatch, tmp_path):
    from ko import llm

    _with_config(monkeypatch, tmp_path, '[llm]\nsmart = "openrouter:openai/gpt-5.4"\n')
    assert llm.model_for("smart") == "openrouter:openai/gpt-5.4"
    assert llm.model_for("basic") == llm.TIERS["basic"]  # untouched tiers keep baked picks
    config._data.cache_clear()


def test_default_model_rides_basic_tier(monkeypatch, tmp_path):
    from ko import llm

    _with_config(monkeypatch, tmp_path, '[llm]\nbasic = "test:cheap"\n')
    monkeypatch.delenv("KO_DEFAULT_MODEL", raising=False)
    assert llm.default_model() == "test:cheap"  # no [llm] model set → basic tier
    config._data.cache_clear()


# --- malformed config.toml is loud, missing is fine ---


def test_config_error_on_malformed_toml(monkeypatch, tmp_path, capsys):
    _with_config(monkeypatch, tmp_path, "[keys\nbroken = ")
    config.config_error.cache_clear()
    assert "TOML" in (config.config_error() or "") or "config.toml" in (config.config_error() or "")
    config.load_keys_into_env()
    assert "malformed" in capsys.readouterr().err
    config._data.cache_clear()
    config.config_error.cache_clear()


def test_config_error_none_when_missing_or_valid(monkeypatch, tmp_path):
    monkeypatch.setenv("KO_CONFIG_DIR", str(tmp_path))  # no config.toml at all
    config._data.cache_clear()
    config.config_error.cache_clear()
    assert config.config_error() is None
    _with_config(monkeypatch, tmp_path, '[llm]\nmodel = "x:y"\n')
    config.config_error.cache_clear()
    assert config.config_error() is None
    config._data.cache_clear()
    config.config_error.cache_clear()


# --- run cost: OR actuals preferred, estimate fallback, tokens always ---


def _resp(cost=None, tin=10, tout=5, model="m:x"):
    from pydantic_ai.messages import ModelResponse, TextPart
    from pydantic_ai.usage import RequestUsage

    return ModelResponse(
        parts=[TextPart(content="x")],
        usage=RequestUsage(input_tokens=tin, output_tokens=tout),
        model_name=model,
        provider_details={"cost": cost} if cost is not None else None,
    )


def test_run_cost_prefers_openrouter_actuals():
    from ko import llm

    rc = llm.run_cost([_resp(cost=0.001), _resp(cost=0.002)])
    assert rc.source == "actual"
    assert abs(rc.usd - 0.003) < 1e-9
    assert (rc.input_tokens, rc.output_tokens) == (20, 10)
    assert "$0.0030" in rc.note and "~" not in rc.note  # actuals are exact, not approximate


def test_run_cost_tokens_only_when_unpriceable():
    from ko import llm

    # no provider cost + model unknown to genai-prices -> tokens only, no $ in the note
    rc = llm.run_cost([_resp(model="nonexistent:model-xyz")])
    assert rc.usd is None
    assert "tok" in rc.note and "$" not in rc.note


def test_run_cost_tiny_amounts_never_show_zero():
    from ko import llm

    rc = llm.run_cost([_resp(cost=0.00001)])
    assert "<$0.0001" in rc.note  # never a misleading "$0.0000"
