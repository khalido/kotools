"""TV/movie agent — what to watch tonight in Australia, shaped to Ko's tastes.

Lightweight by design: a cheap model + tmdb lookup + general web/fetch (for
reviews and "where to watch" pages). Same ~15-line assembly as research.py,
just a different model and a smaller toolset subset — plus its own memory, so
it remembers what it already suggested and how Ko reacted.
"""

from __future__ import annotations


from pydantic_ai import Agent

from ko import config, llm
from ko.agents import _shared
from ko.agents._memory import instructions_block, memory_toolset
from ko.agents._toolsets import tmdb, web

# Cheap model — this is a light task. KO_AGENT_MODEL -> `[agents] model` -> baked
# default (read at import); the `-m` flag overrides per-run via model=.
_MODEL = config.setting("KO_AGENT_MODEL", "agents", "model", llm.model_for("basic"))

# Optional personal taste profile, kept out of the code: set `[agents] tv_tastes` in
# config.toml (or KO_TV_TASTES). Empty by default — the agent just recommends broadly.
_TASTES = config.setting("KO_TV_TASTES", "agents", "tv_tastes", "")
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
        "it fits, and where to stream. Be concise and opinionated, not a list dump. "
        "Check your memory before recommending — don't re-pitch something you already "
        "suggested (unless Ko liked it and wants more like it); after a session, note "
        "what you recommended and any reaction/likes/dislikes so future picks improve."
    ),
    toolsets=[tmdb, web, memory_toolset("tv")],
)


@agent.instructions
def _memory() -> str:
    """Shared + own memory.md, head-capped — see agents/_memory.py."""
    return instructions_block("tv")


def run(prompt: str, model: str | None = None, resume: str | None = None) -> str:
    """One-shot recommendation; streams to a TTY, plain text when piped. resume=<id> continues."""
    return _shared.run(agent, prompt, name="tv", model=model, resume=resume)


def repl(model: str | None = None, resume: str | None = None) -> None:
    """Interactive watch-picking session; pass resume=<id> to continue an earlier one."""
    _shared.repl(agent, banner="tv", model=model, resume=resume)
