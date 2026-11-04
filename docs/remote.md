# Remote boxes — sandboxes and dormant agents

> **Status: planned, not built.** This is the high-level what, why and shape. The deeper brief (guardrails, prior art, build order, the handoff pattern) is `docs/fleet.md`. This doc supersedes fleet.md's *substrate* thinking: Sprites-only became a box interface with pluggable backends.

## The goal

Never run agent-written code on my Mac. Hand it a **remote box** instead. There are two shapes of box, picked by the shape of the work.

## Two pathways

### A. Ephemeral: the throwaway code-runner (Railway-style)

Spin up, drop in code or a repo, run, return output, **die**. Auto-destroy is a *feature* here. (It was the bug for the dormant case; right tool, right job.) Detach/reattach and checkpoint/fork give the calling agent a "fire it, walk away, come back for the result" handle. The box runs a cheap open coder (opencode), model-agnostic: swap the model via the OpenRouter `base_url`.
- **For:** self-contained code tasks, builds, "render this MD into a site", quick experiments.

### B. Dormant: the persistent pet (Shellbox-style)

A cheap box where an agent *lives*. It sleeps when idle and wakes on cron or HTTP. It has a standing brief, a worklog and memory, and owns a durable role rather than a one-shot task. Cron plus wake-on-HTTP is the native fit, and that half is settled.
- **For:** ambient agents, watchers, things that accumulate state over time.

## One interface, N backends

Both halves are the same primitive: **a remote box I can exec in.** So I build one box interface with pluggable backends, and the workload shape picks the backend.
- Backends: **`railway`** (ephemeral), **`shellbox`** (dormant). Fly.io Sprites, e2b and Modal are alternatives for either. The interface is substrate-agnostic.
- The **OpenRouter per-key budget rides on top of both, unchanged**: one abstraction, N backends, one budget model. `ko billing` already reads OpenRouter credits (and the Vercel AI Gateway balance); fleet spend slots in next to them.

## The control service

Something periodically wakes and checks the boxes: pokes the dormant ones, checks builds, reaps the ephemeral ones. State is **a JSON registry in `~/.config/ko`** ("I spun up 8 boxes").
- Ephemeral entries are cleaned up after their task, or on a sweep.
- Dormant entries are watched (status, last wake, cost).

It runs on the **always-on home server**, not the laptop, because a closed lid kills a laptop cron. Minimum fields per box: `id, backend, kind (ephemeral|dormant), created, task, status, url, cost`.

## Callable from anywhere: an MCP tool

Put a `spin_sandbox(code | repo) -> output` tool on **`edge-mcp.khalido.dev`**. Then Claude Code, claude.ai and my own agents can all run code in a disposable box without touching anyone's real machine. This is the clean version of the whole idea: the box interface, reachable by any agent. It composes with `ko mcp`. ko is already an MCP client; a ko MCP server (`mcp_server.py`) is scaffolded but unwired.

## Why a ko CLI (not "just use Claude Code")

It's not CLI versus Claude Code. ko is the **control plane**: it defines the precise verbs and composes with the rest of kotools (integrations, toolsets, memory, budget). opencode and Claude Code are interchangeable **engines** ko drives. I get the precision without rebuilding the coding agent.

The honest cost is **maintenance**: a bespoke system I carry, for a thing I'm not sure I'll use. That is a fair trade for an experimental personal setup where the precision and the building are the point. "It works exactly how I think about it" is a payoff no off-the-shelf harness gives.

## Thin slice first

One `ko` verb that spins a Railway sandbox, drops a file in, runs it, and returns stdout. That single tool proves the code-runner pattern, is useful on day one, and shows whether the dormant half is worth building *before* I commit to it. MCP exposure, the registry, the control service and the dormant backend all layer on after.

## Ideas

- **A box builds a microsite** from an MD file and a prompt, and hands back a URL. This ties to the artifact-factory idea in `fleet.md`: durable source in git, disposable render on the box.
- **ko routes by intent.** "A 30-second script" goes ephemeral; "watch my calendar daily" goes dormant. I state the intent and ko picks the backend.
- **`ko box status`** is the overview pattern for the fleet. An agent glances at worklogs and builds across the boxes and says what needs my attention (the same move as `ko mcp overview`, pointed at the fleet).
- **Cost as a first-class readout.** Extend `ko billing` so fleet spend shows next to credits: "8 boxes, $0.40 today."

## Open questions

- Is **Railway** the right ephemeral substrate, or Sprites, e2b or Modal? Confirm the box API and idle pricing before wrapping. It's newish; don't wrap a moving target.
- Where the control service runs (home server, leaning yes).
- How results come home: git push, PR or R2 (see the handoff pattern in `fleet.md`).
- Does the **dormant half earn its keep**, or is ephemeral plus cron enough? The thin slice answers this.
- Budget and guardrails: a per-run dollar cap and a verification gate (machine-checkable done), carried from `fleet.md`.

## See also

- **`docs/fleet.md`**: verification-gate-first guardrails, prior art (shire, agentsmesh, swarm-protocol, Block BuilderBot, Broccoli, Ralph/loop-engineering), the durable-source / disposable-render handoff pattern, and a 5-step build order.
