# Yaad — the always-on assistant

> Status: design, nothing built. Rewritten 2026-08-01 after a three-agent review pass:
> an independent (Opus) critique of the first draft, a line-by-line fact-check of the
> [`refs/qm`](https://github.com/yc-software/qm) findings, and a mapping of `~/code/ops`.
> Inputs also include `~/code/yaad` (my 2025 second brain), `refs/hermes-agent`,
> [Karpathy's append-and-review note](https://karpathy.bearblog.dev/the-append-and-review-note/),
> and the `LOG.md` running in `~/code/uts-tmos-robot`. qm/hermes takeaways: `refs/CLAUDE.md`.

**One assistant. Stores split by trust, not by topic. ko is what it can do.**

- **ko** — the existing libraries, CLI subcommands and `FunctionToolset`s. Unchanged shape:
  one module per domain → CLI → agent tool.
- **the brain** — a private git repo of markdown (`~/brain`). Captures, project notes, Yaad's
  memory. This is the only store that ever syncs to a cloud instance.
- **Yaad** — a persistent pydantic-ai agent on a cheap tier, reachable over Telegram, holding
  the brain and summoning ko's specialist agents when a job needs one.

There is no "service agent that other agents call". Local Claude Code doesn't message Yaad —
it reads the brain's files and runs `ko` commands directly. **The repo is the API.** That is
the main argument for markdown-first, ahead of "we can migrate to SQLite later".

## The trust boundary (decided 2026-08-01)

"Everything is an operation" is right at the *agent* layer and wrong at the *storage* layer.
One Yaad handles work and personal alike — but what the always-on instance can hold is a
trust decision, and both reviews landed on the same line:

- **brain** — personal: captures, project notes, website, Yaad's memory. Syncs to the cloud
  instance. This is the always-on substrate.
- **ops** — stays its own repo, **never cloned to the cloud box**. Yaad works on it (invoice
  loop, PDFs, inbox routing, STATUS/WORKLOG updates) only in interactive laptop sessions.
  Two reasons found independently: `docs/business.md` holds real banking details that no
  documented workflow ever needs in an LLM context, and a client security questionnaire's
  "is our data on third-party infra / sent to third-party LLMs?" must stay a "no". ops is a
  control panel, not a content store — client work lives in per-client repos regardless, so
  merging would have consolidated nothing.
- **client repos** — untouched by Yaad. The ops `.claude/settings.json`
  `additionalDirectories` pattern already covers "one coordinator reads many repos" for
  laptop Claude Code sessions.
- **Credentials follow the same line** (stage 3): the cloud box gets a narrow, read-mostly
  key set. Gmail compose/send, gsheets write, publish — laptop only. The full ko keychain on
  a Telegram-reachable VM is the whole digital write-access behind one bot token.

## The brain repo

```
brain/
  inbox/2026-08-01-1432-some-link.md   # capture: one timestamped file, no filing decision
  projects/website/                     # one folder per project/area
  projects/python-agents/               #   each may carry its own CLAUDE.md
  projects/masters/
  memory/log.md                         # append-and-review: what happened (Yaad-owned)
  memory/memory.md                      # the anchor: who I am, standing facts (Yaad-owned)
  memory/<topic>.md                     # detail notes the log links out to
```

Project-first, because all three consumers want it: humans think "the robot-arm project",
Claude Code hangs per-project context on a subtree's CLAUDE.md, and a grepping model scopes
"everything about X" to one directory. The exception is capture: at capture time the project
is often unknown, and forcing the choice kills capture speed — yaad's entire value. So
capture lands unfiled in `inbox/`, one file per capture (timestamped name — two clones can
never conflict on it), and a periodic pass files things into `projects/`. ops already
converged on exactly this shape (root `inbox.md` + project subdirs); trust the convergence.

Content front matter (unchanged from v1):

```yaml
---
title: ...
url: https://...
kind: article | paper | video | doc | note
source: telegram | cli | agent
tags: [python, agents]
saved: 2026-08-01
published: 2026-07-15   # optional, from the source
summary: one line
---
```

**No embeddings, and no SQLite, until grep demonstrably fails.** qm runs a multi-tenant
company agent on substring-AND over markdown plus a byte-capped recency tail — verified, with
one nuance worth keeping: its single escalation past grep was Postgres `tsvector` lexical
FTS for the Slack cache, still not vectors. So the ladder is grep → FTS5-as-rebuildable-
projection → (probably never) vectors. The libSQL/vector track in `docs/memory.md` is
**superseded** by this for Yaad.

## Memory: the append-and-review log

Karpathy's note and the uts-tmos `LOG.md` are the same pattern discovered twice — once for a
person, once for a project — and it replaces the first draft's two-stage distill/prune
machinery with something simpler:

- **`memory/log.md`** — newest-first entries, tagged, an **Open** section pinned at the top
  for unresolved threads. Only *useful things that happened* — decisions, corrections, facts
  learned — one entry each, linking out to `memory/<topic>.md` or `projects/` files for
  detail. Wrong entries are corrected in place, never silently deleted.
- **`memory/memory.md`** — stays what `agents/_memory.py` already ships: the small anchor
  injected every run (pin marker, tail-survives truncation). The log is *what happened*; the
  anchor is *what's standing*.
- **Review is the maintenance job**: periodically (cron, or when the log crosses a size
  line) Yaad re-reads the log bottom-up — rescue what still matters toward the top or into
  the anchor/topic notes, merge duplicates, let the rest sink. That's Karpathy's review
  mechanized, and it's one prompt, not a pipeline. Extraction discipline comes from qm
  (verified verbatim in `strategies/per-turn.ts`): record only what I actually stated —
  never assistant inference, never secrets — stricter still on autonomous runs.
- **When the review prompt matters enough to defend, give it qm's bench** — fixture
  conversations named for the failure they provoke (`stale-fact-supersession`,
  `secrets-exclusion`, `inference-bait`), cheap judge, floor. Corrected fact: qm built this
  but never wired it into CI — it's a hand-run script there. Wiring the floor in is our
  improvement, not a copy.

## Two writers, one store: the ownership rules

Local Claude Code and Yaad share the brain. They never talk; they follow ownership:

- **Yaad owns `memory/`.** Claude Code (and any other agent) reads it freely, writes never —
  anything it wants remembered goes into `inbox/` as a capture for Yaad to fold in. One
  writer per mutable file kills the whole conflict class.
- **The hot path is append-only, one file per event** (`inbox/`). Different files never
  conflict; git syncs them for free.
- **CAS guard on memory writes** — qm's `revisionToken` pattern (sha256 of content,
  write-only-if-hash-unchanged, `memory-service.ts:26-38`): cheap two-writer clobber
  protection with no database, for the day the ownership rule is accidentally broken.
- **Counters and run-state live in `~/.local/state/ko/`**, never in synced markdown. qm's
  in-document HTML-comment counters are correct in its single-writer world and a textbook
  lost-update bug across two clones.
- **Sync is non-ff-aware**: `pull --rebase` (append-only files rebase cleanly); on a real
  conflict, write a `.conflict` copy and message me on Telegram — never auto-resolve, never
  force-push.
- **Interrupted-tool marker** (qm, `context-compaction.ts:19-20`): if the daemon restarts
  mid-tool-call, the replayed history gets an explicit "outcome not recorded — check before
  redoing anything with side effects". An always-on bot *will* die mid gmail/gsheets call.

## Where the daemon lives

Deferred deliberately — and made cheap to defer. hermes-agent is the working precedent for
the shape kotools could grow into: one open-source repo holding tools, libs, targeted
subagents and one controlling agent with a chat gateway, while **all** state, secrets and
memory live outside the repo. ko already has that discipline (`dirs.py`: config in
`~/.config/ko`, state in `~/.local/state/ko`, brain in its own private repo) — so the daemon
*code* carries no more secrets than `gmail.py` does, and an eventual open-sourcing of
kotools is compatible with either home.

Default: **start inside kotools** (`ko yaad serve` or similar) with one design rule — the
daemon module imports only ko's public library surface, as an external repo would. Then
extraction later is mechanical, and the decision never has to be made under pressure.

## Engine: pydantic-ai plus the harness (added 2026-08-01)

[pydantic-ai-harness](https://pydantic.dev/docs/ai/harness/) is the official capability
library (cloned in `refs/pydantic-ai-harness`), and it turns several of this plan's MISSING
items into adopt-don't-build candidates. It's deliberately young — a separate package so it
can break API faster than core — so the rule is: **pin the version, adopt one capability at
a time, only where it beats what exists or replaces a planned build.**

- **[CodeMode](https://pydantic.dev/docs/ai/harness/code-mode/)** wraps eligible tools into
  one `run_code`: the model writes Python that fans out with `asyncio.gather`, filters and
  transforms locally, and returns only what matters — N tool calls in one model round-trip,
  compact history. For a cheap-model Telegram assistant this is the biggest cost/latency
  lever available. ko's toolsets are already `FunctionToolset`s; opting in is
  `.with_metadata(code_mode=True)` per toolset. **Monty is an orchestration sandbox, not a
  build environment** — allowlisted stdlib, no filesystem, no network, no pip; it can never
  build a mini app. Tag the read-side toolsets first (web, papers, news, brain search);
  keep memory writes and anything approval-gated as native calls, visible turn-by-turn.
  Sandboxed calls still dispatch through pydantic-ai to host tools, so per-tool events (the
  typing indicator) survive.
- **Capabilities that supersede planned builds — fit-check before writing a line:**
  `conversation_search` (exactly the qm `history` tool — the agent searching its own past
  sessions), `memory` (injected notebook + on-demand files, a `FileStore` backend and
  `MemoryConflictError` — the CAS this plan wanted; candidate to back or replace
  `_memory.py` *if* its FileStore can point at `brain/memory/` — own the store, adopt the
  mechanism), `shell` + `filesystem` (the write-side trio), `subagents` (stage 5),
  `compaction` (the `throughSeq` pattern for long Telegram threads), `skills`.
- This ends CLAUDE.md's standing deferral ("capabilities/lifecycle hooks deferred until we
  need request/tool interception") — Yaad is that need.

## Stages

### 0. `ko brain` — capture, no Telegram

`ko brain add <url|text>` routes through `ko fetch` (arxiv, YouTube transcripts, PDFs, DOIs,
trafilatura, Wayback — already better than yaad's Exa+Gemini capture). Writes one
front-mattered file into `inbox/`. Then `search`, `list --tag`, `file` (inbox → project),
`sync` (pull --rebase, commit, push).

Useful alone even if nothing else ships — the test of a correct first slice. Tagging stays
cheap: whatever the fetch/Exa summary gives, plus anything I typed alongside the link. No
hand-rolled tag heuristics (yaad grew three brittle ones; none worth porting). Search ships
with tests from day one — yaad's `ILIKE '%q%'` placeholder stayed known-weak for a year;
that's the failure mode to design against.

### 1. Yaad on Telegram, locally — with the safety rails

`python-telegram-bot`, **long polling** — no webhook, no public URL, and the identical
process later runs on a cloud box unchanged.

Safety is stage-1, not polish, because this is the moment Yaad can act unattended:

- **One allowed Telegram user id.** A line of code, checked first.
- **Daily spend ceiling** backed by `ko billing` — `request_limit=30` guards a run, not a
  runaway day of crons and follow-ups. Ceiling hit → bot pauses and says so.
- **Fail closed when unattended** (qm, verified `run-trigger.ts`): an autonomous run that
  hits an approval-gated command (`gmail draft send`, `gsheets delete-tab`, `publish rm`)
  fails and reports, never self-approves. The existing TTY-confirm gates already refuse in a
  pipe; this makes it a rule rather than an accident.
- Fetched/captured content is **data, never instructions, never a direct memory source** —
  a captured page or forwarded message must not be able to write Yaad's memory.

Worth copying from old yaad's Telegram layer: refresh the typing indicator after every tool
call; map tool activity onto message reactions. Handle a message arriving mid-turn
deliberately (qm routes `engage | steer | drop`, verified `wake.ts:18-30`) rather than
letting a fast follow-up spawn an overlapping reply.

Memory: run on the already-shipped `agents/_memory.py` anchor from day one. The log + review
job is stage 4 — don't build maintenance machinery before there's anything to maintain.

### 2. Cron and proactive messages

`ko brief` already exists — this is largely "push it to Telegram at 7am".

Guardrails as literal numbers from qm (verified `schedule.ts`): reject sub-60s intervals;
reject raw-ms intervals ≥24h in favour of `{cron, timezone}` (raw ms drifts across DST);
self-heal next-fire from `(schedule, createdAt, lastFiredAt)` alone. From old yaad's runner:
mark a job started *before* executing so the next tick can't double-fire; recover anything
still marked running on boot.

### 3. Cloud (Railway or a small VM)

Same process, same command, long polling. Clone **only the brain** on boot; sync per the
ownership rules. Narrow credential set (see trust boundary) — write+send capabilities stay
on the laptop. Either instance can be the awake one; `memory/` maintenance runs on exactly
one of them.

### 4. The review job

The append-and-review pass, on a schedule: fold `inbox/` items Yaad captured into
`projects/`, rescue/merge/sink `memory/log.md`, prune the anchor against its budget. Cheap
tier drafts, capable tier edits — and this is the point where the bench + CI floor gets
built, attached to a prompt that's now worth defending. Steal qm's self-degrading
consolidator: if a read-back doesn't match what was written, disable the path and log once.

### 5. Specialists and mini apps

Two different sandboxes — don't conflate them. CodeMode/Monty is Yaad's *orchestration*
sandbox and can't build anything. The mini-app builder is a coding **subagent** (harness
`subagents`, or the existing `ko agent` pattern) with shell + filesystem in a real
workspace — the laptop or VM directly (one operator, no isolation problem), with harness
`modal_sandbox` as the escalation if isolation is ever wanted.

Where a proven app *runs*: never in a sandbox — sandboxes are workshops, not runtimes. The
runtime already exists: `ko publish` → a Cloudflare Worker with a sticky URL
(`<name>.khalido.dev`), and a Telegram Mini App is just that URL behind a `web_app` button.
App-side scheduled work = Cloudflare cron triggers inside the Worker; agent-side scheduled
work = Yaad's stage-2 cron; data = D1/R2 via the `--hono` scaffold. Build → publish → link
in Telegram; Yaad only touches the app again to rebuild it.

## Codebase readiness (audited 2026-08-01)

The "daemon imports only ko's public surface" bet checks out — nearly every domain module is
already a clean library function under a thin CLI (`fetch.fetch()` returns a dataclass, the
toolsets call it library-style, sessions do an atomic load→run→save loop). Gaps concentrate
exactly where the stages expect them, plus one Stage-0 surprise:

**Stage 0 pre-work (the only real blockers):**

- **`Fetched` carries no metadata** (`fetch.py:55-59` — text/source/note only). The front
  matter wants `title` and `published`, and the capture path never sees them (it uses
  trafilatura directly, not Exa). Extend `Fetched` with optional `title`/`published` via
  trafilatura's metadata extraction, or parse the H1 and leave `published` optional. `kind`
  derives from `Fetched.source`: arxiv→paper, yt→video, pdf→doc, live/wayback→article.
- **Hoist `atomic_write` (tmp + `os.replace`) into `dirs.py`.** The idiom exists in
  triplicate (`sessions.py:90`, `llm.py:183`, `x.py:177`) and is missing exactly where it
  matters most — `_memory.py`'s writers use bare `open("a")`/`write_text`.
- **Brain search builds on the `_files.grep` ripgrep template** (`_files.py:125-169`) as a
  sibling, not a reuse (that one is containment-guarded to `~/code`). Tests from commit one.
- `sync` borrows the git-subprocess plumbing already in `refs.py:135-165`.

**Stage 1 builds (everything else is composition over existing primitives):**

- **Event-streaming run variant** — `_shared` exposes only text deltas today; the typing
  indicator and reactions need per-tool-call events, which pydantic-ai's `agent.iter()`
  provides but nothing in ko surfaces. The real Stage-1 build.
- **Spend-ceiling poller** — no accounting to write: OpenRouter's `/key` endpoint already
  returns `usage_daily` and `billing.openrouter()` already reads it (`billing.py:56-60`).
  Poll it, pause the bot at the line, say so on Telegram.
- **`run_headless()`** — `_shared.run` prints to stdout unconditionally; the daemon calls
  `agent.run_sync` + `sessions.save/load` directly, or `run` grows a no-print mode. Small.
- Session continuity is READY as-is; resume already rebuilds dynamic context because
  instructions are `@agent.instructions` functions recomputed every run — the old Open
  question about stale resumed context is answered by code that already exists.
- Fail-closed is composition: the daemon's toolset simply excludes gated commands, and the
  existing non-TTY refusals catch the rest. Daemon logging is one flag: loguru
  `enqueue=True` (the sync-write assumption is per-command CLI-shaped, `logs.py:63`).

**Known contradiction to resolve when building the log:** `append_memory` is newest-*last*
with tail-survives truncation (`_memory.py:148-157`); `log.md` is newest-*first* with a
pinned Open section. The log needs its own insert-below-Open tool and a head-keeping
injection — the anchor tools carry over unchanged. And `_memory.py` has neither CAS nor
atomic writes yet (Stage-1+ items, correctly absent today).

**Cloud warts, noted not fixed:** `fetch`'s archive.today fallback CAPTCHAs datacenter IPs
(Wayback still works from a VM); `DOWNLOADS` hardcodes `~/Downloads` but only on the
`save=True` path brain doesn't use.

### Tools Yaad needs that ko deliberately lacks

qm's registry is 13 tools; most overlap ko or are multi-tenant. The rest cluster into one
fact: **every ko toolset is read-only by construction, and an assistant that acts needs a
write side.** For Yaad, in rough order:

- **`execute` + `write` + `background`** — scoped shell exec, durable file writes, and
  start/poll/**watch** for long jobs (watch pushes output to the conversation instead of
  the model polling). The structural gap; deliberately absent from `ko ai`, correctly so.
  Adopt-first candidates: harness `shell` + `filesystem` (see Engine).
- **`history`** — the agent searching its *own* past sessions mid-run ("what did we decide
  about X last week"). The SQLite index already exists; today only humans can query it.
  Adopt-first candidate: harness `conversation_search`.
- **`stay_silent` / `finish_silently`** — explicit end-turn-say-nothing, split by reason
  (declined to reply vs. a cron fire found nothing). The cure for an always-on bot
  narrating "nothing happened" at 7am. Nearly free to add.
- **Standing orders** — qm's `guidance`: "when X happens, do Y" policy kept separate from
  the facts notebook. One more file in `memory/`, auto-injected like the anchor.

Registration patterns to fold into the docstring-as-contract rule: truncation notices that
teach the recovery path ("refetch narrower, or redirect to a file and read it in pieces"),
pagination footers naming the exact next offset, *policy* defaults stated inline (not just
filtering defaults), and a screening hook on external content for the day Yaad reads
forwarded messages from anyone who isn't me.

**Packages: nothing new now.** Stage 1 adds `python-telegram-bot` and (version-pinned)
`pydantic-ai-harness[codemode]`; stage 2 adds `croniter` (or APScheduler) with job rows in
the existing `ko.db`. qm's Postgres/pg-boss/AWS-microVM
stack is multi-tenant weight; their Slack socket-mode choice independently confirms
dial-out-not-webhook.

## Not doing

- Merging ops (or any client material) into the cloud-synced store. THE trap flagged by both
  reviews — a business-credibility risk created for zero benefit.
- WhatsApp, and anything from old yaad's pywa layer.
- Multi-user: tiers, billing, JWT web auth, invite flow. One operator, one Telegram id.
- A dashboard. If a browsing UI is ever wanted, `ko publish` builds one over the same files.
- qm's multi-tenant substrate (scope hierarchy, dual backends, HMAC, leader election,
  Terraform) and hermes's seven terminal backends. Precedents, not dependencies.
- Embeddings, SQLite, libSQL/vector (supersedes `docs/memory.md`'s v2 track for Yaad) —
  until grep visibly fails, then FTS5 as a rebuildable projection first.
- The Claude Agent SDK as default engine. Cheap pydantic-ai now; escalation is a later hook.

## Open

- The daemon-home default (in kotools, extraction-ready) is a default, not a decision — Ko
  is deliberately sitting with it. Revisit when stage 1 starts.
- Whether `memory/log.md` stays one file (Karpathy: single file wins for Ctrl+F and review
  gravity) or splits when it grows. The uts-tmos one is healthy at ~100 entries; revisit at
  10× that.
- Long-running sessions: resume already rebuilds dynamic context (see readiness audit), but
  qm's `throughSeq` summary-compaction is the pattern to reach for when a Telegram thread's
  history outgrows the context window.
