"""Offline tests for ko.llm — no model calls."""

from ko import llm


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
