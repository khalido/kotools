# OpenCode agents

Curated [OpenCode](https://opencode.ai) agent definitions, installed onto a machine by
`ko setup opencode` → `~/.config/opencode/agents/`. Source of truth is here; edit here,
install, commit. Docs: https://opencode.ai/docs/agents/

These exist so another agent (Claude Code, usually) can shell out for a second opinion
from a different model family without being able to touch anything.

| Agent | Model | For |
|---|---|---|
| `consult` | grok-latest | second opinion, poke holes, adversarial read |
| `design` | kimi-latest | UI/UX for SvelteKit + Tailwind + shadcn-svelte + LayerChart |
| `review` | grok-latest | code review — correctness and security, no style nitpicks |

Invoke: `opencode run "question" --agent design -f file.svelte`. `-m` overrides the
pinned model.

## Conventions for these files

- **Markdown, YAML frontmatter, filename = agent name.** `mode: primary` so they're
  directly invocable via `--agent` (subagents are for `@`-mention/delegation instead).
- **Read-only by default.** Every agent sets `"*": deny` then allows `read`, `grep`,
  `glob`. Permission keys match as wildcard patterns against tool names, so `*: deny`
  also strips MCP tools — without it, OpenCode's default `build` agent runs `*: allow`
  and can edit files and reach every configured MCP server. Open extra doors one at a
  time and say why (`design` allows `webfetch` for the shadcn-svelte and LayerChart
  llms.txt entry points).
- **Use `permission`, not the legacy `tools` boolean key.**
- **Pin the `model`** to the job; leave sampling settings alone. Temperature and friends
  are tuned by the labs and second-guessing them rarely helps — what to tune is the
  model choice and the brief.
- **Project-specific agents** belong in that repo's `.opencode/agents/`. This cuts both
  ways: a repo's `.opencode/agents/<name>.md` **overrides** these global definitions and
  can hand back full permissions — verified. Check for `.opencode/` before running
  against a repo you don't control.
- **Read-only is not the same as private.** Everything these agents read goes to a
  third-party model via OpenRouter, and `design` can fetch URLs too. Pointing one at a
  client repo is a deliberate decision, not a default.
- **Write the brief for an agent audience.** These are consumed by another agent, not a
  human — answer what was asked, stop, and let the caller follow up in-session. Say so
  in the brief; models pad otherwise.
- **Nothing client-specific and nothing private** — this repo is public. Stack
  preferences and taste are fine; client names, pricing, MCP topology and
  machine-specific paths are not. Client-specific tweaks belong in that client's repo
  under `.opencode/agents/`.
- Don't add an agent named after a built-in unless deliberately overriding it. On
  1.18.10 those are `build`, `plan`, `general`, `explore`, `compaction`, `summary`,
  `title` — check `opencode agent list` rather than trusting this list.

## Gotcha

`opencode agent list` shows only global permission rules — custom agents look identical
to `build` there even when their overrides are live. Don't trust it; test behaviour
instead (ask the agent to write a file and confirm it refuses).
