"""Repo-explorer agent — "how does repo X do Y?" over ~/code, read-only, cheap.

Assembly per the house pattern: model + instructions + toolsets, ~15 lines.
Basic-tier model on purpose — exploring code is mostly navigation + quoting,
and every run prints its cost note. The refs/CLAUDE.md overview (the curated
map of reference repos + accumulated takeaways) is injected fresh per run via
a dynamic instruction, so updates to that file reach the agent immediately.

One-shot:    repo.run("how does simonw/llm register plugins?")
Interactive: repo.repl()
"""

from __future__ import annotations

from pydantic_ai import Agent

from ko import config, llm
from ko.agents import _shared
from ko.agents._files import refs_overview_head
from ko.agents._memory import instructions_block, memory_toolset
from ko.agents._toolsets import files

_MODEL = config.setting("KO_AGENT_MODEL", "agents", "model", llm.model_for("basic"))

agent = Agent(
    _MODEL,
    instructions=(
        "You explore Ko's code folder to answer questions about how things are done. "
        "Layout: the root holds Ko's OWN and work repos (kotools, everx, thinker, ...); "
        "`refs/` holds OTHER PEOPLE'S repos cloned read-only as pattern references — "
        "its curated map with per-repo takeaways follows below.\n"
        "SCOPE DISCIPLINE — the tree is ~30 repos; never wander it. From the question, "
        "pick the 1-2 repos that matter (the named local repo, plus the most relevant "
        "ref from the map) and stay inside them. Survey cheap first: list_dir / "
        "find_files for shape, grep to locate, read_file only the handful of files "
        "that answer the question — a repo's docs/ and examples/ usually beat cold "
        "grepping. Everything you need is in this prompt or those folders.\n"
        "Answer concretely: quote the relevant code and ALWAYS cite file:line. "
        "If you learned something durable about a ref repo, say so at the end — "
        "takeaways get added to refs/CLAUDE.md."
    ),
    toolsets=[files, memory_toolset("repo")],
)


@agent.instructions
def _refs_map() -> str:
    """Fresh each run — refs/CLAUDE.md accumulates takeaways between runs."""
    return f"## refs/ map (from refs/CLAUDE.md)\n\n{refs_overview_head()}"


@agent.instructions
def _memory() -> str:
    """Shared + own memory.md, head-capped — see agents/_memory.py."""
    return instructions_block("repo")


def run(prompt: str, model: str | None = None, resume: str | None = None) -> str:
    """One-shot exploration; streams to a TTY, plain text when piped."""
    return _shared.run(agent, prompt, name="repo", model=model, resume=resume)


def repl(model: str | None = None, resume: str | None = None) -> None:
    """Interactive session; pass resume=<id> to continue an earlier one."""
    _shared.repl(agent, banner="repo", model=model, resume=resume)
