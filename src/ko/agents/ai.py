"""ko's default agent — the Layer-2 entry point over every Layer-1 toolset.

The June design (docs/ideas.md "ko ai — the agent layer") finally assembled:
all the module toolsets + its own memory, medium-tier model (capable enough to
route six toolsets, cheap enough to be the default), and a runaway request cap.
Explicit `ko ai` on purpose — intelligence is opt-in, a typo'd subcommand must
never silently spend tokens.

One-shot:    ai.run("what's worth reading on HN about local-first sync?")
Interactive: ai.repl()
"""

from __future__ import annotations

from pydantic_ai import Agent
from pydantic_ai.usage import UsageLimits

from ko import config, llm
from ko.agents import _shared
from ko.agents._memory import instructions_block, memory_toolset
from ko.agents._toolsets import files, news, papers, tmdb, web

_MODEL = config.setting("KO_AGENT_MODEL", "agents", "model", llm.model_for("medium"))

# an all-tools agent gets a runaway guard: 30 model requests per run, hard stop
_LIMITS = UsageLimits(request_limit=30)

agent = Agent(
    _MODEL,
    instructions=(
        "You are ko — Ko's personal agent, the default entry to his CLI toolkit. "
        "Route by task: current web → exa_search/fetch_url; literature → the papers "
        "tools (snowball cites/refs, triangulate sources); practitioner signal → "
        "Hacker News; movies/TV → tv_lookup; questions about Ko's own code or the "
        "refs/ reference clones → the read-only file tools under ~/code (for a deep "
        "multi-file dive, suggest `ko agent repo` instead — it carries the refs map). "
        "Combine tools freely but stay frugal: survey before reading, read only what "
        "answers the question. Keep answers tight and concrete; cite URLs, papers, or "
        "file:line for every claim. Maintain your memory per its rules."
    ),
    toolsets=[web, papers, news, tmdb, files, memory_toolset("ai")],
)


@agent.instructions
def _context() -> str:
    """Shared frame: date, what a ko agent is, brevity — see _shared.preamble."""
    return _shared.preamble()


@agent.instructions
def _memory() -> str:
    """Shared + own memory.md, head-capped — see agents/_memory.py."""
    return instructions_block("ai")


def run(prompt: str, model: str | None = None, resume: str | None = None) -> str:
    """One-shot; streams to a TTY, plain text when piped."""
    return _shared.run(
        agent, prompt, name="ai", model=model, resume=resume, limits=_LIMITS
    )


def repl(model: str | None = None, resume: str | None = None) -> None:
    """Interactive session; pass resume=<id> to continue an earlier one."""
    _shared.repl(agent, banner="ai", model=model, resume=resume, limits=_LIMITS)
