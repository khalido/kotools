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

import os
from datetime import timedelta

from pydantic_ai import Agent
from pydantic_ai.capabilities import Capability
from pydantic_ai.usage import UsageLimits
from pydantic_ai_harness.tool_output_limits import (
    Band,
    LocalFileOverflowStore,
    Spill,
    ToolOutputLimits,
    Truncate,
)

from ko import config, dirs, models
from ko.agents import _shared
from ko.agents._files import refs_overview_head
from ko.agents._memory import instructions_block, memory_toolset
from ko.agents._toolsets import ai_models, files, news, packages, papers, tmdb, web

_MODEL = config.setting("KO_AGENT_MODEL", "agents", "model", models.model_for("basic"))  # KO 2026-10-27: cheap by default; -m for hard ones

# runaway guards: 30 model requests and 3M input tokens a run (about $6 on the smart tier,
# $1 on basic). The old research agent spent 6.2M ($12.58) on one question (WORKLOG 2026-11-17).
_LIMITS = UsageLimits(request_limit=30, input_tokens_limit=3_000_000)

# How-to bundles the model loads only when a question needs one (pydantic-ai deferred
# capabilities: the description is always listed, the instructions arrive on `load_capability`).
# They replace the separate research/repo/tv agents (WORKLOG 2026-11-17). Descriptions stay
# static strings so the catalog never changes between requests (prompt cache).
research = Capability(
    id="research",
    description="Method for a literature or state-of-the-art question: papers, citations, sources.",
    instructions=(
        "Work like a researcher: start broad (papers_search across publishers, hf_search for ML), "
        "find 1-2 seed papers, then SNOWBALL the citation graph: papers_cites (who built on it) and "
        "papers_refs (what it builds on) until sources recur. Triangulate across 2+ tools; they "
        "return different sets. Full papers are long and re-sent with every request after: read "
        "full text only for the 1-3 seeds, at most two per request, and use abstracts "
        "(papers_get) for the rest. Judge "
        "quality by released code and independent replication, not citation count alone (it lags "
        "and is prestige-biased). Verify a citation is real (papers_get) before relying on it. "
        "Reply in markdown with headers, bullets, and cited URLs/DOIs."
    ),
    defer_loading=True,
)


def _code_map() -> str:
    """Read at load time, so takeaways added to refs/CLAUDE.md reach the next run."""
    return f"## refs/ map (from refs/CLAUDE.md)\n\n{refs_overview_head()}"


code = Capability(
    id="code",
    description="For questions about Ko's repos or the refs/ reference clones under ~/code: "
    "scope rules and the curated refs map.",
    instructions=[
        (
            "Layout: the code root holds Ko's OWN and work repos (kotools, everx, thinker, ...); "
        "`refs/` holds OTHER PEOPLE'S repos cloned read-only as pattern references (map below). "
        "The tree is ~30 repos; never wander it. From the question, pick the 1-2 repos that matter "
        "(the named local repo, plus the most relevant ref from the map) and stay inside them. "
        "A repo's docs/ and examples/ usually beat cold grepping. Quote the relevant code and "
        "ALWAYS cite file:line. If you learned something durable about a ref repo, say so at the "
            "end: takeaways get added to refs/CLAUDE.md."
        ),
        _code_map,
    ],
    defer_loading=True,
)

# Optional taste profile, kept out of the code: `[agents] tv_tastes` in config.toml (or KO_TV_TASTES).
_TASTES = config.setting("KO_TV_TASTES", "agents", "tv_tastes", "")

watch = Capability(
    id="watch",
    description="Picking a movie or show to watch tonight in Australia.",
    instructions=(
        "Use tv_lookup for rating, overview, and AU streaming; exa_search + fetch_url for reviews "
        "or where-to-watch details. Strongly prefer titles streaming in AU on a service Ko likely "
        "has. " + (f"Ko's taste: {_TASTES}. " if _TASTES else "") + "Recommend two or three "
        "concrete options, each with rating, a one-line why it fits, and where to stream; "
        "opinionated, not a list dump. Check memory first: don't re-pitch something already "
        "suggested (unless Ko liked it and wants more like it). Afterwards, note what you "
        "recommended and any reaction, so future picks improve."
    ),
    defer_loading=True,
)

# A tool result over 20k chars (a whole paper is up to ~300k) is saved to disk and the model
# gets a preview plus a handle it reads on with read_tool_result. Results stay in history and
# are re-sent every request, so this was most of a research run's cost (WORKLOG 2026-11-17).
# If the save fails it truncates instead, and the harness logs a warning to stderr.
output_limits = ToolOutputLimits(
    bands=[Band(over=20_000, action=Spill(then=Truncate(max_chars=20_000)))],
    store=LocalFileOverflowStore(base_dir=dirs.cache_dir() / "tool-output", cleanup_after=timedelta(days=7)),
)

agent = Agent(
    _MODEL,
    defer_model_check=True,  # the model (and its key) is resolved per run by _shared
    capabilities=[_shared.wrap_up_near_limit, _shared.detect_loops(), output_limits, research, code, watch],
    instructions=(
        "You are ko — Ko's personal agent, the default entry to his CLI toolkit. "
        "Route by task: current web → exa_search/fetch_url; literature → the papers "
        "tools; practitioner signal → Hacker News; a library or package → pkg_info; "
        "movies/TV → tv_lookup; Ko's own code or the refs/ reference clones → the "
        "read-only file tools under ~/code. Load the matching capability before you start "
        "a literature, code or what-to-watch question. "
        "Combine tools freely but stay frugal: survey before reading, read only what "
        "answers the question. Keep answers tight and concrete; cite URLs, papers, or "
        "file:line for every claim. A long tool result comes back as a preview and a handle: "
        "read on with read_tool_result only for the part you need. Maintain your memory per its rules; say you saved "
        "something only after the tool call that saved it returned."
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


# the most one run (one REPL turn) may spend, USD; it winds down to an answer before going over.
# KO 2026-11-17 after a $12.58 research question. `--budget` per run, `[agents] budget` to change.
DEFAULT_BUDGET = 1.0


def budget_usd(budget: float | None = None) -> float:
    """The run's budget: the argument, else KO_AGENT_BUDGET / `[agents] budget`, else $1.
    A value that isn't a positive number is a ConfigError, never silently the default."""
    raw = budget
    for source in (lambda: os.environ.get("KO_AGENT_BUDGET"), lambda: config.local_get("agents", "budget"),
                   lambda: config.get("agents", "budget")):
        if raw is None:
            raw = source()  # not config.setting: its `or` chain would turn a 0 into the default
    raw = DEFAULT_BUDGET if raw is None else raw
    try:
        usd = float(raw)
    except (TypeError, ValueError):
        usd = 0.0
    if not usd > 0:
        raise config.ConfigError(f"agent budget must be a positive number of USD, got {raw!r} (--budget or [agents] budget)")
    return usd


def run(prompt: str, model: str | None = None, resume: str | None = None, budget: float | None = None) -> str:
    """One-shot; streams to a TTY, plain text when piped."""
    return _shared.run(
        agent, prompt, name="ai", model=model, resume=resume, limits=_LIMITS, budget=budget_usd(budget)
    )


def repl(model: str | None = None, resume: str | None = None, budget: float | None = None) -> None:
    """Interactive session; pass resume=<id> to continue an earlier one."""
    _shared.repl(agent, banner="ai", model=model, resume=resume, limits=_LIMITS, budget=budget_usd(budget))
