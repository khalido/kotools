"""`ko sum` — fetch+summarize shortcut. LLM calls are monkeypatched; routing, prompt
resolution, section format and exit codes are what's under test."""

import os

import pytest
import typer.testing

from ko import llm as llm_mod
from ko import prompt as prompt_mod
from ko.cli import app

runner = typer.testing.CliRunner()


@pytest.fixture
def fake_llm(monkeypatch):
    """Replace llm.run with an echo that records the prompt/model it was called with."""
    calls = []

    def fake_run(prompt, stdin=None, model=None, system=None, schema=None):
        calls.append({"prompt": prompt, "stdin": stdin, "model": model})
        return f"SUMMARY({len(stdin or '')})"

    monkeypatch.setattr(llm_mod, "run", fake_run)
    monkeypatch.setattr(llm_mod, "last_cost", None)
    return calls


def test_packaged_summarize_prompt_exists():
    p = prompt_mod.get_prompt("summarize")
    assert "TL;DR" in p.body and "voice" in p.body  # keeps author voice, leads with a TL;DR


def test_sum_stdin(fake_llm):
    r = runner.invoke(app, ["sum"], input="a document about pelicans\n")
    assert r.exit_code == 0
    assert "SUMMARY(" in r.stdout and "#" not in r.stdout  # single input: no section header
    assert fake_llm[0]["stdin"].startswith("a document")
    assert "TL;DR" in fake_llm[0]["prompt"]  # default prompt is the packaged brief
    assert fake_llm[0]["model"] == llm_mod.sum_model()


def test_sum_local_md_file(fake_llm, tmp_path):
    f = tmp_path / "notes.md"
    f.write_text("# heading\n\nbody text")
    r = runner.invoke(app, ["sum", str(f)])
    assert r.exit_code == 0 and "SUMMARY(" in r.stdout
    assert f"file:{f.name}" in r.stderr  # dispatch line names what routing chose


def test_sum_multi_input_sections_and_partial_failure(fake_llm, tmp_path):
    a = tmp_path / "a.md"
    a.write_text("aaa")
    r = runner.invoke(app, ["sum", str(a), str(tmp_path / "missing.md")])
    assert r.exit_code == 0  # one succeeded -> success (fetch's multi-URL contract)
    assert f"# {a}" in r.stdout  # >1 input: per-input section headers
    assert "failed:" in r.stderr and "missing.md" in r.stderr


def test_sum_all_failed_exits_1(fake_llm, tmp_path):
    r = runner.invoke(app, ["sum", str(tmp_path / "nope.md")])
    assert r.exit_code == 1 and not fake_llm


def test_sum_prompt_override_by_file_and_name(fake_llm, tmp_path):
    pf = tmp_path / "brief.md"
    pf.write_text("MY CUSTOM PROMPT")
    runner.invoke(app, ["sum", "-p", str(pf)], input="text")
    assert fake_llm[-1]["prompt"] == "MY CUSTOM PROMPT"
    runner.invoke(app, ["sum", "-p", "summarize"], input="text")  # library name works too
    assert "TL;DR" in fake_llm[-1]["prompt"]
    r = runner.invoke(app, ["sum", "-p", "no-such-brief-xyz"], input="text")
    assert r.exit_code == 2  # not a file, no matching brief -> usage error


def test_sum_tty_no_input_is_usage_error(fake_llm, monkeypatch):
    import sys

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    r = runner.invoke(app, ["sum"])
    assert r.exit_code == 2


@pytest.mark.skipif(not os.environ.get("VERCEL_AI_GATEWAY_API_KEY"), reason="no VERCEL_AI_GATEWAY_API_KEY")
def test_sum_live_smoke():
    """One tiny live call on the real default model — cents, and proves the model string."""
    r = runner.invoke(app, ["sum"], input="Pelicans are large water birds with big bills.")
    assert r.exit_code == 0 and r.stdout.strip()


def test_urlish_heuristic_keeps_local_paths_local():
    """A mistyped path must fail fast, not spend two network round-trips (fetch + Wayback)."""
    from ko.cli_ai import _URLISH_RE

    for path in ("./notes.md", "/abs/path/x.md", "docs/x.md", "notes.md"):
        assert not _URLISH_RE.match(path), path
    for url in ("nyt.com/2026/x.html", "example.com:8080/page", "sub.domain.co/x"):
        assert _URLISH_RE.match(url), url


def test_sum_typod_relative_path_fails_fast(fake_llm):
    r = runner.invoke(app, ["sum", "./definitely-not-here.md"])
    assert r.exit_code == 1 and not fake_llm
    assert "neither an existing file nor a URL" in r.stderr


def test_sum_multi_input_separator_shape(fake_llm, tmp_path):
    """The flush refactor changed the emission path — pin the `# a … --- … # b` shape."""
    a, b = tmp_path / "a.md", tmp_path / "b.md"
    a.write_text("aaa")
    b.write_text("bbb")
    r = runner.invoke(app, ["sum", str(a), str(b)])
    assert r.exit_code == 0
    assert r.stdout.count("\n---\n") == 1  # exactly one rule between two sections
    assert r.stdout.index(f"# {a}") < r.stdout.index("\n---\n") < r.stdout.index(f"# {b}")
