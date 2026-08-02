---
name: opencode
description: OpenCode headless for a second opinion — read-only lock, model tiers, follow-up sessions
---
# OpenCode, programmatically — kickoff

Paste this into an agent (or read it yourself) when you want a **second opinion** from a
different model family — a fresh take on a design, a review, a hard bug, or a plan worth
double-checking. The value is the *difference*, not the extra compute.

Assumes `opencode` is installed and an OpenRouter key is configured
(`opencode auth login`). Docs: https://opencode.ai/docs/cli/.
(There's also a `/opencode` Claude Code skill that wraps this.)

## The call

```bash
OPENCODE_PERMISSION='{"*":"deny","read":"allow","grep":"allow","glob":"allow"}' \
  opencode run "MESSAGE" -m provider/model -f FILE1 -f FILE2
```

**Message comes FIRST** as a quoted positional arg; flags come after. There is no
`--prompt`. Attach what matters with repeated `-f` — OpenCode has no repo context beyond
what you attach. Lead with a 2–3 sentence context summary, then the question. Pipes work:
`ko fetch <url> | opencode run "what's the strongest counterargument?" -m openrouter/~x-ai/grok-latest`.

### Run it read-only — the permission prefix is not optional

OpenCode's default `build` agent runs with `"*": "allow"`. It can edit files and call
every configured MCP server without asking, and **without** `--auto`. The env var above
is the reliable lock: denied tools are removed from the model's tool list entirely, so
they can't be called. Verified — the allowlist leaves exactly `glob, grep, read`, and a
shell redirect attempt comes back "unavailable tool".

**If you've run `ko setup opencode`**, prefer named agents instead — `opencode run "..."
--agent consult` carries the same lock plus a pre-baked brief, with no env prefix. The
env var above is the portable fallback for a machine without them. Either way, a repo's
own `.opencode/agents/` overrides global definitions and can restore full permissions,
so check for it before running against a repo you don't control.

- **Never `--auto`** (auto-approves everything not explicitly denied).
- `--pure` is not a substitute — it drops plugins but leaves MCP tools attached.
- Need more? Open one door deliberately (e.g. add `"webfetch":"allow"`), don't drop the
  default-deny.

Other useful flags:
- `-c` / `--continue`, `-s <id>` / `--session <id>` — resume a session (see below).
- `--format json` — raw JSON events for parsing (default is formatted text).
- `--variant high|max` — provider-side reasoning effort; often a better lever than
  paying for a bigger model.
- `opencode models` / `opencode models --verbose` — current IDs; verbose is JSON with
  live cost and context limits, so a tier table can be regenerated rather than guessed.

**Avoid cold-boot latency** on repeated calls: `opencode serve` in one terminal, then
`opencode run --attach http://localhost:4096 "..."` in another.

## Model tiers (match cost to stakes)

| Tier | Model | $/1M in/out | When |
|---|---|---|---|
| **Budget** | `openrouter/z-ai/glm-5.2` | 0.74 / 2.32 | everyday "what am I missing", quick sanity checks, high volume |
| **Default** | `openrouter/~x-ai/grok-latest` | 2 / 6 | most second opinions — different family, good bang for buck |
| **High-stakes** | `openrouter/~moonshotai/kimi-latest` | 3 / 15 | design thinking, architecture calls, subtle bugs |

A leading `~` marks an alias that auto-tracks the latest of that family — set and forget.
Prices drift, so check `opencode models --verbose` rather than trusting this table.

OpenRouter over OpenCode Zen: cheaper on open-weight models, and new ones land there
first — worth swapping in something newer when it ships.

Rule of thumb: **default to the cheap tier**, step up when being wrong is expensive. For
a genuinely important call, ask two tiers and compare: where they agree, move; where they
split is where the real question lives.

## How to ask

The better models here (Kimi, Grok) are close to frontier and reward judgment the same
way — so prompt them like you'd prompt a strong model, not like a form:

- **Give context and the goal, not a checklist.** "Poke holes in this design" beats
  "check these eight things" — a rigid rubric suppresses the independent read you're
  paying for.
- **Attach the artifact, don't describe it.** Real code, the actual diff, the failing
  output. A description of code always underperforms the code.
- **Don't lead the witness.** State the problem and constraints; don't reveal which
  option you favour. A model that agrees with a premise you smuggled in has told you
  nothing — that's how two blind spots become false consensus.
- **Say it once.** Repeating an instruction in the context blurb *and* the question
  doesn't strengthen it.
- **Long inputs first, question last** — attach the files/docs, then ask.

## Follow-ups — don't re-explain, continue

Sessions persist, so push back instead of starting over:

```bash
OPENCODE_PERMISSION='{"*":"deny","read":"allow","grep":"allow","glob":"allow"}' \
  opencode run "You said X — but what about Y?" -c -m <same model>
```

`-c` continues the last session (no ID needed); `-s <id>` targets a specific one. Worth
doing when the first answer is interesting but incomplete, or to see whether it holds its
position under pressure. Keep the same `-m` — switching models mid-session defeats the
purpose.

## Evaluate, don't relay

A second opinion is only worth the tokens if it's weighed. Report its take, then say
where you agree, where you disagree, and which you'd follow and why. Check its claims
against the actual code first — other models are confidently wrong sometimes, and they're
reasoning from a handful of attached files, not the whole repo.

## When to reach for it

- **Before committing to a non-trivial design** — ask a different model to poke holes.
- **A hard bug or perf question** — failing code plus symptoms, default or high tier.
- **Architecture comparison** — "A vs B given these constraints", high tier.
- **An adversarial read** — "argue against this plan" beats "review this plan".

Not for: routine edits (just do them), or anything needing live repo context. It's a
consult, not a co-worker.
