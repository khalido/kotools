"""Offline tests for ko.sessions — save/load/list roundtrip, DB index, summarize, no model calls."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock

from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

from ko import sessions


# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------


def _msgs():
    return [
        ModelRequest(parts=[UserPromptPart(content="hello world")]),
        ModelResponse(parts=[TextPart(content="hi there")], model_name="test:model"),
    ]


def _fake_msgs_with_tool():
    """Raw message dicts (not pydantic-ai objects) that match the on-disk JSON format."""
    return [
        {
            "kind": "request",
            "parts": [{"part_kind": "user-prompt", "content": "find me arxiv papers on transformers"}],
        },
        {
            "kind": "response",
            "model_name": "test:model",
            "parts": [
                {
                    "part_kind": "tool-call",
                    "tool_name": "arxiv_search",
                    "tool_call_id": "call_001",
                    "args": {"query": "transformers nlp", "max_results": 5},
                }
            ],
        },
        {
            "kind": "request",
            "parts": [
                {
                    "part_kind": "tool-return",
                    "tool_call_id": "call_001",
                    "content": "1. Attention is All You Need (2017)\n2. BERT (2018)\n3. GPT-3 (2020)",
                }
            ],
        },
        {
            "kind": "response",
            "model_name": "test:model",
            "parts": [{"part_kind": "text", "content": "Here are the top transformer papers."}],
        },
    ]


# ---------------------------------------------------------------------------
# Existing roundtrip tests (preserved)
# ---------------------------------------------------------------------------


def test_save_load_list_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setenv("KO_STATE_DIR", str(tmp_path))
    sid = sessions.new_id()

    path = sessions.save(sid, "research", _msgs())
    assert path.exists()
    assert path.parent.name == "sessions"

    rows = sessions.listing()
    row = next(r for r in rows if r["id"] == sid)
    assert row["agent"] == "research"
    assert row["title"] == "hello world"
    assert row["model"] == "test:model"

    loaded = sessions.load(sid)
    assert len(loaded) == 2
    assert isinstance(loaded[0], ModelRequest)


def test_save_preserves_created_at_across_turns(monkeypatch, tmp_path):
    monkeypatch.setenv("KO_STATE_DIR", str(tmp_path))
    sid = sessions.new_id()
    sessions.save(sid, "research", _msgs())

    first = json.loads((tmp_path / "sessions" / f"{sid}.json").read_text())["created_at"]
    sessions.save(sid, "research", _msgs())  # second turn
    second = json.loads((tmp_path / "sessions" / f"{sid}.json").read_text())["created_at"]
    assert first == second  # created_at is stable; only updated_at moves


# ---------------------------------------------------------------------------
# SQLite index tests
# ---------------------------------------------------------------------------


def test_db_row_created_on_upsert(monkeypatch, tmp_path):
    """upsert_session_summary writes a row; get_session_row retrieves it."""
    monkeypatch.setenv("KO_STATE_DIR", str(tmp_path))

    sessions.upsert_session_summary(
        id="test-001",
        agent="research",
        model="test:model",
        title="Transformers paper search",
        summary="Found top 3 transformer papers via arxiv.",
        tags=["nlp", "transformers", "arxiv"],
        created_at="2026-07-01T00:00:00+00:00",
    )

    row = sessions.get_session_row("test-001")
    assert row is not None
    assert row["id"] == "test-001"
    assert row["title"] == "Transformers paper search"
    assert row["summary"] == "Found top 3 transformer papers via arxiv."
    assert "nlp" in row["tags"].split(",")
    assert row["agent"] == "research"


def test_db_returns_none_for_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("KO_STATE_DIR", str(tmp_path))
    assert sessions.get_session_row("nonexistent-id") is None


def test_list_session_rows_tag_filter(monkeypatch, tmp_path):
    """list_session_rows(tag=...) returns only rows with that tag."""
    monkeypatch.setenv("KO_STATE_DIR", str(tmp_path))

    sessions.upsert_session_summary(
        id="sess-a", agent="tv", model="m", title="Movie night",
        summary="Picked Arrival to watch.", tags=["movies", "scifi"], created_at="2026-01-01T00:00:00+00:00",
    )
    sessions.upsert_session_summary(
        id="sess-b", agent="research", model="m", title="NLP papers",
        summary="Found BERT paper.", tags=["nlp", "papers"], created_at="2026-01-02T00:00:00+00:00",
    )

    movie_rows = sessions.list_session_rows(tag="movies")
    assert len(movie_rows) == 1
    assert movie_rows[0]["id"] == "sess-a"

    all_rows = sessions.list_session_rows()
    assert len(all_rows) == 2


def test_list_session_rows_search_filter(monkeypatch, tmp_path):
    """list_session_rows(search=...) does case-insensitive substring match on title+summary."""
    monkeypatch.setenv("KO_STATE_DIR", str(tmp_path))

    sessions.upsert_session_summary(
        id="sess-c", agent="research", model="m", title="Python packaging",
        summary="Resolved uv vs pip conflicts.", tags=["python", "packaging"], created_at="2026-01-01T00:00:00+00:00",
    )
    sessions.upsert_session_summary(
        id="sess-d", agent="tv", model="m", title="Weekend movies",
        summary="Recommended Dune for Saturday night.", tags=["movies"], created_at="2026-01-02T00:00:00+00:00",
    )

    results = sessions.list_session_rows(search="python")
    assert len(results) == 1
    assert results[0]["id"] == "sess-c"

    results2 = sessions.list_session_rows(search="DUNE")  # case-insensitive
    assert len(results2) == 1
    assert results2[0]["id"] == "sess-d"

    results3 = sessions.list_session_rows(search="xyz-no-match")
    assert len(results3) == 0


def test_listing_merges_db_fields(monkeypatch, tmp_path):
    """listing() merges DB summary into the file-based listing."""
    monkeypatch.setenv("KO_STATE_DIR", str(tmp_path))

    sid = sessions.new_id()
    sessions.save(sid, "tv", _msgs())

    # Before summarizing — no summary
    rows = sessions.listing()
    row = next(r for r in rows if r["id"] == sid)
    assert row["summary"] is None
    assert row["title"] == "hello world"

    # After summarizing — DB values take over
    sessions.upsert_session_summary(
        id=sid, agent="tv", model="test:model",
        title="Hello test session", summary="User said hello; model replied.",
        tags=["test"], created_at="2026-01-01T00:00:00+00:00",
    )
    rows2 = sessions.listing()
    row2 = next(r for r in rows2 if r["id"] == sid)
    assert row2["summary"] == "User said hello; model replied."
    assert row2["title"] == "Hello test session"
    assert "test" in row2["tags"]


def test_listing_tag_filter(monkeypatch, tmp_path):
    """listing(tag=...) filters using DB tags."""
    monkeypatch.setenv("KO_STATE_DIR", str(tmp_path))

    sid1 = sessions.new_id()
    sessions.save(sid1, "tv", _msgs())
    sessions.upsert_session_summary(
        id=sid1, agent="tv", model="m", title="Movie rec",
        summary="Recommended Arrival.", tags=["movies", "scifi"], created_at="2026-01-01T00:00:00+00:00",
    )

    sid2 = sessions.new_id()
    sessions.save(sid2, "research", _msgs())
    sessions.upsert_session_summary(
        id=sid2, agent="research", model="m", title="Python tips",
        summary="Covered uv and ruff usage.", tags=["python"], created_at="2026-01-02T00:00:00+00:00",
    )

    rows = sessions.listing(tag="movies")
    assert len(rows) == 1
    assert rows[0]["id"] == sid1


def test_listing_search_filter(monkeypatch, tmp_path):
    """listing(search=...) filters by title/summary substring."""
    monkeypatch.setenv("KO_STATE_DIR", str(tmp_path))

    sid = sessions.new_id()
    sessions.save(sid, "research", _msgs())
    sessions.upsert_session_summary(
        id=sid, agent="research", model="m", title="Arxiv transformer search",
        summary="Found BERT and GPT-3 papers.", tags=["nlp"], created_at="2026-01-01T00:00:00+00:00",
    )

    # match in summary
    rows = sessions.listing(search="BERT")
    assert any(r["id"] == sid for r in rows)

    # no match
    rows2 = sessions.listing(search="quantum-computing-xyzzy")
    assert not any(r["id"] == sid for r in rows2)


# ---------------------------------------------------------------------------
# summarize command tests (monkeypatched model call)
# ---------------------------------------------------------------------------


def _write_session_file(tmp_path: Path, sid: str, agent: str = "research") -> Path:
    """Write a minimal session JSON file to tmp_path/sessions/."""
    sdir = tmp_path / "sessions"
    sdir.mkdir(parents=True, exist_ok=True)
    f = sdir / f"{sid}.json"
    data = {
        "id": sid,
        "agent": agent,
        "model": "test:model",
        "title": "find me arxiv papers on transformers",
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "tags": [],
        "messages": _fake_msgs_with_tool(),
    }
    f.write_text(json.dumps(data, indent=2))
    return f


def _make_fake_summarizer(title: str, summary: str, tags: list[str]):
    """Return a fake Agent.run_sync that returns canned structured output."""
    from pydantic import BaseModel

    class SessionSummary(BaseModel):
        title: str
        summary: str
        tags: list[str]

    fake_result = MagicMock()
    fake_result.output = SessionSummary(title=title, summary=summary, tags=tags)

    fake_agent = MagicMock()
    fake_agent.run_sync.return_value = fake_result
    return fake_agent


def test_summarize_db_row_created(monkeypatch, tmp_path):
    """summarize command creates a DB row for an unsummarized session."""
    from typer.testing import CliRunner
    from ko.cli import app  # registers all sub-apps on import

    monkeypatch.setenv("KO_STATE_DIR", str(tmp_path))
    sid = "20260101T000000-abc123"
    _write_session_file(tmp_path, sid)

    # Monkeypatch pydantic_ai.Agent so no real model call is made
    fake_agent = _make_fake_summarizer(
        title="Transformer papers found",
        summary="Searched arxiv and found BERT and GPT-3.",
        tags=["nlp", "transformers", "papers"],
    )

    import pydantic_ai

    original_agent = pydantic_ai.Agent

    def fake_agent_constructor(*args, **kwargs):
        if kwargs.get("output_type") is not None:
            return fake_agent
        return original_agent(*args, **kwargs)

    monkeypatch.setattr(pydantic_ai, "Agent", fake_agent_constructor)

    runner = CliRunner()
    result = runner.invoke(app, ["agent", "sessions", "summarize", "--n", "1"])
    assert result.exit_code == 0, result.output + (result.exception and str(result.exception) or "")

    row = sessions.get_session_row(sid)
    assert row is not None
    assert row["title"] == "Transformer papers found"
    assert "nlp" in row["tags"].split(",")


def test_summarize_idempotent(monkeypatch, tmp_path):
    """Running summarize twice on an up-to-date session makes no additional model call."""
    from typer.testing import CliRunner
    from ko.cli import app  # registers all sub-apps on import

    monkeypatch.setenv("KO_STATE_DIR", str(tmp_path))
    sid = "20260101T000000-idem01"
    _write_session_file(tmp_path, sid)

    call_count = 0

    class FakeSummary:
        title = "Idempotent test"
        summary = "Tested idempotence."
        tags = ["test"]

    def fake_run_sync(prompt, model=None):
        nonlocal call_count
        call_count += 1
        r = MagicMock()
        r.output = FakeSummary()
        return r

    fake_agent = MagicMock()
    fake_agent.run_sync.side_effect = fake_run_sync

    import pydantic_ai

    original_agent = pydantic_ai.Agent

    def fake_agent_constructor(*args, **kwargs):
        if kwargs.get("output_type") is not None:
            return fake_agent
        return original_agent(*args, **kwargs)

    monkeypatch.setattr(pydantic_ai, "Agent", fake_agent_constructor)

    runner = CliRunner()

    # First run — should call the model
    result = runner.invoke(app, ["agent", "sessions", "summarize", "--n", "1"])
    assert result.exit_code == 0, result.output
    assert call_count == 1

    # Second run — file mtime has not changed; should skip
    result2 = runner.invoke(app, ["agent", "sessions", "summarize", "--n", "1"])
    assert result2.exit_code == 0, result2.output
    assert call_count == 1  # no new call
    assert "already current" in result2.output


def test_summarize_stale_after_touch(monkeypatch, tmp_path):
    """After touching a session file, the next summarize run re-summarizes it."""
    from typer.testing import CliRunner
    from ko.cli import app  # registers all sub-apps on import

    monkeypatch.setenv("KO_STATE_DIR", str(tmp_path))
    sid = "20260101T000000-stale1"
    f = _write_session_file(tmp_path, sid)

    call_count = 0

    class FakeSummary:
        title = "Stale test"
        summary = "Session was stale and re-summarized."
        tags = ["test", "stale"]

    def fake_run_sync(prompt, model=None):
        nonlocal call_count
        call_count += 1
        r = MagicMock()
        r.output = FakeSummary()
        return r

    fake_agent = MagicMock()
    fake_agent.run_sync.side_effect = fake_run_sync

    import pydantic_ai

    original_agent = pydantic_ai.Agent

    def fake_agent_constructor(*args, **kwargs):
        if kwargs.get("output_type") is not None:
            return fake_agent
        return original_agent(*args, **kwargs)

    monkeypatch.setattr(pydantic_ai, "Agent", fake_agent_constructor)

    runner = CliRunner()

    # First run
    result = runner.invoke(app, ["agent", "sessions", "summarize", "--n", "1"])
    assert result.exit_code == 0, result.output
    assert call_count == 1

    # Touch the file to make mtime newer than summarized_at
    time.sleep(0.01)
    f.touch()

    # Second run — file is newer than DB row → re-summarize
    result2 = runner.invoke(app, ["agent", "sessions", "summarize", "--n", "1"])
    assert result2.exit_code == 0, result2.output
    assert call_count == 2  # re-summarized


# ---------------------------------------------------------------------------
# _digest / extract_digest unit tests
# ---------------------------------------------------------------------------


def test_digest_keeps_user_and_assistant_text():
    """_digest includes USER and ASSISTANT segments verbatim."""
    msgs = _fake_msgs_with_tool()
    digest = sessions._digest(msgs)

    assert "USER: find me arxiv papers on transformers" in digest
    assert "ASSISTANT: Here are the top transformer papers." in digest


def test_digest_compresses_tool_calls():
    """_digest represents tool calls as single compressed lines, not full output."""
    msgs = _fake_msgs_with_tool()
    digest = sessions._digest(msgs)

    # Should have a tool line
    assert "[tool: arxiv_search(" in digest
    # Should not have the full tool return verbatim (it was longer)
    assert "Attention is All You Need" in digest  # 150-char window allows this
    # Should not have raw tool-return part_kind in the output
    assert "part_kind" not in digest


def test_digest_one_line_per_tool_call():
    """Each tool call appears as exactly one [tool: ...] line."""
    msgs = _fake_msgs_with_tool()
    digest = sessions._digest(msgs)

    tool_lines = [line for line in digest.split("\n") if line.startswith("[tool:")]
    assert len(tool_lines) == 1  # one tool call in the fixture


def test_digest_truncation_respects_budget():
    """_digest never returns more than max_chars chars."""
    msgs = _fake_msgs_with_tool()
    digest = sessions._digest(msgs, max_chars=100)
    assert len(digest) <= 100


def test_digest_always_keeps_first_user_prompt():
    """Even under a tight budget, the first user prompt is always present."""
    msgs = _fake_msgs_with_tool()
    # Use a very tight budget — first user prompt is 43 chars + "USER: " prefix
    digest = sessions._digest(msgs, max_chars=60)
    assert "USER: find me arxiv papers on transformers" in digest

