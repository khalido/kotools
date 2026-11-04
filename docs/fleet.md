# kotools Agent Fleet — handoff brief

> **Status: future direction, not built.** None of `sprites.py`, `fleet.py` or `ko fleet` exists. Fleet rides on top of `ko ai` (built), the MCP and Telegram front-ends, and the always-on home server. Do not start before those exist. Captured 2026-06-26; reactions and risks are at the bottom.
>
> **Read `docs/remote.md` first.** It is the higher-level planning talk: the two pathways, the one-interface/N-backends model, the thin-slice sequencing. The substrate has since moved from Sprites-only to a **box interface with pluggable backends** (railway for ephemeral, shellbox for dormant). This doc is the deeper brief: guardrails, prior art, the handoff pattern, build order.

Goal: a small fleet of mostly-dormant specialist agents on Sprites, supervised by a smart control agent.

## Operating model

- **Bursty and dormant, not endless loops.** Each agent wakes, does ~10 minutes of useful work, writes state, and sleeps until the next schedule or poke. No Ralph-style continuous burn. Token spend is bounded by short bursts, not by a budget I police.
- **Standing brief and worklog per agent**, not a finite PRD. The agent owns a durable role. The control agent reviews output and redirects ("also do X", "improve this").
- **Cheap workers, smart controller.** Workers run on DeepSeek (or similar) via OpenRouter. The control agent runs on Claude for judgment and triage. opencode and pi both route to OpenRouter.

## Infra decisions (settled)

- **Substrate: Fly.io Sprites.** Idle is free (compute is removed when inactive), wake-on-request takes under 1s, Firecracker microVM isolation, a public URL per box. One Sprite per agent.
- **No permissions inside the box** (`--dangerously-skip-permissions`). That is safe because the Sprite boundary plus an egress allowlist is the real containment, not the agent's prompt gate.
- **No Telegram for the control plane.** (The Telegram *front-end* in ideas.md is a separate, human-facing layer.) The control plane is direct HTTP or `exec` via the Sprite API. State lives on the box (git history, worklog, `status.json`), and the controller reads it on each poke.
- **No Python SDK for Sprites.** Wrap the REST API in `ko` (a thin httpx client).

## Agent types (mix, decide per agent)

- **Coding boxes:** opencode or pi on a Sprite, for real code work.
- **World-facing agents:** lighter. Basically a `ko` task (arxiv, hn, exa, gmail, gsheets, x …) wrapped in a standing brief and a memory file. They may not need a full coding agent.
- **Site agent:** serves a site on its Sprite URL, sleeps when idle, wakes on load. The control agent feeds it content or prompts it to build custom interfaces, and it rebuilds itself. Free at rest, alive on demand.

## kotools changes

- `src/ko/sprites.py`: REST wrapper for create, exec, url, checkpoint, restore, network policy.
- `src/ko/fleet.py`: registry and lifecycle (provision, dispatch, status, retire, sweep). Persist the registry via the existing `config.py`, `dirs.py` and `sessions.py` (SQLite).
- `src/ko/agents/fleet.py`: the control/supervisor pydantic-ai agent (triage and direction).
- `src/ko/agents/_toolsets.py`: add `fleet_toolset` (list, dispatch, status, tail, spawn, checkpoint, retire), so the main `ko` agent drives the fleet in chat.
- `src/ko/cli.py`: a `ko fleet` group with up, dispatch, status, logs, poke, down.
- Optional later: an `agent-host` shim on :8080 in each box, for a uniform interface across opencode and pi. Not needed on day one; `exec` is enough for opencode alone.

## How the controller sees what happened

On each poke it reads three things via the Sprite API: opencode/pi session output (structured), `git diff --stat` (the work product), and a short worker-written summary in the worklog. It continues by resuming the session with the next instruction, with no re-explaining.

## Guardrails (build the first one on day one)

- **Verification gate**, the real bottleneck. Use machine-checkable done criteria (tests, lint, typecheck) plus a `<promise>COMPLETE</promise>`-style sigil. Subjective goals get gamed.
- **Per-run dollar budget** and no-progress detection (cap the burst).
- **Egress allowlist** per box: model provider and git host only.
- **API key, not a Max/Pro subscription.** Wrapping subscriptions isn't supported.
- If a box serves a public URL, set URL/webhook auth. Public is not open.

## Prior art to mine

