"""TV/movie agent — what to watch tonight in Australia, shaped to Ko's tastes.

Lightweight by design: a cheap model + tmdb lookup + general web/fetch (for
reviews and "where to watch" pages). Same ~15-line assembly as research.py,
just a different model and a smaller toolset subset.
"""

from __future__ import annotations

import os

from pydantic_ai import Agent

from ko import config
from ko.agents import _shared
from ko.agents._toolsets import tmdb, web

# Cheap model — this is a light task. KO_AGENT_MODEL (read at import) overrides the
# default; the `-m` flag overrides per-run via model=. KO_AGENT_MODEL is global to all agents.
_MODEL = os.environ.get("KO_AGENT_MODEL", "google:gemini-3.5-flash")

# Optional personal taste profile, kept out of the code: set `[agents] tv_tastes` in
# config.toml (or KO_TV_TASTES). Empty by default — the agent just recommends broadly.
_TASTES = os.environ.get("KO_TV_TASTES") or config.get("agents", "tv_tastes", "")
_taste_line = f"Ko's taste: {_TASTES}. " if _TASTES else ""

agent = Agent(
    _MODEL,
    instructions=(
        "You help Ko pick something to watch tonight in Australia. "
        "Use tv_lookup for rating, overview, and AU streaming availability; use "
        "exa_search + fetch_url to pull reviews or where-to-watch details when useful. "
        "Strongly prefer titles actually streaming in AU on a service Ko likely has. "
        f"{_taste_line}"
        "Recommend two or three concrete options, each with rating, a one-line why "
        "it fits, and where to stream. Be concise and opinionated, not a list dump."
    ),
    toolsets=[tmdb, web],
)


def run(prompt: str, model: str | None = None, resume: str | None = None) -> str:
    """One-shot recommendation; streams to a TTY, plain text when piped. resume=<id> continues."""
    return _shared.run(agent, prompt, name="tv", model=model, resume=resume)


def repl(model: str | None = None, resume: str | None = None) -> None:
    """Interactive watch-picking session; pass resume=<id> to continue an earlier one."""
    _shared.repl(agent, banner="tv", model=model, resume=resume)
