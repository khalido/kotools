"""An agent that reaches its request limit answers from what it has, instead of failing."""

from __future__ import annotations

from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import UsageLimits

from ko.agents import _shared


def _hungry_agent() -> tuple[Agent, list, list]:
    """An agent whose model calls a tool whenever it can, recording what each request offered."""
    calls: list[str] = []
    offered: list[tuple[int, bool]] = []

    def model(messages, info):
        warned = any(
            "request(s) left" in str(getattr(part, "content", ""))
            for part in messages[-1].parts
        )
        offered.append((len(info.function_tools), warned))
        if info.function_tools:
            return ModelResponse(parts=[ToolCallPart("look", {})])
        return ModelResponse(parts=[TextPart("what I found")])

    agent = Agent(FunctionModel(model), capabilities=[_shared.wrap_up_near_limit])

    @agent.tool_plain
    def look() -> str:
        """Look something up."""
        calls.append("look")
        return "a finding"

    return agent, calls, offered


def test_warned_three_requests_out_and_the_last_has_no_tools():
    agent, calls, offered = _hungry_agent()
    result = agent.run_sync("q", usage_limits=UsageLimits(request_limit=6))
    assert result.output == "what I found"
    assert len(calls) == 5
    assert [tools for tools, _ in offered] == [1, 1, 1, 1, 1, 0]
    assert [warned for _, warned in offered] == [False, False, False, True, True, True]


def test_a_run_that_finishes_early_never_hears_about_it():
    agent = Agent(
        FunctionModel(lambda messages, info: ModelResponse(parts=[TextPart("done")])),
        capabilities=[_shared.wrap_up_near_limit],
    )
    result = agent.run_sync("q", usage_limits=UsageLimits(request_limit=30))
    assert result.output == "done"
    assert "request(s) left" not in str(result.all_messages())


def test_a_resumed_session_gets_its_full_budget_again():
    agent, calls, _ = _hungry_agent()
    first = agent.run_sync("q", usage_limits=UsageLimits(request_limit=2))
    second = agent.run_sync(
        "again", message_history=first.all_messages(), usage_limits=UsageLimits(request_limit=2)
    )
    assert second.output == "what I found"
    assert len(calls) == 2


def test_run_uses_the_default_limit_when_an_agent_sets_none(monkeypatch):
    seen = {}

    class Result:
        output = "x"

        def all_messages(self):
            return []

    class Fake:
        model = "m"

        def run_sync(self, prompt, **kw):
            seen.update(kw)
            return Result()

    monkeypatch.setattr(_shared, "resolve", lambda m: m)
    monkeypatch.setattr(_shared.sessions, "save", lambda *a: None)
    monkeypatch.setattr(_shared, "_cost_note", lambda *a, **k: None)
    monkeypatch.setattr(_shared, "_console", type("Piped", (), {"is_terminal": False})())
    _shared.run(Fake(), "q")
    assert seen["usage_limits"].request_limit is not None
