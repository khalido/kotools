# A persistent personal assistant

**Status: ideation, nothing built.** Started 2026-08-19.

The thing I actually want: an agent I can message from my phone that remembers me, has
ko's tools, and runs somewhere that isn't my laptop. yaad already does this over
WhatsApp/Telegram but carries ~14.6k LOC of harness I don't want to maintain, and has
been quiet since 2026-04-27. This doc works out what replaces it.

---

## 1. What's actually being decided

Four separable choices. They've been getting tangled together, so pull them apart:

| Decision | Options |
|---|---|
| **Engine** — what runs the agent loop | ko's pydantic-ai agents · Claude Code driving ko via bash · Claude Agent SDK (status quo) |
| **Channel** — how I reach it | Telegram · WhatsApp · both |
| **Home** — what repo, what deploy | kotools · new private repo · restructured yaad |
| **Memory** — where the state lives | git-committed files · Railway volume · both, split |

Only the engine choice is genuinely hard. The other three have clear answers below.

---

## 2. Engine

### Option A — ko's own agents (pydantic-ai)

`ko ai` already exists: all Layer-1 toolsets, its own memory at `memory/ai/`, medium
tier, 30-request cap. A channel adapter calls the same `run()` that `_shared.py`'s REPL
calls.

- **For:** zero new agent infrastructure. Toolset-is-the-allowlist by construction.
  ko's own session persistence (`sessions.py`) rather than someone else's file format.
  Cost notes already generic (`llm.run_cost`, `ko billing`). Model-agnostic via
  OpenRouter — the assistant can run on a cheap tier, which matters a lot for something
  that answers every message.
- **Against (as of 2026-08-19, largely obsolete — see §2.5):** ko's agents were a thinner
  harness than Claude Code. No sub-agents, no skills, no compaction, no todo tracking.
  Pydantic AI Harness now ships all four as capabilities, on any model.

### Option B — Claude Code in the container, driving `ko` via bash

This is the one that looks most like ko's stated design premise: *"built so both I and AI
agents (Claude Code via bash) can lean on it."* Claude Code is the loop, ko is the tool
surface, the Telegram adapter is a pipe.

**Researched 2026-08-19 — the finding that decides it:**

> Anthropic's Consumer Terms (§3.7) prohibit accessing the service "through automated or
> non-human means, whether through a bot, script, or otherwise" **except via an Anthropic
> API key**. This is reported to cover Pro, Max and Team plans.

So the appealing version of this idea — *log in with the subscription I already pay for,
get Claude Code's harness for free* — **is not available**. An unattended Telegram bot on
subscription auth is outside the terms. It has to be `ANTHROPIC_API_KEY` on API billing.

That doesn't kill Option B, but it removes its main advantage. Once you're paying API
rates, you're comparing Claude Code's harness against ko's harness on merit, and paying
Anthropic list price per message either way — where Option A can route a cheap tier
through OpenRouter.

> ⚠️ This is a policy claim that decides a whole branch. Worth reading the terms directly
> before committing, rather than taking this doc's word for it.

Mechanics if it's chosen anyway:

- `claude -p "<msg>" --output-format json --permission-mode dontAsk --allowedTools "Bash(ko *),Read"`
- Sessions: `--resume <session_id>`, files at `~/.claude/projects/<hash>/<id>.jsonl`,
  30-day default retention → needs a volume mount.
- **Resume can fail silently.** yaad hit exactly this and carries three layers of
  workaround in `agent/core.py`. Not a hypothetical.
- Concurrency: one session per process. Two messages at once = two processes. Parallel
  writes to the same session id corrupt the transcript, so session ids need to be unique
  per conversation, not per user.
