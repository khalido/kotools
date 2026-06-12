"""Offline tests for ko.llm — no model calls."""

from ko import llm


def test_default_model_env_override(monkeypatch):
    monkeypatch.setenv("KO_DEFAULT_MODEL", "anthropic:claude-sonnet-4-6")
    assert llm.default_model() == "anthropic:claude-sonnet-4-6"
    monkeypatch.delenv("KO_DEFAULT_MODEL")
    assert llm.default_model() == llm.FALLBACK_MODEL


def test_available_models_filters_by_env_keys(monkeypatch):
    for var in set(llm.PROVIDER_KEYS.values()):
        monkeypatch.delenv(var, raising=False)
    assert llm.available_models() == []
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    models = llm.available_models()
    assert models and all(m.startswith("anthropic:") for m in models)
    assert llm.available_models("anthropic:claude-s")  # prefix filtering works
