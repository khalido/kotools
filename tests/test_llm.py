"""Offline tests for ko.llm — no model calls (plus one live smoke test, skipped without a key)."""

import json
import os

import pytest
from typer.testing import CliRunner

from ko import llm, models
from ko.cli import app

runner = CliRunner()


def test_default_model_env_override(monkeypatch):
    monkeypatch.setenv("KO_DEFAULT_MODEL", "openrouter:anthropic/claude-sonnet-4")
    assert llm.default_model() == "openrouter:anthropic/claude-sonnet-4"
    monkeypatch.delenv("KO_DEFAULT_MODEL")
    assert llm.default_model() == models.TIERS["basic"]


def test_available_models_filters_by_env_keys(monkeypatch):
    for var in set(models.PROVIDER_KEYS.values()):
        monkeypatch.delenv(var, raising=False)
    assert models.available_models() == []
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    names = models.available_models()
    assert names and all(m.startswith("google:") for m in names)
    assert models.available_models("google:gemini")  # prefix filtering works


def test_openrouter_cache_surfaces_only_with_key(monkeypatch, tmp_path):
    monkeypatch.setenv("KO_CACHE_DIR", str(tmp_path))
    for var in set(models.PROVIDER_KEYS.values()):
        monkeypatch.delenv(var, raising=False)
    assert models.cached_models("openrouter") == []  # no cache yet
    (tmp_path / "openrouter_models.json").write_text(
        '{"models": ["openrouter:anthropic/claude-sonnet-4", "openrouter:openai/gpt-5"]}'
    )
    assert len(models.cached_models("openrouter")) == 2
    # cached catalog is hidden until OPENROUTER_API_KEY is set
    assert not any(m.startswith("openrouter:") for m in models.available_models())
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    assert models.available_models("openrouter:") == [
        "openrouter:anthropic/claude-sonnet-4",
        "openrouter:openai/gpt-5",
    ]


def test_refresh_openrouter_noop_without_key(monkeypatch, tmp_path):
    monkeypatch.setenv("KO_CACHE_DIR", str(tmp_path))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert models.refresh_catalog("openrouter") == []  # no key → no network, no cache
    assert not (tmp_path / "openrouter_models.json").exists()


def test_refresh_force_bypasses_ttl(monkeypatch, tmp_path):
    import httpx2 as httpx

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
    assert models.refresh_catalog("openrouter") == ["openrouter:old/model"]
    assert calls["n"] == 0
    # force re-fetches despite a fresh cache
    assert models.refresh_catalog("openrouter", force=True) == ["openrouter:new/model"]
    assert calls["n"] == 1


def test_refresh_records_network_failure(monkeypatch, tmp_path):
    import httpx2 as httpx

    monkeypatch.setenv("KO_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    (tmp_path / "openrouter_models.json").write_text('{"models": ["openrouter:old/model"]}')

    def boom(url, timeout=0):
        raise httpx.ConnectError("no network")

    monkeypatch.setattr(httpx, "get", boom)
    # a forced refresh that can't reach OpenRouter serves the old cache and records the miss,
    # instead of looking identical to a success (the real item-3 defect: silent failure)
    assert models.refresh_catalog("openrouter", force=True) == ["openrouter:old/model"]
    assert models.last_refresh_hit_network is False


def test_cache_age_none_when_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("KO_CACHE_DIR", str(tmp_path))
    assert models.catalog_age("openrouter") is None


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
    monkeypatch.setattr(models, "refresh_catalogs", lambda *a, **k: None)
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
    not os.environ.get("VERCEL_AI_GATEWAY_API_KEY"),
    reason="VERCEL_AI_GATEWAY_API_KEY not set; skipping live --schema smoke test",
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
    monkeypatch.setenv("VERCEL_AI_GATEWAY_API_KEY", "vck_test")  # resolve() checks it; no call is made
    seen = {}

    def _run_sync(user_prompt, **kw):
        seen["prompt"] = user_prompt
        raise SystemExit  # stop before any provider work

    class _Agent:
        def __init__(self, *a, **k):
            pass

        run_sync = staticmethod(_run_sync)

    import pydantic_ai

    monkeypatch.setattr(pydantic_ai, "Agent", _Agent)  # llm imports it lazily, inside run()
    monkeypatch.setattr(llm, "_default_agent", lambda: _Agent())
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
    import httpx2 as httpx

    class _Resp:
        headers = {"content-type": "text/html; charset=utf-8"}
        content = b"<html>login</html>"

        def raise_for_status(self):
            pass

    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp())
    r = runner.invoke(app, ["llm", "-f", "https://x.com/chart.png", "describe"])
    assert r.exit_code != 0 and not fake_run
    assert "text/html" in r.stderr


