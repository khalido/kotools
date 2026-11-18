"""Shared run/stream/repl helpers — generic over any pydantic-ai Agent.

Each ko agent module builds its `Agent` and binds these (`run(agent, prompt, name=...)`),
so streaming, pipe handling, the REPL loop, model override, and session saving all
live in one place. Every turn is persisted via `ko.sessions` so any agent gets
resume + history for free.

`model` is passed per-run (not baked into the Agent) so the `-m` flag actually
takes effect — the Agent reads its default model at import, before the CLI runs.
"""

from __future__ import annotations

import readline  # noqa: F401 — enables up-arrow history + line editing in repl
from dataclasses import dataclass, replace
from datetime import datetime
from zoneinfo import ZoneInfo

from pydantic_ai import Agent, SystemPromptPart
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import (
    ModelResponse,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
)
from pydantic_ai.usage import UsageLimits
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown

from ko import config, logs, sessions
from ko.llm import run_cost, tally
from ko.models import resolve

_console = Console()
_err = Console(stderr=True)  # cost notes are notes, not data — stderr keeps pipes clean


def preamble() -> str:
    """A short shared frame injected into every agent's instructions (above its own
    role): today's date in Ko's calendar timezone, what a ko agent is, and terminal
    brevity. Computed per run so the date is always current — agents otherwise guess
    it (e.g. when stamping a memory entry). Reads `[cal] timezone` directly rather
    than via gcal, to avoid pulling the Google stack into every agent import."""
    tz_name = config.get("cal", "timezone") or "Australia/Sydney"
    try:
        today = datetime.now(ZoneInfo(tz_name)).strftime("%A, %-d %B %Y")
    except Exception:
        today = datetime.now().strftime("%A, %d %B %Y")
    return (
        f"Today is {today}. You are a ko agent — one of the AI agents in Ko's personal "
        "command-line toolkit, run from a terminal by Ko (or by another agent over bash). "
        "Be concise and concrete: lead with the answer, cite sources (URLs, DOIs, or "
        "file:line), and cut filler — terminal output rewards brevity."
    )


def _cost_note(messages: list, prior: int = 0) -> None:
    """Print what the new messages (beyond `prior`) cost — OR actuals when available.
    markup=False: the note's own [brackets] would otherwise parse as rich tags and
    silently swallow the whole line."""
    _err.print(
        run_cost(messages[prior:]).note, style="dim", markup=False, highlight=False
    )


_GROWTH = 1.5


def _requests_left(ctx) -> tuple[float, str] | None:
    """How many more model requests this run can afford, and which limit says so: the fewest
    left under the request cap, the input-token cap and the dollar budget
    (`metadata["budget_usd"]`). Tokens and dollars are projected at 1.5x the last request: each
    one re-sends the whole context plus the new tool results, so it costs more than the one
    before. None when nothing limits the run."""
    limits = ctx.usage_limits
    left: list[tuple[float, str]] = []
    if limits and limits.request_limit is not None:
        left.append((limits.request_limit - ctx.usage.requests, f"{limits.request_limit}-request cap"))
    mine = [m for m in ctx.messages if isinstance(m, ModelResponse) and m.run_id == ctx.run_id]
    if not mine:
        return min(left, default=None)
    last = mine[-1]
    if limits and limits.input_tokens_limit and last.usage and last.usage.input_tokens:
        room = limits.input_tokens_limit - ctx.usage.input_tokens
        left.append((room / (_GROWTH * last.usage.input_tokens), f"{limits.input_tokens_limit:,}-token cap"))
    budget = (ctx.metadata or {}).get("budget_usd")
    if budget is not None:
        per = tally([last]).usd
        if per is None:
            _note_unpriced(last.model_name or "this model")
        elif per > 0:
            spent = tally(mine).usd or 0.0
            left.append(((budget - spent) / (_GROWTH * per), f"${spent:.{2 if spent >= 0.1 else 4}f} of the ${budget:g} budget spent"))
    return min(left, default=None)


