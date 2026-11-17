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
            "nearly out of budget" in str(getattr(part, "content", ""))
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
    assert "nearly out of budget" not in str(result.all_messages())


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


def test_a_token_cap_winds_down_before_it_overshoots():
    """Input tokens grow every request (the context is re-sent): the run is projected from
    the last request, warned about three out, and answers instead of raising."""
    agent, calls, offered = _hungry_agent()
    result = agent.run_sync("q", usage_limits=UsageLimits(request_limit=None, input_tokens_limit=2000))
    assert result.output == "what I found"
    assert offered[-1][0] == 0 and len(calls) == len(offered) - 1
    assert "nearly out of budget" in str(result.all_messages())
    assert result.usage.input_tokens <= 2000


def _priced(monkeypatch, per_token: float | None):
    """Price every response at `per_token` USD per input token (None = unknown price)."""
    from ko import llm

    def fake(messages):
        tin = sum(m.usage.input_tokens for m in messages if isinstance(m, ModelResponse))
        return llm.RunCost("m", tin, 0, None if per_token is None else tin * per_token, "estimate")

    monkeypatch.setattr(_shared, "tally", fake)


def test_a_dollar_budget_winds_down_and_still_answers(monkeypatch):
    _priced(monkeypatch, 0.001)  # ~$0.05-0.5 a request as the context grows
    agent, _calls, offered = _hungry_agent()
    result = agent.run_sync("q", usage_limits=UsageLimits(request_limit=None), metadata={"budget_usd": 1.0})
    assert result.output == "what I found"
    assert offered[-1][0] == 0  # the answer came from a request with no tools
    spent = sum(m.usage.input_tokens for m in result.all_messages() if isinstance(m, ModelResponse)) * 0.001
    assert spent <= 1.0


def test_one_huge_request_under_a_small_budget_still_answers(monkeypatch):
    """The first request alone costs more than the budget: the next one gets no tools."""
    _priced(monkeypatch, 1.0)
    agent, _calls, offered = _hungry_agent()
    result = agent.run_sync("q", usage_limits=UsageLimits(request_limit=None), metadata={"budget_usd": 0.5})
    assert result.output == "what I found"
    assert [tools for tools, _ in offered] == [1, 0]


def test_an_unpriced_model_says_so_once(monkeypatch, capsys):
    _priced(monkeypatch, None)
    monkeypatch.setattr(_shared, "_unpriced_noted", set())
    agent, calls, _ = _hungry_agent()
    agent.run_sync("q", usage_limits=UsageLimits(request_limit=5), metadata={"budget_usd": 1.0})
    err = capsys.readouterr().err
    assert err.count("no price known") == 1 and "$ budget can't be checked" in err
    assert len(calls) == 4  # the request cap still winds it down


def test_a_resumed_run_counts_only_its_own_spend(monkeypatch):
    _priced(monkeypatch, 0.001)
    agent, _, _ = _hungry_agent()
    first = agent.run_sync("q", usage_limits=UsageLimits(request_limit=None), metadata={"budget_usd": 1.0})
    agent2, _, offered2 = _hungry_agent()
    agent2.run_sync("again", message_history=first.all_messages(),
                    usage_limits=UsageLimits(request_limit=None), metadata={"budget_usd": 1.0})
    assert offered2[0][0] == 1  # history's spend doesn't count against the new run


def test_piped_output_keeps_text_written_alongside_a_tool_call():
    msgs = [
        ModelResponse(parts=[TextPart("the picks"), ToolCallPart("append_memory", {})]),
        ModelResponse(parts=[TextPart("Logged them.")]),
    ]
    assert _shared._run_text(msgs) == "the picks\n\nLogged them."


def test_terminal_stream_runs_past_narration_to_the_answer():
    """run_stream would stop at "let me check" (first text = final output) and skip the tool."""
    from pydantic_ai.models.function import DeltaToolCall

    calls = []

    async def stream(messages, info):
        if len(messages) == 1:
            yield "Let me check."
            yield {0: DeltaToolCall(name="look", json_args="{}")}
        else:
            yield "The answer."

    agent = Agent(FunctionModel(stream_function=stream))

    @agent.tool_plain
    def look() -> str:
        """Look something up."""
        calls.append("look")
        return "a finding"

    text, _ = _shared._stream(agent, "q", [])
    assert calls == ["look"]
    assert text == "Let me check.\n\nThe answer."


def test_the_last_request_offers_no_tools_even_with_deferred_capabilities(capsys):
    """load_capability is added after prepare_tools runs, so the wind-down strips tools at the
    request itself; a model that would load a capability on its last request must answer."""
    from pydantic_ai.capabilities import Capability

    offered = []

    def model(messages, info):
        names = [t.name for t in info.function_tools]
        offered.append(names)
        if "look" in names:
            return ModelResponse(parts=[ToolCallPart("look", {})])
        if "load_capability" in names:
            return ModelResponse(parts=[ToolCallPart("load_capability", {"id": "extra"})])
        return ModelResponse(parts=[TextPart("what I found")])

    extra = Capability(id="extra", description="more", instructions="x", defer_loading=True)
    agent = Agent(FunctionModel(model), capabilities=[_shared.wrap_up_near_limit, extra])

    @agent.tool_plain
    def look() -> str:
        """Look something up."""
        return "a finding"

    result = agent.run_sync("q", usage_limits=UsageLimits(request_limit=5))
    assert result.output == "what I found"
    assert offered[-1] == []
    assert capsys.readouterr().err.count("wrapping up: 5-request cap") == 1


def test_a_zero_budget_in_config_is_an_error_not_the_default(monkeypatch):
    import pytest

    from ko import config
    from ko.agents import ai

    monkeypatch.delenv("KO_AGENT_BUDGET", raising=False)
    monkeypatch.setattr(config, "local_get", lambda *a: None)
    monkeypatch.setattr(config, "get", lambda section, key, *a: 0 if (section, key) == ("agents", "budget") else None)
    with pytest.raises(config.ConfigError, match="positive number"):
        ai.budget_usd()
    monkeypatch.setattr(config, "get", lambda *a: None)
    assert ai.budget_usd() == ai.DEFAULT_BUDGET
    assert ai.budget_usd(2.5) == 2.5