def test_a_response_cut_short_raises_instead_of_passing_as_an_answer():
    """Gemini via OpenRouter returned half a page with finish_reason "error" and 0 usage; ko
    printed it and exited 0. Partial output must never pass as the answer."""
    import pytest
    from pydantic_ai.messages import ModelResponse, TextPart

    def resp(reason):
        return ModelResponse(
            parts=[TextPart("half a sente")], finish_reason=reason,
            provider_details={"downstream_provider": "Google"},
        )

    with pytest.raises(llm.TruncatedOutput, match=r"provider errored mid-answer \(upstream: Google\)"):
        llm.raise_if_cut_short([resp("error")], "openrouter:google/gemini-3-flash-preview")
    with pytest.raises(llm.TruncatedOutput, match="output-token limit"):
        llm.raise_if_cut_short([resp("length")], "m")
    llm.raise_if_cut_short([resp("stop")], "m")  # a finished answer passes


# --- decision models (`-m jev`, --choice, --score) ---


def test_parse_options_names_and_meanings():
    assert llm.parse_options("reply=needs an answer, read ,archive", kind="choice") == [
        ("reply", "needs an answer"), ("read", ""), ("archive", ""),
    ]


@pytest.mark.parametrize(
    ("spec", "kind"),
    [("a", "choice"), ("a,a", "choice"), ("a,,b", "choice"), (",".join("abcdefghijk"), "score")],
)
def test_parse_options_rejects_before_any_call(spec, kind):
    with pytest.raises(ValueError):
        llm.parse_options(spec, kind=kind)


def test_is_decision_model():
    assert llm.is_decision_model("jev")
    assert llm.is_decision_model("typesafe:jev-1.13")
    assert llm.is_decision_model("openai-decisions:gpt-6-luna")
    assert not llm.is_decision_model("openrouter:openai/gpt-6-luna")
    assert not llm.is_decision_model(None)


