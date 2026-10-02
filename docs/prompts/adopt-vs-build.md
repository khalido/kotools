# Research brief: what ko should adopt vs keep hand-rolled

You are investigating a build-vs-adopt question for `ko`, a personal CLI at
`/Users/ko/code/kotools`. **Do not write or modify any code.** The deliverable is a
decision document with evidence. Use subagents to parallelise; verify everything against
installed packages, not documentation.

## Read first

- `/Users/ko/code/kotools/CLAUDE.md` — what ko is, its principles, its subcommands
- `/Users/ko/code/kotools/docs/assistant.md` §2.5 — a first pass at this question, done
  2026-08-19. It has an overlap table and a code-mode benchmark. **Treat it as a starting
  hypothesis to verify or refute, not as fact.**
- `/Users/ko/.claude/CLAUDE.md` — Ko's global preferences. Two rules govern this
  investigation: *"Prefer well-maintained libraries over hand-rolling"* and *"Check what's
  already installed before building it — read the package's own types and dist rather than
  assuming. Where the docs and the installed version disagree, go with the installed
  version."*
- `/Users/ko/code/refs/CLAUDE.md` — map of reference repos, including `pydantic-ai`
  (v2.32.0) and `pydantic-ai-harness` (v0.23.0), both freshly pulled and readable locally

## The question

ko hand-rolled an agent layer — `src/ko/agents/` (`_toolsets.py`, `_memory.py`,
`_files.py`, `_shared.py`), plus `sessions.py`, `telemetry.py`, `llm.py`. Pydantic AI v2
and the first-party Harness now ship ~53 capabilities, many overlapping.

**For each overlapping piece, decide: adopt, keep, or hybrid — and say why.**

The thesis to test: *ko's opinion should live in composition and defaults, not in
mechanisms.* Mechanisms (memory stores, session persistence, search, sandboxed file
access) are commodity and maintained upstream; ko's value is which tiers, which toolsets,
what `--help` says, and what the output looks like piped into `cut -f`. Argue for or
against this with evidence.

## Method — non-negotiable

1. **Verify by import, not by docs.** For every capability you assess, import it from the
   installed `pydantic_ai_harness` 0.23.0 in ko's venv (`uv run python -c ...`), read its
   signature and source in `.venv/lib/python3.14/site-packages/pydantic_ai_harness/`, and
   compare against the source in `~/code/refs/pydantic-ai-harness/`. Doc page names are
   not always symbols — "Compaction" is four classes, "Guardrails" is three,
   `PromptInjectionDefender` needs a separate extra. Report any doc/reality mismatch.
2. **Read ko's actual implementation** before claiming an upstream equivalent is better.
   Several were built deliberately: `_files.py` resolves symlinks *before* containment
   checks (a TOCTTOU ordering choice), `_memory.py` has a pin marker protecting
   foundations from truncation, `exa_search` returns bounded highlights only
   (`with_text=False`) as a measured token decision. If upstream drops a property ko
   chose on purpose, that's a finding, not a detail.
3. **Cite file:line** on both sides of every comparison.
4. **State what you could not verify.** Unknowns are results.

## Assess at minimum

| ko | candidate upstream |
|---|---|
| `agents/_memory.py` | `Memory` + `FileStore`/`SqliteMemoryStore`/`SearchableMemoryStore` |
| `sessions.py` | `StepPersistence` |
| `sessions --search` (metadata only, SQLite) | `ConversationSearch` (BM25) |
| `agents/_files.py` | `FileSystem` |
| `web` toolset (Exa) | `ExaSearch` |
| `llm.run_cost` / `ko billing` | `SpendLimits` |
| `telemetry.py` | core `Instrumentation` |
| `ko prompt` briefs | `Skills` |
| `agents/repo.py` | `RepoContext`, `Coder` |
| `agents/research.py` | `Researcher` |
| nothing | `CodeMode`, `Planning`, `SubAgents`, compaction family, `ToolOutputLimits`, `SystemReminders`, `Advisor`, `DynamicWorkflow` |

## Specific questions to answer

- **Migration cost per item.** Lines deleted, lines changed, behaviour lost. Which are
  drop-in and which need a shim?
- **The 0.x risk.** The Harness says APIs may move between minor releases. Read its actual
  release notes and git history in `~/code/refs/pydantic-ai-harness` — how often have
  public APIs actually broken in the last 10 releases? Is the stated risk borne out?
  This is the single biggest input to how aggressive the recommendation should be.
- **Does adopting force a heavier install?** ko is one flat dependency list. Which
  capabilities need extras, and what do they pull in transitively?
- **Does `CodeMode` help on ko's real toolsets?** `docs/assistant.md` benchmarked a
  synthetic tool. Re-run the question against `news`/`papers`/`web` — real latency, real
  failure modes, cheap tiers (`basic`, `medium`). Does Monty's restricted stdlib bite on
  tools returning dataclasses rather than dicts?
- **What does ko lose?** For each "adopt", name the specific ko behaviour that disappears
  and whether it can be preserved by configuration.
- **Sequencing.** If some of this is worth doing, what is the first, smallest, highest-value
  change — and what must not be touched until it's proven?

## Also worth checking

Ko raised two side threads; investigate briefly and say whether they matter:

- ko pins `pydantic-ai-slim>=2.32,<3`. The global preference now says *"don't pin core deps
  (ranges + lockfile)"*. Is the `<3` cap right, given v2 shortened its no-breaking-changes
  window between majors from six months to three?
- Ko mentioned adopting modern Python typing (PEP 695 `type` statements / type parameters).
  ko targets Python 3.14. Assess whether ko's codebase would genuinely benefit or whether
  it's churn — and check what pydantic-ai's own source does, since it's a good proxy for
  what works with pydantic.

## Output

A single markdown document:

1. **Verdict table** — every item: adopt / keep / hybrid, one-line reason, migration size.
2. **The three highest-value changes**, in order, with the evidence for each.
3. **What to leave alone**, and why — explicitly including anything where ko's version is
   better for ko's use case.
4. **The 0.x risk assessment**, grounded in the Harness's actual release history.
5. **Open questions and what you could not verify.**

Be decisive. Ko wants a recommendation, not a survey. Where you're uncertain, say so and
say what would settle it. Push back on the starting hypothesis if the evidence disagrees —
`docs/assistant.md` §2.5 was written fast and wants adversarial review.
