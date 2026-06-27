# Remote boxes — sandboxes & dormant agents (planning talk)

> **Not building this yet — getting thoughts in order.** This is the high-level "what & why & shape."
> The deeper brief (guardrails, prior art, build order, the handoff pattern) lives in `docs/fleet.md`;
> this doc supersedes its *substrate* thinking (Sprites-only → a box interface with pluggable backends).

## What we're trying to do
Never run agent-written code on my Mac — hand it a **remote box**. Two shapes of box, picked by the
shape of the work:

## The two pathways

### A. Ephemeral — the throwaway code-runner (Railway-style)
Spin up → drop in code/repo → run → return output → **die**. The instinct: an agent should never write
or run code on my machine; give it a disposable box. **Auto-destroy is a *feature* here** (it was the bug
for the dormant case — right tool, right job). detach/reattach + checkpoint/fork give the calling agent a
real *"fire it, walk away, come back for the result"* handle. Runs a cheap open coder (opencode),
model-agnostic — swap the model via the OpenRouter `base_url`.
- **For:** self-contained code tasks, builds, "render this MD into a site", quick experiments.

### B. Dormant — the persistent pet (Shellbox-style)
A cheap box where an agent *lives* — sleeps when idle, wakes on cron / HTTP. Standing brief + worklog +
memory; it owns a durable role rather than a one-shot task. Cron + wakeup-on-HTTP is the native fit;
that half's basically settled.
- **For:** ambient agents, watchers, things that accumulate state over time.

## The unifying insight: one interface, N backends
Both halves are the same primitive — **"a remote box I can exec in."** So don't build two systems:
build **one box interface** with pluggable backends and let the workload shape pick the backend.
- Backends: **`railway`** (ephemeral), **`shellbox`** (dormant). Fly.io Sprites / e2b / Modal are
  alternatives for either — the interface is substrate-agnostic, which is the point.
- The **OpenRouter per-key budget rides on top of both, unchanged.** One abstraction, N backends, one
  budget model. (`ko billing` already reads OR credits — fleet spend slots in next to it.)

## The control service
Something that periodically wakes/checks them: poke the dormant ones, check builds, reap the ephemeral
ones. State = **a simple JSON registry in `~/.config/ko`** ("I spun up 8 boxes"):
- ephemeral entries → cleaned up after their task (or on a sweep);
- dormant entries → kept an eye on (status, last-wake, cost).
Runs on the **always-on home server**, not the laptop (lid-close kills a laptop cron). Minimal per-box
fields: `id, backend, kind (ephemeral|dormant), created, task, status, url, cost`.

## Make it callable from anywhere: expose as an MCP tool
Put a `spin_sandbox(code | repo) -> output` tool on **`edge-mcp.khalido.dev`**. Then Claude Code,
claude.ai, *and* my own agents can all run code in a disposable box without touching anyone's real
machine. That's the clean version of the whole thing — the box interface, reachable by any agent. Composes
with `ko mcp` (ko is already an MCP client; `ko mcp serve` later makes it a server too).

## Why a ko CLI for this (not "just use Claude Code")
It's **not CLI vs Claude Code.** ko is the **control plane** — it defines the precise verbs and composes
with the rest of kotools (integrations, toolsets, memory, budget). opencode / Claude Code are
interchangeable **engines** ko drives. You get the precision without rebuilding the coding agent.
Honest cost: **maintenance** — a bespoke system you carry, for a thing you're "not sure you'll use." Fair
trade for an experimental personal setup where the precision and the building *are* the point; "it works
exactly how I think about it" is a payoff no off-the-shelf harness gives.

## Sequencing — the thin slice first
Build the smallest proof: **one `ko` verb that spins a Railway sandbox, drops a file in, runs it, returns
stdout.** That single tool proves the whole code-runner pattern, is useful day one, and tells you whether
the dormant-fleet half is worth building *before* you commit to it. Everything else — MCP exposure, the
registry, the control service, the dormant backend — layers on after that proves out.

## What would be cool (riffing)
- **Box builds a microsite** from an MD + prompt and hands back a URL — ties to the artifact-factory idea
  in `fleet.md`: durable source in git, disposable render on the box.
- **ko routes by intent:** "this is a 30-second script" → ephemeral; "watch my calendar daily" → dormant.
  State the intent, ko picks the backend.
- **`ko box status` = the overview pattern, for the fleet:** an agent glances at worklogs/builds across the
  boxes and tells you what's worth your attention (same move as `ko mcp overview`, pointed at the fleet).
- **Cost as a first-class readout:** extend `ko billing` so the fleet's spend shows up next to OR credits —
  "8 boxes, $0.40 today."

## Open questions
- Is **Railway** actually the right ephemeral substrate, or Sprites / e2b / Modal? Confirm the box API +
  idle pricing before wrapping (it's newish; don't wrap a moving target).
- Where the control service runs (home server — leaning yes).
- How results come home (git push / PR / R2 — see the handoff pattern in `fleet.md`).
- Does the **dormant half earn its keep**, or is ephemeral + cron enough? The thin slice answers this.
- Budget/guardrails: per-run dollar cap + verification gate (machine-checkable done) — carry from `fleet.md`.

## See also
- **`docs/fleet.md`** — the deeper brief: verification-gate-first guardrails, prior art (shire,
  agentsmesh, swarm-protocol, Block BuilderBot, Broccoli, Ralph/loop-engineering), the durable-source /
  disposable-render handoff pattern, and a 5-step build order.
