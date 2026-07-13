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

from pydantic_ai import Agent
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown

from ko import sessions
from ko.llm import run_cost

_console = Console()
_err = Console(stderr=True)  # cost notes are notes, not data — stderr keeps pipes clean


def _cost_note(messages: list, prior: int = 0) -> None:
    """Print what the new messages (beyond `prior`) cost — OR actuals when available.
    markup=False: the note's own [brackets] would otherwise parse as rich tags and
    silently swallow the whole line."""
    _err.print(run_cost(messages[prior:]).note, style="dim", markup=False, highlight=False)


def _stream(
    agent: Agent, prompt: str, history: list, model: str | None = None, limits=None
) -> tuple[str, list]:
    """Run one turn with live markdown output. Returns (text, full message history).

    v2: run_stream_sync returns the result directly (not a context manager), and
    stream_text(delta=False) yields the full text so far each tick.
    """
    result = agent.run_stream_sync(prompt, message_history=history, model=model, usage_limits=limits)
    text = ""
    with Live(console=_console, refresh_per_second=15) as live:
        for text in result.stream_text():
            live.update(Markdown(text))
    return text, result.all_messages()


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
    starting fresh — so `ko a research "follow-up" -r <id>` works as a one-shot continuation.
    """
    session_id = resume or sessions.new_id()
    history = sessions.load(resume) if resume else []
    if _console.is_terminal:
        text, messages = _stream(agent, prompt, history, model=model, limits=limits)
    else:
        result = agent.run_sync(prompt, message_history=history, model=model, usage_limits=limits)
        text, messages = result.output, result.all_messages()
        print(text)
    _cost_note(messages, prior=len(history))
    sessions.save(session_id, name, messages)
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
        except (EOFError, KeyboardInterrupt):
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
