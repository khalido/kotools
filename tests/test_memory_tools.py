"""Offline tests for agents/_memory.py — per-agent markdown memory workspaces."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic_ai import ModelRetry

from ko.agents import _memory


@pytest.fixture
def dirs(monkeypatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("KO_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("KO_CONFIG_DIR", str(tmp_path / "config"))
    (tmp_path / "config").mkdir()
    return tmp_path


def _tool(ts, name):
    """Fish a plain tool function back out of a FunctionToolset."""
    return ts.tools[name].function


# --- containment: agent's own dir, .md only ---


def test_note_escape_blocked(dirs) -> None:
    with pytest.raises(ModelRetry, match="outside"):
        _memory._resolve_note("repo", "../research/memory.md")


def test_non_md_blocked(dirs) -> None:
    with pytest.raises(ModelRetry, match="markdown only"):
        _memory._resolve_note("repo", "script.py")


def test_agents_have_separate_dirs(dirs) -> None:
    assert _memory.memory_dir("repo") != _memory.memory_dir("research")


# --- tools: append, protected anchor, edit uniqueness ---


def test_append_and_read_roundtrip(dirs) -> None:
    ts = _memory.memory_toolset("repo")
    _tool(ts, "append_memory")("- learned a thing")
    _tool(ts, "append_memory")("- learned another")
    text = _tool(ts, "read_note")()
    assert text.splitlines() == ["- learned a thing", "- learned another"]


def test_write_note_refuses_memory_md(dirs) -> None:
    ts = _memory.memory_toolset("repo")
    with pytest.raises(ModelRetry, match="can't be overwritten"):
        _tool(ts, "write_note")("memory.md", "clobbered")


def test_write_note_creates_other_files(dirs) -> None:
    ts = _memory.memory_toolset("repo")
    _tool(ts, "write_note")("svelte-notes.md", "# svelte\nstuff")
    assert "svelte" in _tool(ts, "read_note")("svelte-notes.md")


def test_edit_note_uniqueness_guard(dirs) -> None:
    ts = _memory.memory_toolset("repo")
    _tool(ts, "append_memory")("- alpha seen 2026-07-01\n- alpha seen 2026-07-02")
    with pytest.raises(ModelRetry, match="occurs 2 times"):
        _tool(ts, "edit_note")("alpha seen", "beta")
    with pytest.raises(ModelRetry, match="not found"):
        _tool(ts, "edit_note")("gamma", "beta")
    _tool(ts, "edit_note")("alpha seen 2026-07-01", "alpha seen 2026-07-03")
    assert "2026-07-03" in _tool(ts, "read_note")()


def test_edit_note_empty_new_string_deletes(dirs) -> None:
    ts = _memory.memory_toolset("repo")
    _tool(ts, "append_memory")("- stale entry")
    _tool(ts, "edit_note")("- stale entry", "")
    assert "stale" not in _tool(ts, "read_note")()


# --- pin-aware head truncation ---


def test_head_under_budget_untouched(dirs) -> None:
    assert _memory._head("a\nb", budget=10) == "a\nb"


def test_head_keeps_pin_and_newest_tail(dirs) -> None:
    pinned = ["foundation 1", _memory.PIN_MARKER]
    tail = [f"entry {i}" for i in range(20)]
    out = _memory._head("\n".join(pinned + tail), budget=10)
    lines = out.splitlines()
    assert lines[0] == "foundation 1"  # pin always survives
    assert lines[-1] == "entry 19"  # newest tail kept
    assert "entry 0" not in out  # oldest tail dropped
    assert "older lines truncated" in out


# --- injection block ---


def test_instructions_block_lists_other_notes(dirs) -> None:
    ts = _memory.memory_toolset("repo")
    _tool(ts, "append_memory")("- remembered")
    _tool(ts, "write_note")("extras.md", "x")
    block = _memory.instructions_block("repo")
    assert "- remembered" in block
    assert "extras.md" in block and "read_note" in block


def test_instructions_block_includes_shared(dirs) -> None:
    _memory.shared_file().write_text("Ko prefers tight summaries.")
    block = _memory.instructions_block("research")
    assert "Shared memory" in block and "tight summaries" in block
    assert "(empty — you have not saved anything yet)" in block
