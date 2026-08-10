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


# --- -f/--file: attach files/URLs as input (the opencode/simonw-llm convention) ---


@pytest.fixture
def fake_run(monkeypatch):
    calls = []

    def _run(prompt, stdin=None, model=None, system=None, schema=None, attachments=None):
        calls.append({"prompt": prompt, "stdin": stdin, "attachments": attachments})
        return "OK"

    monkeypatch.setattr(llm, "run", _run)
    monkeypatch.setattr(llm, "last_cost", None)
    return calls


def test_llm_file_flag_prepends_content(fake_run, tmp_path):
    a = tmp_path / "a.md"
    a.write_text("ALPHA CONTENT")
    b = tmp_path / "b.md"
    b.write_text("BETA CONTENT")
    r = runner.invoke(app, ["llm", "-f", str(a), "-f", str(b), "compare"], input="PIPED")
    assert r.exit_code == 0
    stdin = fake_run[0]["stdin"]
    # fragments in flag order, piped stdin last — long inputs first, question last
    assert stdin.index("ALPHA") < stdin.index("BETA") < stdin.index("PIPED")
    assert f"file:{a.name}" in r.stderr  # dispatch line per attachment


def test_llm_file_flag_missing_file_is_fatal(fake_run, tmp_path):
    r = runner.invoke(app, ["llm", "-f", str(tmp_path / "nope.xyz"), "go"])
    assert r.exit_code != 0 and not fake_run  # no model call with half the context missing


def test_llm_image_attaches_as_binary(fake_run, tmp_path):
    from pydantic_ai import BinaryContent

    img = tmp_path / "chart.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\nfakebytes")
    txt = tmp_path / "notes.md"
    txt.write_text("TEXT CONTEXT")
    r = runner.invoke(app, ["llm", "-f", str(img), "-f", str(txt), "what does this say?"])
    assert r.exit_code == 0
    call = fake_run[0]
    # the image rides as a multimodal part, the .md stays on the text path
    assert len(call["attachments"]) == 1
    att = call["attachments"][0]
    assert isinstance(att, BinaryContent) and att.media_type == "image/png"
    assert "TEXT CONTEXT" in call["stdin"] and "PNG" not in call["stdin"]
    assert "image/png:chart.png" in r.stderr  # dispatch line shows what attached, as what


def test_llm_missing_image_is_fatal(fake_run, tmp_path):
    r = runner.invoke(app, ["llm", "-f", str(tmp_path / "nope.png"), "describe"])
    assert r.exit_code != 0 and not fake_run


# --- -x/--extract: first fenced code block only ---


def test_extract_first_fence(fake_run, monkeypatch):
    monkeypatch.setattr(
        llm, "run", lambda *a, **k: "Sure:\n```python\nprint(1)\n```\nAnd more prose."
    )
    r = runner.invoke(app, ["llm", "-x", "write it"])
    assert r.stdout == "print(1)\n"  # fence body only — pipe-clean


def test_extract_no_fence_passes_through(fake_run, monkeypatch):
    monkeypatch.setattr(llm, "run", lambda *a, **k: "just prose")
    r = runner.invoke(app, ["llm", "-x", "write it"])
    assert r.stdout == "just prose\n"


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


def test_extract_unclosed_fence_drops_the_opener(fake_run, monkeypatch):
    # A truncated reply leaves the fence unclosed — -x must not pipe the ```python opener.
    monkeypatch.setattr(llm, "run", lambda *a, **k: "Sure:\n```python\nprint(1)\n")
    r = runner.invoke(app, ["llm", "-x", "write it"])
    assert r.stdout == "print(1)\n"


def test_media_type_ignores_query_and_fragment_and_routes_svg_to_text():
    from ko.cli_ai import _media_type

    assert _media_type("photo.png#frag") == "image/png"
    assert _media_type("https://x.com/a.jpg?w=200") == "image/jpeg"
    assert _media_type("diagram.svg") is None  # SVG is text — the free text path takes it
    assert _media_type("report.pdf") is None  # PDFs deliberately stay on the text path


def test_llm_non_utf8_text_file_is_runtime_error_not_usage(fake_run, tmp_path):
    bad = tmp_path / "latin1.txt"
    bad.write_bytes("café".encode("latin-1"))
    r = runner.invoke(app, ["llm", "-f", str(bad), "go"])
    assert r.exit_code == 1 and not fake_run  # a read failure is 1; 2 is for usage errors
    assert "latin1.txt" in r.stderr  # the codec message alone doesn't name the file


def test_input_goes_before_the_prompt(monkeypatch):
    """simonw-llm's fragment convention, and what the -f help promises: documents first
    (stable prefix, cache-friendly), question last. This was documented-but-wrong once."""
    seen = {}

    def _run_sync(user_prompt, **kw):
        seen["prompt"] = user_prompt
        raise SystemExit  # stop before any provider work

    class _Agent:
        def __init__(self, *a, **k):
            pass

        run_sync = staticmethod(_run_sync)

    monkeypatch.setattr(llm, "Agent", _Agent)
    monkeypatch.setattr(llm, "_agent", _Agent())
    with pytest.raises(SystemExit):
        llm.run("MY QUESTION", stdin="DOC TEXT")
    assert seen["prompt"].index("DOC TEXT") < seen["prompt"].index("MY QUESTION")
    assert seen["prompt"].startswith("<input>")


def test_llm_svg_reaches_the_model_as_text(fake_run, tmp_path):
    """Regression: routing .svg off the multimodal path must not drop it — liteparse returns
    '' for SVG, and an empty part is filtered out, so it would vanish before the call."""
    svg = tmp_path / "diagram.svg"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg"><text>HELLO SVG</text></svg>')
    r = runner.invoke(app, ["llm", "-f", str(svg), "explain"])
    assert r.exit_code == 0
    call = fake_run[0]
    assert not call["attachments"] and "HELLO SVG" in call["stdin"]


def test_llm_media_url_serving_html_fails_loud(fake_run, monkeypatch):
    """A .png URL behind a login wall returns text/html — attaching that as an image sends
    the model garbage, so it must be an error, not a silent bad attachment."""
    import httpx

    class _Resp:
        headers = {"content-type": "text/html; charset=utf-8"}
        content = b"<html>login</html>"

        def raise_for_status(self):
            pass

    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp())
    r = runner.invoke(app, ["llm", "-f", "https://x.com/chart.png", "describe"])
    assert r.exit_code != 0 and not fake_run
    assert "text/html" in r.stderr
