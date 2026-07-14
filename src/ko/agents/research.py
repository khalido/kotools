"""Research agent — web (exa), papers (arxiv, hf), and HN discussion.

Assembly only: pick a model + instructions + the toolsets this agent gets.
Adding another agent is the same ~15 lines with a different model/toolset subset.

One-shot:    research.run("latest on agentic memory")
Interactive: research.repl()
"""

from __future__ import annotations

from pydantic_ai import Agent

from ko import config, llm
from ko.agents import _shared
from ko.agents._memory import instructions_block, memory_toolset
from ko.agents._toolsets import news, papers, web

# Default model (read at import): KO_AGENT_MODEL -> `[agents] model` in config.toml ->
# baked default; the `-m` flag overrides per-run via the model= arg (see run/repl).
_MODEL = config.setting("KO_AGENT_MODEL", "agents", "model", llm.model_for("smart"))

agent = Agent(
    _MODEL,
    instructions=(
        "You are a research assistant. For a literature/state-of-the-art question, work like a "
        "researcher: start broad (papers_search across publishers, hf_search for ML), find 1-2 "
        "seed papers, then SNOWBALL the citation graph — papers_cites (who built on it) and "
        "papers_refs (what it builds on) — until sources recur. Triangulate across ≥2 tools; "
        "they return different sets. Read what matters (papers_get / arxiv_fetch / fetch_url). "
        "Judge quality by released code + independent replication, not citation count alone "
        "(it lags and is prestige-biased). Verify a citation is real (papers_get) before relying "
        "on it. Reply in markdown with headers, bullets, and cited URLs/DOIs; be concise."
    ),
    toolsets=[web, papers, news, memory_toolset("research")],
)


@agent.instructions
def _context() -> str:
    """Shared frame: date, what a ko agent is, brevity — see _shared.preamble."""
    return _shared.preamble()


@agent.instructions
def _memory() -> str:
    """Shared + own memory.md, head-capped — see agents/_memory.py."""
    return instructions_block("research")


def run(prompt: str, model: str | None = None, resume: str | None = None) -> str:
    """One-shot research; streams to a TTY, plain text when piped. resume=<id> continues a session."""
    return _shared.run(agent, prompt, name="research", model=model, resume=resume)


def repl(model: str | None = None, resume: str | None = None) -> None:
    """Interactive research session; pass resume=<id> to continue an earlier one."""
    _shared.repl(agent, banner="research", model=model, resume=resume)