- **shire:** persistent agent workspaces with inter-agent mailboxes; supports opencode and pi.
- **agentsmesh:** remote AI workstations (AgentPods), worktree isolation, BYOK, self-host.
- **swarm-protocol:** headless coordination as MCP: claim work, heartbeat, hand off.
- **Block BuilderBot** (on Goose): a fleet managed from one thread; ticket, branch, PR, CI.
- **Broccoli:** each task gets its own cloud sandbox, then a PR. The pattern, productized.
- **Ralph loop** and Cobus Greyling's **loop-engineering** repo (`loop-cost`/`loop-audit` CLIs): patterns and cost tooling. Borrow the mechanism, skip the endless burn.

## Build order

1. `sprites.py` and `ko fleet up/exec`: create a box, run a command.
2. One agent end to end on a cheap model: dispatch a task, read `status` back.
3. `fleet.py` registry and `ko fleet poke` heartbeat.
4. Control agent and `fleet_toolset`: `ko` runs the sweep and redirects agents.
5. Add the site agent (serve on :8080, wake-on-load).

## Open questions

- Where the controller lives: laptop cron (simplest) or an always-on control Sprite.
- How results come home: push a branch, open a PR, or put an artifact in R2.
- Which agents need a full Sprite and coding agent, and which are just a scheduled `ko` task.
- Per-agent standing-brief design. This is the actual high-leverage work.

---

## The handoff pattern: local, box, microsite (2026-06-26)

The first concrete use case sets the core principle: **durable source, disposable compute, disposable render.** The only irreplaceable thing is the *source* (the research MD and the build prompt). The Sprite, the build and the hosted output are all *derived* and regenerable. Design it so that is literally true, and two things follow for free:
- "Doesn't matter if the box crashes" becomes a guarantee, because the box holds no unique state.
- Throwaway becomes a *feature*. Because the source is safe, renders can be ephemeral. That is the appeal: a personal **artifact factory** of cheap hosted things I don't have to curate.

Flow:
```
local ko agent → writes research.md + a BUILD prompt → git push → poke the box
              → box clones → cheap coding agent builds (rendered doc … full project, by prompt)
              → hosts on its Sprite URL (or `ko publish`) → optionally pushes the result back
```

- **Source of truth is git** (real projects, the robot-arm kind) **or the Drive-synced folder** (quick, throwaway notes). **Not `~/.config/ko`**: that is settings, not content, and not a backup.
- **Git is both the handoff medium and the local backup.** The repo lives on the Mac, GitHub and the box at once, so no separate backup is needed. Handoff is "push and poke", which is `ko fleet dispatch`.
- **A cheap coding model is plenty** for "MD + prompt → microsite" (templated build work). Save the smart model for the controller. The spectrum from rendered doc to full project is just how much the box builds: same pipeline, different prompt.

## Reactions and risks (2026-06-26)

I back these without hesitation: **bursty and dormant** (spend bounded by short wakes; the best call here), **cheap worker with smart controller**, **verification gate first**, and the insight that **a world-facing agent is a `ko` task plus a standing brief plus memory**. That last one converges with `ko prompt`: the per-agent standing brief *is* a `ko prompt` brief, the same artifact. Design around that.

Pressure-test before building:
- **Verification generalizes for code, not for research.** Tests, lint and a sigil gate coding boxes cleanly. World-facing and research agents (the majority) have no test suite. "Good work?" is subjective and gameable. The real gate there is controller review, which is expensive judgment on every poke. Budget for the asymmetry; one gate doesn't fit both.
- **Start with 1-2 agents, not the framework.** `sprites.py`, `fleet.py`, the control agent, the toolset, the CLI and the guardrails add up to a lot. The high-value 20% is one scheduled research agent plus one coding box, run from the home server. Build the registry and lifecycle only when N agents actually hurt to manage.
- **The controller goes on the always-on home server.** Not laptop cron (a closed lid kills it), and not a control Sprite (more infra than the box I already own).
- **Confirm Sprites' REST surface and idle-free pricing** before wrapping. It's a newish product; don't wrap a moving target. Alternatives if it slips: e2b, exe.dev, Modal sandboxes.
- **Results come home differently by location.** A *local* agent can write to a Drive-for-Desktop synced folder (free backup, phone access, `ko publish --md`). A *remote* Sprite agent must use git push, a PR or R2. The Drive-sync trick does not reach a remote box.
