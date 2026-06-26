# kotools Agent Fleet — Handoff Brief

> **Status: future direction (far layer).** This rides on top of `ko ai` (the agent layer) +
> the MCP/Telegram front-ends. Dependency order: auth → `ko brief` → `ko ai` → MCP/front-ends →
> fleet. Do **not** start before `ko ai` exists. Captured 2026-06-26; reactions/risks at the bottom.

A concise context dump for the `ko` agent. Goal: stand up a small fleet of mostly-dormant
specialist agents on Sprites, supervised by a smart control agent.

## Operating model
- **Bursty + dormant, not endless loops.** Each agent wakes, does ~10 min of useful work,
  writes state, sleeps until the next schedule or poke. Avoid Ralph-style continuous burn —
  token spend is bounded by short bursts, not by a budget we police.
- **Standing brief + worklog per agent**, not a finite PRD. The agent owns a durable role;
  the control agent reviews output and redirects ("also do X", "improve this").
- **Cheap workers, smart controller.** Workers on DeepSeek (or similar) via OpenRouter;
  control agent on Claude for judgment/triage. opencode and pi both route to OpenRouter.

## Infra decisions (settled)
- **Substrate: Fly.io Sprites.** Idle is free (compute removed when inactive), wake-on-request
  <1s, Firecracker microVM isolation, public URL per box. One Sprite per agent.
- **No permissions inside the box** (`--dangerously-skip-permissions`). Safe because the
  Sprite boundary + egress allowlist is the real containment, not the agent's prompt gate.
- **No Telegram.** Control plane = direct HTTP / `exec` via the Sprite API. State lives on
  the box (git history + worklog + `status.json`); the controller reads it on each poke.
- **No Python SDK for Sprites** — wrap the REST API in `ko` (thin httpx client).

## Agent types (mix, decide per agent)
- **Coding boxes**: opencode or pi on a Sprite, for real code work.
- **World-facing agents**: lighter — basically a `ko` task (arxiv/hn/exa/gmail/gsheets/x …)
  wrapped in a standing brief + memory file. May not need a full coding agent.
- **Site agent** (nice pattern): serves a site on its Sprite URL, sleeps when idle, wakes on
  load. Control agent feeds it content or prompts it to build custom interfaces; it rebuilds
  itself. Free at rest, alive on demand.

## kotools changes
- `src/ko/sprites.py` — REST wrapper: create, exec, url, checkpoint, restore, network policy.
- `src/ko/fleet.py` — registry + lifecycle (provision, dispatch, status, retire, sweep).
  Persist registry via existing `config.py`/`dirs.py`/`sessions.py` (SQLite).
- `src/ko/agents/fleet.py` — control/supervisor pydantic-ai agent (triage + direction).
- `src/ko/agents/_toolsets.py` — add `fleet_toolset` (list, dispatch, status, tail, spawn,
  checkpoint, retire) so the main `ko` agent drives the fleet in chat.
- `src/ko/cli.py` — `ko fleet` group: up, dispatch, status, logs, poke, down.
- Optional later: an `agent-host` shim on :8080 in each box for a uniform interface across
  opencode/pi. Not needed day one — `exec` is enough for opencode alone.

## How the controller "sees what happened"
Reads three things via the Sprite API on each poke: opencode/pi session output (structured),
`git diff --stat` (work product), and a short worker-written summary in the worklog. Continues
by resuming the session with the next instruction — no re-explaining.

## Guardrails (build the first one on day one)
- **Verification gate** = the real bottleneck. Machine-checkable done criteria (tests/lint/
  typecheck) + a `<promise>COMPLETE</promise>`-style sigil. Subjective goals get gamed.