- Auth in a container is `ANTHROPIC_API_KEY`, or `claude setup-token` →
  `CLAUDE_CODE_OAUTH_TOKEN` (1-year, but doesn't work in `--bare` mode). Neither is a
  browser flow, which is the part that can't work unattended.

### Option C — Claude Agent SDK (what yaad does today)

Rejected. It works by shelling out to the Claude Code CLI as a subprocess, which means
`npm install -g @anthropic-ai/claude-code` in the Dockerfile just to run the agent, plus
the resume-failure workarounds above. It also doesn't escape the API-key requirement.
This is the weight I'm trying to shed.

### 2.5 — Pydantic AI v2 + Harness closes Option A's gap

Checked 2026-08-19. kotools was pinned `>=2.21` and resolving to **2.21.0**; latest is
**2.32.0** (both v2 — the version is 2.32, not 2.3.2). Bumped, 529 tests pass, live run
fine.

v2's organising idea is the **capability**: instructions + tools + lifecycle hooks +
model settings as one composable unit you drop into `capabilities=[...]`. The first-party
**Pydantic AI Harness** (`pydantic-ai-harness`, 0.23.0) ships **53** of them — verified by
import, not from the docs page. That materially changes ko's build-vs-adopt calculus,
because a lot of `agents/` is now upstream:

| ko has, hand-rolled | Harness ships |
|---|---|
| `agents/_memory.py` — flat md dir, pinned anchor, truncation | `Memory(FileStore(dir), agent_name=…, namespace=…)` — plus `Sqlite`/`Searchable`/`Postgres` stores |
| `sessions.py` — full message dump, `-r` resume | `StepPersistence` — save/restore/resume/**fork**, file/SQLite/Mongo |
| `sessions --search` (metadata only) | `ConversationSearch` — BM25 over history *including turns compaction dropped* |
| `agents/_files.py` — symlink-safe read-only tree | `FileSystem` — path-traversal + symlink safe, secrets read-only |
| `web` toolset (Exa, search-wide-read-narrow) | `ExaSearch` — excerpted search, full reads, opt-in cited deep search |
| `llm.run_cost` / `ko billing` | `SpendLimits` — USD/token budgets **enforced**, not just reported |
| `telemetry.py` (OTel → PostHog) | `Instrumentation` (core) — same OTel GenAI spans |
| `ko prompt` briefs | `Skills` — `SKILL.md` loaded on demand |
| the repo agent's job | `RepoContext` — AGENTS.md/CLAUDE.md + repo structure |

And the things ko wanted but never had: `CodeMode`, `Planning`, `SubAgents`,
`TieredCompaction`/`SlidingWindow`/`Summarizing`, `ToolOutputLimits`, guardrails
(`InputGuardrail`/`OutputGuardrail`/`ToolGuardrail`), `SystemReminders` (cache-safe
re-injection against instruction fade), `Advisor` (consult a stronger model mid-run),
`DynamicWorkflow`, `CapabilityCreation`. There are whole prebuilt stacks too — `Coder`
and `Researcher` are just `CombinedCapability`s you can take apart.

**Code mode, measured on ko's own cheap tiers** (25 items, look up price+stock each, then
total and rank — the "call one tool 25 times" case):

| Tier | Mode | Time | Model turns | Tool calls | Correct |
|---|---|---|---|---|---|
| basic (deepseek-v4-flash) | tool calling | 34.9s | 2 | 25 | yes |
| basic | **code mode** | **24.0s** | 3 | 25 | yes |
| medium (gpt-5.6-luna) | tool calling | 7.1s | 2 | 25 | **NO** |
| medium | **code mode** | 8.1s | 3 | 25 | yes |

The failure is the finding. On medium with normal tool calling the model looked at 25 tool
results and asserted all 25 items had *identical* price and stock, then said there was no
highest item. It pattern-matched instead of reading. In code mode the same model wrote a
loop and Monty did the arithmetic — exact.

So the argument for code mode on cheap models is **correctness on aggregation**, not just
latency: cheap models are bad at arithmetic-by-eyeballing and fine at writing the loop.
Note what did *not* improve — 25 tool calls either way (it moves orchestration into Python,
it doesn't reduce invocations) and one *extra* model turn at this size.

Wiring fits ko's existing design exactly, because toolsets are declared once:

```python
web = FunctionToolset(...).with_metadata(code_mode=True)
Agent(model, toolsets=[web], capabilities=[CodeMode(tools={'code_mode': True})])
```

Per-toolset opt-in — `web`/`papers`/`news` through the sandbox, `files` and the memory
writers staying native.

**Caveats, in order of how much they should slow adoption:**

- Harness is **0.x on purpose** — "APIs may still move between minor releases". Their words.
  ko's `<3` pin on core buys stability; the harness buys none, and v2 shortened the
  no-breaking-changes window between majors from six months to **three**.
- Monty is a Python *subset*: no third-party imports, stdlib limited to
  `sys/typing/asyncio/math/json/re/unicodedata/datetime/os/pathlib`, no `time` or
  `asyncio.sleep`, filesystem needs an explicit `mount`/`os_access`.
- Doc names aren't always symbols: "Compaction" is
  `TieredCompaction`/`SlidingWindowCompaction`/`SummarizingCompaction`/`ClearToolResults`;
  "Guardrails" is `InputGuardrail`/`OutputGuardrail`/`ToolGuardrail`.
  `PromptInjectionDefender` needs a separate `[prompt-injection-defender]` extra.
- ko's versions are **built, tested and tuned to my habits**. This is item-by-item, not a
  wholesale migration, and the house rule applies: build the replacement alongside and
  verify before switching.

Worth noting the overlap with §10's hermes steals: `ConversationSearch` is the FTS-over-
history idea, already implemented and maintained upstream. That one probably shouldn't be
hand-rolled now.

### Leaning

**Option A**, and more strongly than before §2.5. The whole case for Option B was that
Claude Code has the better harness; that gap has largely closed, on any model, at
OpenRouter prices. Revisit B if the assistant turns
out to need real coding-agent capability rather than tool calls — and note that "fire a
one-off coding job at a Railway sandbox" (see §7) covers that case without making the
always-on bot depend on it.

---

## 3. Channel: Telegram, and no web server

**Telegram supports long polling.** The bot dials out to `getUpdates`; there's no inbound
endpoint, no public HTTPS, no domain, no webhook secret, no health check. One process,
runs identically on a laptop, a Railway worker, or a Pi.

This deletes FastAPI from the design. yaad needs it because it serves WhatsApp — whose
Business API is **webhook-only, no polling** — plus a dashboard. Telegram alone needs
neither.

- Library: `python-telegram-bot` 22.8 (yaad pins `~22.7`, so it's proven here). Handles
  offset tracking, backoff, media. Rolling it by hand against `getUpdates`/`sendMessage`
  is two endpoints but you re-implement the annoying parts.
- yaad's handler is 601 lines because it's tangled with multi-user routing, a DB, media
  preprocessing and a command system. Single-user with no DB is a fraction of that.

**Security is the first line of code, not a hardening pass.** A bot token is a bearer
credential and anyone who finds the bot can message it — and `ko ai` has every Layer-1
toolset plus memory *write*. Whitelist one Telegram user id, hard-refuse everything else,
before any agent call happens.

**Three things that always bite:** 4096-char message cap (agent replies exceed it, so
split); MarkdownV2 needs escaping that LLM output breaks constantly, so plain text or
HTML parse mode; typing indicator during the run, because a tool-using turn takes 20s and
silence reads as broken.

---

## 4. Home

ko's dirs are already redirectable, which makes the scaffold shape nearly free:

```
KO_CONFIG_DIR=/app/private/config   # config.toml (keys), memory.md (shared context)
KO_STATE_DIR=/app/private/state     # memory/<agent>/*.md, sessions/, ko.db
```

`memory_dir(agent)` → `state_dir()/memory/<agent>` (`agents/_memory.py:34-39`),
`state_dir()` honours `KO_STATE_DIR` (`dirs.py:25`). Point them at a checkout and ko's
agents are reading and writing inside a private repo with no code change.

Open question — where the *adapter* lives:

- **Split** (adapter in kotools, private repo is config+memory+Dockerfile). No duplicated
  harness, adapter is testable from ko, nothing private in the public repo.
- **All private.** Cleanest separation, but the ~100 lines of generic glue live where
  they can't be reused or tested.
- **All in kotools.** Simplest, but kotools is public — memory has to live outside it
  anyway, so this only defers the split.

Undecided. Leaning split.

---

## 5. Memory

Split by write frequency, because git is a bad database:

- **Slow memory** → git. The distilled `memory.md` that a nightly review rewrites.
  Versioned, diffable, revertable when the agent records something wrong, portable off
  the platform. One commit a day.
- **Fast state** → volume/SQLite. Per-message logs, sessions, cost ledger. A commit per
  message is noisy history and races the nightly job.

yaad today has **zero git-awareness** — `data/` is gitignored and persistence is entirely
a Railway volume. So the commit-and-push step is net-new. ko's only git usage is
`refs.py` (`git pull --ff-only` via subprocess), which sets the pattern for how, not the
plumbing itself.

Two things to decide deliberately:

1. **Git history is permanent.** `memory.md` holds personal facts. A bad write stays in
   history after the file is fixed. Private repo makes that tolerable, not moot.
2. **Push auth from a container** is a deploy key or PAT with write access to the repo,
   sitting in Railway env.

---

## 6. What ko would have to grow

From the yaad audit, most of its tools are thin personal-API wrappers — weather, bus,
family calendar, image generation, news cache — that port to `_toolsets.py` entries
almost mechanically. Two things don't:

- **Self-scheduling** (yaad's `job` tool: the agent creates, pauses and resumes its own
  recurring runs, with per-job model and tool restrictions). ko has no concept of
  scheduling at all. This is the real gap, and it's what makes an assistant more than a
  request/response bot.
- **The channel itself.** Can't be a toolset; it's the front door.

Everything else — dynamic system-prompt assembly, session resume, memory files, cost
tracking — ko already has, generally in a better-designed form.

---

## 7. Where Railway sandboxes fit (they don't, here)

Separate idea, worth not conflating: **the assistant can't be a sandbox.** It needs a
public webhook endpoint if it uses WhatsApp, and sandboxes explicitly have no domain and
no TCP proxy. With Telegram polling it doesn't need an endpoint — but it does need to run
continuously, and sandboxes are killed by an idle timeout that counts *your* interactions,
not what's running inside, capped at 120 minutes.

So: **the assistant is a Railway service.** Sandboxes are the right primitive for the
other thing — fire-and-forget jobs ("write code to do X, return the result, die"), where
the default image already ships Claude Code, Codex, OpenCode and Pi, and the loop is
`create --checkpoint` → `exec` → `destroy`. An assistant *tool* that dispatches a sandbox
job is a plausible bridge between the two, later.

---

## 8. What to do with yaad

Last commit 2026-04-27. Rewriting a dormant 14.6k-LOC service is a big lift to justify on
architecture grounds alone. The useful residue is: *a scheduled ko agent with memory that
can message me*. Built fresh that's plausibly a week; restructured out of yaad you inherit
the dashboard, the tier system, the DB migrations and the phone-auth model whether you
want them or not.

Worth salvaging: the personal tool wrappers (weather/bus/calendar/news), the two-stage
nightly memory review (cheap model digests the day, better model edits `memory.md`), and
the `job` scheduling design.

Worth killing: multi-user tiers, the web dashboard, WhatsApp (unless it's the point),
phone-number identity, the Claude Agent SDK dependency.

---

## 9. Open questions

- [ ] Read the Consumer Terms directly and confirm §2's finding before ruling out
      subscription auth.
- [ ] Adapter in kotools or in the private repo?
- [ ] Which agent does the bot drive — `ko ai`, or a new personal agent that inherits
      yaad's tools?
- [ ] Is WhatsApp actually wanted? It's the only reason FastAPI comes back.
- [ ] Does the assistant need self-scheduling in v1, or is external cron enough to start?

## 10. Prior art in `refs/`

Takeaways landed in `refs/CLAUDE.md`; the ones that change this design:

**`hermes-agent`** (NousResearch) — a persistent multi-platform assistant, so it has
already hit every problem below. Verdict is the same shape as `qm`: steal the mechanisms,
leave the architecture (1.74M LOC, ~300 commits/day, a funded multi-tenant product with 8
pluggable memory-SaaS backends and 20+ channel adapters).

- **Memory overflow is handled in-turn, not by truncation.** A write past the limit is
  rejected with the *full current entry list* and "merge, shorten, drop stale, retry now";
  a batch action validated only against the final result lets the model consolidate and
  add atomically. A 3-strikes-per-turn breaker stops a stuck consolidation burning the
  whole budget. ko's `_memory.py` truncates instead — this is strictly better and small.
- **Data-loss guards worth copying verbatim.** If on-disk content wouldn't round-trip the
  parser (I hand-edited it, or another session wrote), the write is *refused* and a
  `.bak.<ts>` taken. A file that exists but fails to read is treated as **unknown, never
  empty** — so a transient I/O blip can't wipe memory. ko has no equivalent, and ko's
  memory files are exactly the kind I'd hand-edit.
- **Limits in chars, not tokens** — model-independent. And entries freeze into a
  system-prompt snapshot at session start, so mid-session writes stay durable on disk
  without breaking the prompt-cache prefix.
- **Deterministic session keys** — `agent:main:{platform}:{chat_type}:{chat_id}` routes
  each conversation to an isolated lane. Plus `suspended` (hard reset) vs `resume_pending`
  (soft recovery preserving session_id across an unclean shutdown) as distinct restart
  states. Ready-made vocabulary for §3's gateway.
- **For §6's scheduling gap:** a cron **notepad** — byte-capped per-job KV (16KB/value,
  64KB/job) for cursors and watermarks across wake-ups, capped precisely because it's
  re-injected into every wake-up's prompt. Cron output delivers back to *origin*, the chat
  that created the job — that's the proactive-message mechanism. And an explicit
  **"outcome not recorded"** marker for a tool call interrupted by a restart: second
  sighting after `qm`, and a real concern for anything that can die mid gmail-send.
- **Skip:** micro-compaction (hermes ships it *off* — documents the prompt-cache cost as
  not universally worth it), the memory-provider abstraction, the injection pattern
  library (a multi-tenant threat model, not a single-operator one).

**`openclaw`** — self-hosted multi-channel assistant; the closest thing in `refs/` to what
this doc describes. Read before hand-rolling a gateway.

Adapter-design company, none of it Python: **`chat`** (vercel) routes one core across 10+
platforms via a typed adapter catalog; **`channels-sdk`** (CopilotKit) is the reference for
*approval gates inside the conversation*, which is worth thinking about for an unattended
agent with write tools; **`grammY`** is the most Telegram-specific; **`eve`** covers the
durability angle.

### What this adds to the plan

Two things move from "later" to "do it when the memory writer is next touched", because
they're small and they protect data I can't reconstruct: **drift detection /
refuse-on-unreadable** and **in-turn consolidation instead of truncation**. Neither
depends on any of the assistant work landing.
