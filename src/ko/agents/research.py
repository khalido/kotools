"""Research agent — web (exa), papers (arxiv, hf), and HN discussion.

Assembly only: pick a model + instructions + the toolsets this agent gets.
Adding another agent is the same ~15 lines with a different model/toolset subset.

One-shot:    research.run("latest on agentic memory")
Interactive: research.repl()
"""

from __future__ import annotations

import os

from pydantic_ai import Agent

from ko.agents import _shared
from ko.agents._toolsets import news, papers, web

# Default model. KO_AGENT_MODEL (read at import) overrides the default; the `-m` flag
# overrides per-run via the model= arg (see run/repl). KO_AGENT_MODEL is global to all agents.
_MODEL = os.environ.get("KO_AGENT_MODEL", "openrouter:z-ai/glm-5.2")

agent = Agent(
    _MODEL,
    instructions=(
        "You are a research assistant. Pick the right sources per query and "
        "triangulate across them. Always reply in markdown with headers, bullet "
        "lists, and cited URLs. Be concise and prefer recent results."
    ),
    toolsets=[web, papers, news],
)


def run(prompt: str, model: str | None = None, resume: str | None = None) -> str:
    """One-shot research; streams to a TTY, plain text when piped. resume=<id> continues a session."""
    return _shared.run(agent, prompt, name="research", model=model, resume=resume)


def repl(model: str | None = None, resume: str | None = None) -> None:
    """Interactive research session; pass resume=<id> to continue an earlier one."""
    _shared.repl(agent, banner="research", model=model, resume=resume)
