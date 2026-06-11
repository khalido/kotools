"""Research agent — semantic web search via Exa.

One-shot:    research.run("find papers on X")
Interactive: research.repl()
"""

from __future__ import annotations

import os
import readline  # noqa: F401 — enables up-arrow history + line editing in repl

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from pydantic_ai import Agent

from ko import exa as exa_mod
from ko.exa import ExaResult


_MODEL = os.environ.get("KO_AGENT_MODEL", "openrouter:deepseek/deepseek-v4-flash")
_console = Console()

agent = Agent(
    _MODEL,
    instructions=(
        "You are a research assistant with access to semantic web search. "
        "Use exa_search to find relevant sources, then exa_get for full content "
        "when needed. Always reply in markdown: use headers, bullet lists, and "
        "code blocks where appropriate. Be concise, cite URLs, prefer recent results."
    ),
)


@agent.tool_plain
def exa_search(query: str, n: int = 5) -> list[ExaResult]:
    """Semantic web search. Returns title, URL, date, and text excerpt per result."""
    return exa_mod.search(query, n=n, with_text=True)


@agent.tool_plain
def exa_get(urls: list[str]) -> dict[str, str]:
    """Fetch full markdown content for known URLs. Use when a result needs deeper reading."""
    return exa_mod.get_contents(urls)


def _stream(prompt: str, history: list) -> tuple[str, list]:
    """Run one turn with streaming markdown output. Returns (text, updated_history)."""
    text = ""
    with agent.run_stream_sync(prompt, message_history=history) as result:
        with Live(console=_console, refresh_per_second=15) as live:
            for chunk in result.stream_text():
                text += chunk
                live.update(Markdown(text))
    return text, result.all_messages()


def run(prompt: str) -> str:
    """One-shot: stream a research prompt to stdout, return the full text."""
    text, _ = _stream(prompt, [])
    return text


def repl() -> None:
    """Interactive research session. Message history preserved across turns."""
    history = []
    _console.print("[dim]ko research — /exit to quit, /clear to reset context[/dim]")
    while True:
        try:
            user_input = input("research> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not user_input:
            continue
        if user_input == "/exit":
            break
        if user_input == "/clear":
            history = []
            _console.print("[dim]context cleared[/dim]")
            continue
        _, history = _stream(user_input, history)
