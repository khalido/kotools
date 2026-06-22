"""Offline tests for ko.sessions — save/load/list roundtrip, no model calls."""

from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

from ko import sessions


def _msgs():
    return [
        ModelRequest(parts=[UserPromptPart(content="hello world")]),
        ModelResponse(parts=[TextPart(content="hi there")], model_name="test:model"),
    ]


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
    import json

    first = json.loads((tmp_path / "sessions" / f"{sid}.json").read_text())["created_at"]
    sessions.save(sid, "research", _msgs())  # second turn
    second = json.loads((tmp_path / "sessions" / f"{sid}.json").read_text())["created_at"]
    assert first == second  # created_at is stable; only updated_at moves