def test_jev_goes_through_openrouter_unless_typesafe_key(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    m = models.resolve("jev")
    assert m.model_name == "jev-latest"
    assert m.provider.base_url == "https://openrouter.ai/api"
    assert models.resolve("openrouter:x/y") == "openrouter:x/y"


def test_gateway_model_sends_host_order_only_for_listed_models(monkeypatch):
    monkeypatch.setenv("VERCEL_AI_GATEWAY_API_KEY", "vck_test")
    models._gateway_model.cache_clear()
    m = models.resolve("vercel:zai/glm-5.3-flash")
    gateway = m.settings["extra_body"]["providerOptions"]["gateway"]
    assert gateway == {"order": list(models.MODELS["zai/glm-5.3-flash"].hosts), "has": ["!quantization:fp4"]}
    assert m.provider.base_url == "https://ai-gateway.vercel.sh/v1"
    assert models.resolve("vercel:openai/gpt-6-luna").settings is None  # gateway defaults


def test_gateway_model_without_key_names_the_fix(monkeypatch):
    from ko.config import MissingKeyError

    monkeypatch.delenv("VERCEL_AI_GATEWAY_API_KEY", raising=False)
    models._gateway_model.cache_clear()
    with pytest.raises(MissingKeyError, match="vercel ai-gateway api-keys create"):
        models.resolve("vercel:zai/glm-5.3-flash")


def test_gateway_cost_estimate_from_cached_catalog(monkeypatch, tmp_path):
    from pydantic_ai.messages import ModelResponse, TextPart
    from pydantic_ai.usage import RequestUsage

    monkeypatch.setenv("KO_CACHE_DIR", str(tmp_path))
    (tmp_path / "vercel_models.json").write_text(
        '{"models": [], "prices": {"zai/glm-5.3-flash": {"input": "0.00000015", "output": "0.0000005"}}}'
    )
    resp = ModelResponse(
        parts=[TextPart("ok")],
        usage=RequestUsage(input_tokens=1_000_000, output_tokens=1_000_000),
        model_name="zai/glm-5.3-flash",
        provider_name="vercel",
    )
    c = llm.run_cost([resp])
    assert c.source == "estimate"
    assert c.usd == pytest.approx(0.65)
    assert "~$0.6500" in c.note


@pytest.mark.parametrize(
    ("args", "stdin", "msg"),
    [
        (["decide", "q"], "", "nothing to decide on"),
        (["decide", "q", "--choice", "a,b", "--score", "x,y"], "t", "pick one"),
        (["decide", "q", "--tags", "a", "--score", "x,y"], "t", "pick one"),
        (["decide", "q", "--score", "only"], "t", "2 to 10"),
        (["decide", "q", "--choice", "read=read it, take notes,skip"], "t", "single words"),
        (["llm", "-m", "jev", "q"], "t", "ko decide"),
    ],
)
def test_decision_usage_errors_cost_nothing(monkeypatch, args, stdin, msg):
    monkeypatch.setattr(llm, "decide", lambda *a, **k: pytest.fail("spent a call"))
    r = runner.invoke(app, args, input=stdin)
    assert r.exit_code == 2 and msg in r.output


class _FakeResult:
    """What decide() reads off an agent run: output, response.provider_details, model name."""

    def __init__(self, output, details):
        self.output = output
        self.response = type("R", (), {"provider_details": details, "model_name": "typesafe/jev-test"})()

    def all_messages(self):
        return []


@pytest.mark.parametrize(
    ("kwargs", "output", "details", "want"),
    [
        ({}, 0.97, {"confidence": {}, "probabilities": {}, "scores": {}}, ("yes_no", 0.97, {}, None, None)),
        (
            {"choice": [("read", ""), ("archive", "")]},
            "read",
            {"confidence": {"response": 0.77}, "probabilities": {"response": {"read": 0.88, "archive": 0.12}}},
            ("choice", "read", {"read": 0.88, "archive": 0.12}, 0.77, None),
        ),
        (
            {"score": [("skip", ""), ("skim", ""), ("read", "")]},
            {"level": 1},
            {"confidence": {"level": 0.75}, "probabilities": {"level": {"0": 0.1, "1": 0.8, "2": 0.1}}, "scores": {"level": 1.024}},
            ("score", 1.02, {"0": 0.1, "1": 0.8, "2": 0.1}, 0.75, "skim"),
        ),
        (
            {"tags": [("coding", ""), ("cooking", "")]},
            {"tags": ["coding"]},
            {"confidence": {"tags": 0.4}, "probabilities": {"tags": {"coding": 0.97, "cooking": 0.01}}},
            ("tags", ["coding"], {"coding": 0.97, "cooking": 0.01}, 0.4, None),
        ),
    ],
)
def test_decide_reads_provider_details(monkeypatch, kwargs, output, details, want):
    import pydantic_ai

    monkeypatch.setattr(pydantic_ai.Agent, "run_sync", lambda self, text: _FakeResult(output, details))
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    d = llm.decide("q?", "text", model="jev", **kwargs)
    assert (d.kind, d.answer, d.probabilities, d.confidence, d.level) == want
    assert d.model == "typesafe/jev-test"


def test_run_refuses_prose_from_a_decision_model():
    with pytest.raises(ValueError, match="ko decide"):
        llm.run("summarize", stdin="x", model="jev")


def test_judge_still_works_as_a_hidden_alias():
    r = runner.invoke(app, ["judge", "q"], input="")
    assert r.exit_code == 2 and "nothing to decide on" in r.output
    assert " decide " in runner.invoke(app, ["--help"]).output
    assert " judge " not in runner.invoke(app, ["--help"]).output


def test_judge_json_carries_everything(monkeypatch):
    d = llm.Decision("choice", "read", {"read": 0.88, "archive": 0.12}, 0.77, None, "typesafe/jev-test")
    monkeypatch.setattr(llm, "decide", lambda *a, **k: d)
    monkeypatch.setattr(llm, "last_cost", llm.RunCost("typesafe/jev-test", 1, 1))
    r = runner.invoke(app, ["decide", "q", "--choice", "read,archive", "--json"], input="text")
    assert r.exit_code == 0
    assert json.loads(r.stdout.splitlines()[0])["probabilities"] == {"read": 0.88, "archive": 0.12}


def test_spec_extra_body_keeps_the_host_order(monkeypatch):
    spec = models.ModelSpec("zai/glm-5.3-flash", "t", hosts=("baseten",), settings={"extra_body": {"reasoning": {"effort": "low"}}, "max_tokens": 9})
    monkeypatch.setitem(models.MODELS, spec.id, spec)
    monkeypatch.setenv("VERCEL_AI_GATEWAY_API_KEY", "vck_test")
    monkeypatch.setattr(models, "refresh_catalog", lambda *a, **k: [])
    m = models.resolve("vercel:zai/glm-5.3-flash")
    assert m.settings["extra_body"]["providerOptions"]["gateway"]["order"] == ["baseten"]
    assert m.settings["extra_body"]["reasoning"] == {"effort": "low"}
    assert m.settings["max_tokens"] == 9


def test_model_match_is_fuzzy_and_newest_first():
    catalog = [
        {"id": "anthropic/claude-sonnet-5", "name": "Claude Sonnet 5", "released": 1, "type": "language"},
        {"id": "anthropic/claude-sonnet-5.5", "name": "Claude Sonnet 5.5", "released": 2, "type": "language"},
        {"id": "anthropic/claude-opus-5.5", "name": "Claude Opus 5.5", "released": 3, "type": "language"},
        {"id": "x/sonnet-video", "name": "Sonnet Video", "released": 9, "type": "video"},
    ]
    assert [m["id"] for m in models.match("sonnet", catalog)] == ["anthropic/claude-sonnet-5.5", "anthropic/claude-sonnet-5"]
    assert [m["id"] for m in models.match("Sonnet 5-5", catalog)] == ["anthropic/claude-sonnet-5.5"]
    assert models.match("gemini", catalog) == []
    assert [m["id"] for m in models.match("sonnet 5", catalog)] == ["anthropic/claude-sonnet-5"]


def test_models_info_compare_reports_a_miss_and_keeps_the_rest(monkeypatch):
    def fake_info(q, years=1, threads=3):
        if q == "nope":
            raise LookupError("no gateway model matches 'nope'")
        host = models.Host("baseten", 0.1, 0.3, 250.0, 300.0, "fp8", 99.9)
        return models.ModelInfo(
            q, q, "2026-10-17", 1_048_576, 131_072, 0.1, 0.3, None, [], "", "", [host], [], []
        )

    monkeypatch.setattr(models, "info", fake_info)
    r = runner.invoke(app, ["models", "info", "a/one", "nope", "b/two"])
    assert r.exit_code == 0
    assert "[nope: no gateway model matches" in r.stderr
    assert "a/one" in r.stdout and "b/two" in r.stdout
    assert "1.05M" in r.stdout and "300 tok/s baseten" in r.stdout and "0.2 s baseten" in r.stdout

    r = runner.invoke(app, ["models", "info", "a/one", "nope", "--json"])
    rows = json.loads(r.stdout)
    assert [row.get("id") or row["code"] for row in rows] == ["a/one", "not_found"]

    r = runner.invoke(app, ["models", "info", "nope", "nope", "--json"])
    assert r.exit_code == 1


def test_spec_provider_options_keep_host_order(monkeypatch):
    monkeypatch.setenv("VERCEL_AI_GATEWAY_API_KEY", "fake")
    monkeypatch.setattr(models, "refresh_catalog", lambda *a, **k: [])
    spec = models.ModelSpec("x/y", "", hosts=("baseten",), settings={"extra_body": {"providerOptions": {"zai": {"a": 1}}}})
    monkeypatch.setitem(models.MODELS, "x/y", spec)
    po = models.resolve("vercel:x/y").settings["extra_body"]["providerOptions"]
    assert po["gateway"]["order"] == ["baseten"] and po["zai"] == {"a": 1}


def test_model_info_survives_hn_failure(monkeypatch):
    import httpx2 as httpx

    m = {"id": "x/y", "name": "Y 1", "released": 1, "type": "language", "pricing": {}}
    monkeypatch.setattr(models, "_live_catalog", lambda: [m])

    class R:
        def raise_for_status(self):
            return self

        def json(self):
            return {"data": {"endpoints": []}}

    monkeypatch.setattr(httpx, "get", lambda *a, **k: R())

    def down(*a, **k):
        raise httpx.ConnectError("algolia down")

    monkeypatch.setattr(models, "_hn_stories", down)
    i = models.info("y 1")
    assert i.id == "x/y" and i.threads == [] and i.unavailable == ["HN: algolia down"]