_unpriced_noted: set[str] = set()


def _note_unpriced(model: str) -> None:
    """Fail loud, once per model: a budget we can't price is no budget."""
    if model not in _unpriced_noted:
        _unpriced_noted.add(model)
        _err.print(
            f"[note] no price known for {model}: the run's $ budget can't be checked; "
            "the request and token caps still apply",
            style="yellow", markup=False, highlight=False,
        )


@dataclass
class _WrapUpNearLimit(AbstractCapability):
    """pydantic-ai's "wrapping up before the request limit" recipe, extended to the token cap and
    the dollar budget. A run that reaches a limit raises, losing every tool call already paid for,
    so the model is told to finish while about three requests are left, and gets no tools at all
    (load_capability included) once under 1.5 are. Request-scoped, so the prompt cache holds.
    Says so on stderr once per run, naming the limit, so the caller can raise it."""

    warn_at: float = 3
    last_at: float = 1.5  # under this, the next request has to be the answer

    async def before_model_request(self, ctx, request_context):
        found = _requests_left(ctx)
        if found is None or found[0] > self.warn_at:
            return request_context
        left, why = found
        if ctx.run_id not in _wound_down:
            _wound_down.add(ctx.run_id)
            _err.print(f"[note] wrapping up: {why}", style="yellow", markup=False, highlight=False)
        ctx.enqueue(
            SystemPromptPart(
                "This run is nearly out of budget: give your final answer now from "
                "what you have found, and say plainly what you could not finish.",
                scope="request",
            )
        )
        if left <= self.last_at:
            # here, not in prepare_tools: load_capability is added after prepare_tools runs
            params = replace(request_context.model_request_parameters, function_tools=[])
            request_context = replace(request_context, model_request_parameters=params)
        return request_context


_wound_down: set[str] = set()


wrap_up_near_limit = _WrapUpNearLimit()

# Loops caught inside one response (streamed text, thinking or tool args, or one call repeated in a
# response): DetectLoops' own nudge rejects that response and retries, so the handler does the same.
_IN_RESPONSE_LOOPS = {"repeated_thinking", "repeated_stream_text", "repeated_tool_args", "repeated_parallel_call"}


def _on_loop(ctx, loop) -> None:
    """Say on stderr which loop was caught, then nudge the model as DetectLoops' default does."""
    from pydantic_ai import ModelRetry

    tools = f" ({', '.join(loop.tool_names)})" if loop.tool_names else ""
    _err.print(f"[note] loop: {loop.pattern}{tools} ×{loop.count}, told to change approach",
               style="yellow", markup=False, highlight=False)
    if loop.pattern in _IN_RESPONSE_LOOPS:
        ctx.enqueue(SystemPromptPart(loop.message, scope="request"))
        raise ModelRetry("Your previous response was rejected because it kept repeating itself.")
    ctx.enqueue(SystemPromptPart(loop.message))


def detect_loops():
    """pydantic-ai-harness's DetectLoops at its defaults (same call 5× in 10, same result 3× in a
    row, A-B-A-B, repeated text), with a stderr note per loop. A fresh one per agent; runs get their own."""
    from pydantic_ai_harness.detect_loops import DetectLoops

    return DetectLoops(action=_on_loop)


def _stream(
    agent: Agent, prompt: str, history: list, model: str | None = None, limits=None, budget: float | None = None
) -> tuple[str, list]:
    """Run one turn to completion with live markdown output. Returns (text, full message history).

    Not run_stream: it takes the first text as the final answer, so a model that narrates
    ("let me look that up") before a tool call ended the run there. run_sync with an event
    handler runs every tool call and still streams each text part as it arrives.
    """
    text = ""
    with Live(console=_console, refresh_per_second=15) as live:

        async def show(ctx, events) -> None:
            nonlocal text
            async for e in events:
                if isinstance(e, PartStartEvent) and isinstance(e.part, TextPart):
                    text += ("\n\n" if text else "") + e.part.content
                elif isinstance(e, PartDeltaEvent) and isinstance(e.delta, TextPartDelta):
                    text += e.delta.content_delta
                else:
                    continue
                live.update(Markdown(text))

        result = agent.run_sync(
            prompt,
            message_history=history,
            model=resolve(model or agent.model),
            usage_limits=limits,
            metadata=_budget(budget),
            event_stream_handler=show,
        )
    messages = result.all_messages()
    return _run_text(messages, prior=len(history)), messages


