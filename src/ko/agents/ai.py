"""ko's default agent — the Layer-2 entry point over every Layer-1 toolset.

The June design (docs/ideas.md "ko ai — the agent layer") finally assembled:
all the module toolsets + its own memory, basic-tier model (cheap enough to be
the default; -m a stronger one for hard questions), and a runaway request cap.
Explicit `ko ai` on purpose — intelligence is opt-in, a typo'd subcommand must
never silently spend tokens.

One-shot:    ai.run("what's worth reading on HN about local-first sync?")
Interactive: ai.repl()
"""

from __future__ import annotations

from pydantic_ai import Agent
from pydantic_ai.usage import UsageLimits

from ko import config, models
from ko.agents import _shared
from ko.agents._memory import instructions_block, memory_toolset
from ko.agents._toolsets import ai_models, files, news, packages, papers, tmdb, web

_MODEL = config.setting("KO_AGENT_MODEL", "agents", "model", models.model_for("basic"))  # KO 2026-10-27: cheap by default; -m for hard ones

# an all-tools agent gets a runaway guard: 30 model requests per run, hard stop
_LIMITS = UsageLimits(request_limit=30)

agent = Agent(
    _MODEL,
    defer_model_check=True,  # the model (and its key) is resolved per run by _shared
    capabilities=[_shared.wrap_up_near_limit],
    instructions=(
        "You are ko — Ko's personal agent, the default entry to his CLI toolkit. "
        "Route by task: current web → exa_search/fetch_url; literature → the papers "
        "tools (snowball cites/refs, triangulate sources); practitioner signal → "
        "Hacker News; a library or package → pkg_info; movies/TV → tv_lookup; questions about Ko's own code or the "
        "refs/ reference clones → the read-only file tools under ~/code (for a deep "
        "multi-file dive, suggest `ko agent repo` instead — it carries the refs map). "
        "Combine tools freely but stay frugal: survey before reading, read only what "
        "answers the question. Keep answers tight and concrete; cite URLs, papers, or "
        "file:line for every claim. Maintain your memory per its rules."
    ),
    toolsets=[web, papers, news, ai_models, packages, tmdb, files, memory_toolset("ai")],
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
