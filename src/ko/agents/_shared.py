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
from dataclasses import dataclass
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
from ko.llm import run_cost
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


def _requests_left(ctx) -> int | None:
    limit = ctx.usage_limits.request_limit if ctx.usage_limits else None
    return None if limit is None else limit - ctx.usage.requests


def _token_share(ctx) -> float:
    """How much of the run's input-token budget is spent (0 when there's no budget)."""
    limit = ctx.usage_limits.input_tokens_limit if ctx.usage_limits else None
    return ctx.usage.input_tokens / limit if limit else 0.0


@dataclass
class _WrapUpNearLimit(AbstractCapability):
    """pydantic-ai's "wrapping up before the request limit" recipe, for the input-token budget too. A run that reaches its limit
    raises, losing every tool call already paid for, so the model is told to finish in time, and its
    tools go away on the last request. Request-scoped, so the warning doesn't bust the prompt cache."""

    warn_at: int = 3
    # input tokens grow with every request (the whole context is re-sent), so the token budget
    # warns early and takes the tools away before one more request could overshoot it
    warn_share: float = 0.6
    stop_share: float = 0.75

    async def before_model_request(self, ctx, request_context):
        left = _requests_left(ctx)
        if (left is not None and left <= self.warn_at) or _token_share(ctx) >= self.warn_share:
            budget = (
                f"{left} model request(s) left in this run"
                if left is not None and left <= self.warn_at
                else "This run's token budget is nearly spent"
            )
            ctx.enqueue(
                SystemPromptPart(
                    f"{budget}: give your final answer now from "
                    "what you have found, and say plainly what you could not finish.",
                    scope="request",
                )
            )
        return request_context

    async def prepare_tools(self, ctx, tool_defs):
        return [] if _requests_left(ctx) == 1 or _token_share(ctx) >= self.stop_share else tool_defs


wrap_up_near_limit = _WrapUpNearLimit()


def _stream(
    agent: Agent, prompt: str, history: list, model: str | None = None, limits=None
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
            event_stream_handler=show,
        )
    messages = result.all_messages()
    return _run_text(messages, prior=len(history)), messages


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
) -> str:
    """One-shot. Streams pretty markdown to a TTY; plain text when piped. Saves the session.

    `resume` continues a saved session (the prompt becomes its next turn) instead of
    starting fresh — so `ko ai "follow-up" -r <id>` works as a one-shot continuation.
    """
    limits = limits or UsageLimits()  # the default's limit, but visible to wrap_up_near_limit
    session_id = resume or sessions.new_id()
    history = sessions.load(resume) if resume else []
    logs.add(agent=name, session=session_id, resumed=bool(resume))
    if _console.is_terminal:
        text, messages = _stream(agent, prompt, history, model=model, limits=limits)
    else:
        result = agent.run_sync(
            prompt, message_history=history, model=resolve(model or agent.model), usage_limits=limits
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
) -> None:
    """Interactive session; history preserved across turns and saved after each.

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
        _, history = _stream(agent, user_input, history, model=model, limits=limits)
        _cost_note(history, prior=prior)
        sessions.save(session_id, name, history)
        logs.add(agent=name, session=session_id)
        logs.inc(turns=1)
