---
name: opencode
description: How to drive OpenCode from the CLI programmatically (second opinions) + pick a model tier
---
# OpenCode, programmatically — kickoff

`opencode` is a coding agent CLI I keep around for **second opinions** — a different model's take
on a design, a review, a hard bug. Run it non-interactively (`opencode run`), pick the right model
tier for the stakes, and read its answer back. Docs: https://opencode.ai/docs/cli/ · models:
https://opencode.ai/docs/zen/. (There's also a `/opencode` Claude Code skill that wraps this.)

## The one-shot call

```bash
opencode run "MESSAGE" -m provider/model -f FILE1 -f FILE2
```

**Message comes FIRST** as a quoted positional arg; flags (`-m`, `-f`) come *after*. There is no
`--prompt`. Attach the files that matter with repeated `-f` (always include `CLAUDE.md` + 2–5 relevant
source files — the model has no repo context otherwise). Give it a 2–3 sentence context summary, then
the question. Pipe it: `... | opencode run "review this diff" -m opencode/glm-5.1`.

Useful flags:
- `--format json` — raw JSON events for parsing (default is formatted text).
- `-c` / `--continue`, `-s <id>` / `--session <id>` — resume a prior session.
- `--agent <name>` — pick a configured agent (e.g. a review-tuned one).
- `--auto` — auto-approve non-denied permissions (headless; use carefully).
- `opencode models [provider]` / `opencode models --refresh` — list/refresh available model IDs.

**Avoid cold-boot latency** for repeated calls: `opencode serve` in one terminal, then
`opencode run --attach http://localhost:4096 "..."` in another.

## Picking a model tier (match cost to stakes)

Model IDs drift — run `opencode models` for the current list. As of writing, the tiers I use:

| Tier | Model | When |
|------|-------|------|
| **Cheap / fast** | `opencode/minimax-m2.5-free` (free) · `opencode/kimi-k2.6` (cheap, strong agentic) | everyday "what am I missing", quick sanity checks, high-volume |
| **Medium** | `opencode/glm-5.1` (reviews, comparisons) · `opencode/gpt-5.3-codex-spark` (code-focused) | a real review or design comparison worth a few cents |
| **Hard / expensive** | `opencode/gpt-5.4` (architecture calls) · `opencode/gpt-5.4-pro` (flagship reasoning) | genuinely high-stakes: a critical review, a subtle bug, an architecture decision |

Rule of thumb: **default to cheap/free**, step up only when the answer's value justifies it.
`gpt-5.4-pro` is ~12× the price of `gpt-5.4` — reserve it for the calls where being wrong is expensive.
Paid prices are per 1M tokens (input/output); a single review is usually cents, but a `--auto` loop on
a pro model is not — mind runaway usage.

## When to reach for it

- **Second opinion on my own work** — before committing a non-trivial design/change, ask a *different*
  model to poke holes. Fresh context catches what I've gone blind to (same reason I use subagents).
- **A hard bug or perf question** — give it the failing code + symptoms on a medium/hard model.
- **Architecture comparison** — "approach A vs B for X, given these constraints" on a hard model.

Not for: routine edits (just do them), or anything needing my repo's full live context (opencode only
sees the files you `-f`). It's a consult, not a co-worker.

## Composing with ko

`ko` gathers, `opencode` opines: `ko papers get <doi> | opencode run "does this method hold up?" -m opencode/glm-5.1`,
or `ko fetch <url> | opencode run "summarize the disagreement" -m opencode/kimi-k2.6`.