- **Per-run dollar budget** + no-progress detection (cap the burst).
- **Egress allowlist** per box: model provider + git host only.
- **API key, not a Max/Pro subscription** (wrapping subscriptions isn't supported).
- If a box serves a public URL, set URL/webhook auth; public ≠ open.

## Prior art to mine
- **shire** — persistent agent workspaces with inter-agent mailboxes; supports opencode + pi.
- **agentsmesh** — remote AI workstations (AgentPods), worktree isolation, BYOK, self-host.
- **swarm-protocol** — headless coordination as MCP: claim work, heartbeat, hand off.
- **Block BuilderBot** (on Goose) — fleet managed from one thread; ticket → branch → PR → CI.
- **Broccoli** — each task its own cloud sandbox → PR. The pattern, productized.
- **Ralph loop** + Cobus Greyling's **loop-engineering** repo (`loop-cost`/`loop-audit` CLIs)
  for patterns and cost tooling — borrow the mechanism, skip the endless burn.

## Build order
1. `sprites.py` + `ko fleet up/exec` — create a box, run a command.
2. One agent end-to-end on a cheap model: dispatch a task, read `status` back.
3. `fleet.py` registry + `ko fleet poke` heartbeat.
4. Control agent + `fleet_toolset` — let `ko` run the sweep and redirect agents.
5. Add the site agent (serve on :8080, wake-on-load).

## Open questions
- Where the controller lives — laptop cron (simplest) vs an always-on control Sprite.
- How results come home — push a branch / open a PR / artifact to R2.
- Which agents need a full Sprite + coding agent vs. just a scheduled `ko` task.
- Per-agent standing-brief design — the actual high-leverage work.

---

## The handoff pattern — local → box → microsite (2026-06-26)

The first concrete use case, and it sets the core principle:

**Durable source → disposable compute → disposable render.** The only irreplaceable thing is the
*source* (the research MD + the build prompt). The Sprite, the build, and the hosted output are all
*derived* and regenerable. Design so that's literally true, and two things follow for free:
- "Doesn't matter if the box crashes" becomes a guarantee — the box holds no unique state.
- Throwaway becomes a *feature*: because the source is safe, renders are free to be ephemeral. That's
  the appeal — a personal **artifact factory**, cheap hosted things you don't have to curate.

Flow:
```
local ko agent → writes research.md + a BUILD prompt → git push → poke the box
              → box clones → cheap coding agent builds (rendered doc … full project, by prompt)
              → hosts on its Sprite URL (or `ko publish`) → optionally pushes the result back
```

- **Source of truth = git** (real projects, the robot-arm kind) **or the Drive-synced folder**
  (quick/throwaway notes). **Not `~/.config/ko`** — that's settings, not content, and not a backup.
- **Git is the handoff medium AND the "local backup":** the repo lives on the Mac + GitHub + the box
  at once, so no separate backup is needed. Handoff = "push + poke" = `ko fleet dispatch`.
- **Cheap coding model is plenty** for "MD + prompt → microsite" (templated build work). Save the
  smart model for the controller. The rendered-doc ↔ full-project spectrum is just how much the box
  builds — same pipeline, different prompt.

## Reactions / risks (2026-06-26)

Backed without hesitation: **bursty+dormant** (token spend bounded by short wakes, not a policed
budget — the best call here), **cheap-worker/smart-controller**, **verification-gate-first**, and
the load-bearing insight that **a world-facing agent = a `ko` task + standing brief + memory** —
which converges with `ko prompt`: **the per-agent standing brief IS a `ko prompt` brief.** Same
artifact. Design around that.

Pressure-test before building:
- **Verification generalizes for code, not for research.** Tests/lint + a sigil gate coding boxes
  cleanly. World-facing/research agents (the majority) have no test suite — "good work?" is
  subjective and gameable. The real gate there is controller-review, which is expensive judgment
  on every poke. Budget for the asymmetry; don't pretend one gate fits both.
- **Start with 1–2 agents, not the framework.** `sprites.py`+`fleet.py`+control agent+toolset+cli
  +guardrails is a lot. The high-value 20% is one scheduled research agent + one coding box, run
  from the home server. Build the registry/lifecycle only once N agents actually hurt to manage.
- **Controller on the always-on home server**, not laptop cron (lid-close kills it) and not a
  control Sprite (more infra than the box you already own).
- **Confirm Sprites' REST surface + idle-free pricing** before wrapping — newish product, don't
  wrap a moving target. (Alternatives if it slips: e2b, exe.dev, Modal sandboxes.)
- **Results-come-home differs by location:** a *local* agent can write to a Drive-for-Desktop
  synced folder (free backup + phone + `ko publish --md`); a *remote* Sprite agent must use git
  push / PR / R2. The Drive-sync trick does NOT reach a remote box.
