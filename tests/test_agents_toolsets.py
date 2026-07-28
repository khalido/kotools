"""Agent-facing toolset behaviour: what `_try` lets through, and what the web tools
actually put in front of a model (schema + content mode). No network — the model is a
pydantic-ai FunctionModel and the sources are monkeypatched."""

from __future__ import annotations

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from ko import exa as exa_mod
from ko.agents import _toolsets
from ko.agents._toolsets import _try, web
from ko.config import MissingKeyError

# --- _try: what surfaces vs what becomes a note --------------------------------


def test_try_returns_note_for_flaky_source():
    def boom():
        raise ConnectionError("rate limited")

    note = _try("exa_search", boom)
    assert "exa_search unavailable" in note
    assert "another source" in note


def test_try_reraises_missing_key():
    """A missing key is config Ko must fix — it must NOT become 'try another source',
    which silently drops the tool for the whole run."""

    def boom():
        raise MissingKeyError("EXA_API_KEY is not set. Get a key at https://exa.ai/")

    with pytest.raises(MissingKeyError, match="EXA_API_KEY"):
        _try("exa_search", boom)


def test_try_reraises_programming_errors():
    def boom():
        raise TypeError("search() got an unexpected keyword argument")

    with pytest.raises(TypeError):
        _try("exa_search", boom)


def test_missing_key_propagates_out_of_the_tool(monkeypatch):
    """End-to-end on the real tool function: no EXA_API_KEY → the error reaches the caller."""
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    with pytest.raises(MissingKeyError, match="EXA_API_KEY"):
        _toolsets.exa_search("anything")


def test_missing_key_is_a_runtime_error():
    """Subclassing RuntimeError keeps every `except RuntimeError` / CLI path working."""
    assert issubclass(MissingKeyError, RuntimeError)


# --- exa_search: the content mode the agent actually gets ----------------------


def test_exa_search_asks_for_bounded_highlights(monkeypatch):
    """Agent search = highlights only. Full text/summary are the escalation path
    (exa_get/fetch_url), not the default — ~10x fewer tokens per hit."""
    seen = {}

    def fake_search(query, **kwargs):
        seen.update(query=query, **kwargs)
        return []

    monkeypatch.setattr(exa_mod, "search", fake_search)
    _toolsets.exa_search("pydantic-ai releases", n=3, category="research paper")
    assert seen["with_highlights"] is True
    assert seen["with_text"] is False
    assert seen["with_summary"] is False
    assert seen["category"] == "research paper"
    assert seen["n"] == 3


# --- tool schema: what the model can see and call ------------------------------


def _tool_schema(tool_name: str) -> dict:
    """Run an agent against a FunctionModel purely to capture the tool defs it is handed."""
    captured: dict[str, dict] = {}

    def capture(messages, info: AgentInfo) -> ModelResponse:
        for t in info.function_tools:
            captured[t.name] = t.parameters_json_schema
        return ModelResponse(parts=[TextPart("done")])

    Agent(FunctionModel(capture), toolsets=[web]).run_sync("hi")
    return captured[tool_name]


def test_exa_search_schema_exposes_category_as_an_enum():
    schema = _tool_schema("exa_search")
    props = schema["properties"]
    assert "category" in props, "the scholarly index is unreachable if category isn't exposed"
    blob = str(props["category"])
    assert "research paper" in blob  # the wart: 'research paper', never 'publication'
    assert "publication" not in blob
    for value in exa_mod.CATEGORIES:
        assert value in blob


def test_exa_search_docstring_states_the_highlights_contract():
    """Docstring-as-contract: the model must know search returns bounded excerpts
    and which tool to escalate to."""
    doc = _toolsets.exa_search.__doc__ or ""
    assert "highlights" in doc
    assert "exa_get" in doc and "fetch_url" in doc