def _budget(usd: float | None) -> dict | None:
    """Run metadata carrying the dollar budget to _requests_left (it reads ctx.metadata)."""
    return None if usd is None else {"budget_usd": usd}


def _run_text(messages: list, prior: int = 0) -> str:
    """Every text part the model wrote this run, not just the last: a model that answers
    alongside a tool call (say, saving a memory note) and then signs off would otherwise
    print only the sign-off (2026-11-17 trial: the picks were lost)."""
    texts = [
        part.content
        for m in messages[prior:]
        if isinstance(m, ModelResponse)
        for part in m.parts
        if isinstance(part, TextPart) and part.content.strip()
    ]
    return "\n\n".join(texts)


def run(
    agent: Agent,
    prompt: str,
    name: str = "agent",
    model: str | None = None,
    resume: str | None = None,
    limits=None,
    budget: float | None = None,
) -> str:
    """One-shot. Streams pretty markdown to a TTY; plain text when piped. Saves the session.
    `budget`: most the run may spend in USD; it winds down before going over (see _requests_left).

    `resume` continues a saved session (the prompt becomes its next turn) instead of
    starting fresh — so `ko ai "follow-up" -r <id>` works as a one-shot continuation.
    """
    limits = limits or UsageLimits()  # the default's limit, but visible to wrap_up_near_limit
    session_id = resume or sessions.new_id()
    history = sessions.load(resume) if resume else []
    logs.add(agent=name, session=session_id, resumed=bool(resume))
    if _console.is_terminal:
        text, messages = _stream(agent, prompt, history, model=model, limits=limits, budget=budget)
    else:
        result = agent.run_sync(
            prompt, message_history=history, model=resolve(model or agent.model), usage_limits=limits,
            metadata=_budget(budget),
        )
        messages = result.all_messages()
        text = _run_text(messages, prior=len(history))
        print(text)
    _cost_note(messages, prior=len(history))
    sessions.save(session_id, name, messages)
    logs.inc(turns=1)
    return text


def repl(
    agent: Agent,
    banner: str = "agent",
    name: str | None = None,
    model: str | None = None,
    resume: str | None = None,
    limits=None,
    budget: float | None = None,
) -> None:
    """Interactive session; history preserved across turns and saved after each.
    `budget` applies per turn: each prompt is its own run.

    `resume` = a session id to continue an earlier conversation.
    """
    limits = limits or UsageLimits()
    name = name or banner
    if resume:
        session_id = resume
        history = sessions.load(resume)
        _console.print(f"[dim]resumed session {resume} ({len(history)} messages)[/dim]")
    else:
        session_id = sessions.new_id()
        history = []
    _console.print(f"[dim]ko {banner} — /exit to quit, /clear to reset context[/dim]")
    while True:
        try:
            user_input = input(f"{banner}> ").strip()
        except EOFError, KeyboardInterrupt:
            print()
            break
        if not user_input:
            continue
        if user_input == "/exit":
            break
        if user_input == "/clear":
            history = []
            session_id = sessions.new_id()
            _console.print("[dim]context cleared (new session)[/dim]")
            continue
        prior = len(history)
        _, history = _stream(agent, user_input, history, model=model, limits=limits, budget=budget)
        _cost_note(history, prior=prior)
        sessions.save(session_id, name, history)
        logs.add(agent=name, session=session_id)
        logs.inc(turns=1)
