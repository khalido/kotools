# Changelog

All notable changes to `ko` (kotools) are documented in this file. It is the
canonical release record — GitHub release notes are drawn from it. WORKLOG.md is
the dev journal (why/decisions); this is what changed, release-grained.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/2.0.0/).
Versioning is [CalVer](https://calver.org/), PEP 440-normalized: `YYYY.M.D`
(tag `vYYYY.M.D`; a second release the same day appends `.1`). The version covers
the **CLI contract** (commands, flags, output shapes, exit codes — see AGENTS.md);
breaking changes to it carry a `**Breaking:**` marker inline under their type.
Cut a release with the repo's `/release` skill.

## [Unreleased]

### Added

- `ko publish rm <name|dir>` — delete a published site from Cloudflare (its custom
  domain + DNS record detach with it) and forget it locally; TTY confirm, `--yes`
  when scripted.
- `ko publish list --cf` — reconcile against the Cloudflare account: appends Workers
  published elsewhere (custom domains resolved). The TSV also gains a last-published
  column.
- `--md`/`--hono` sites generate an agent-facing `llms.txt` on every publish (llmstxt.org
  shape) from the pages' front matter (`title:`/`description:`/`tags:`) or H1 + first
  paragraph; sites of ≤2 pages inline their truncated content so one fetch reads the
  whole site. A hand-authored `llms.txt` (marker line removed) is never touched; the
  md shell now strips front matter before rendering.

## [2026.7.14] - 2026-07-14

The money-and-agents release: model tiers with per-call cost notes, agents that
remember between runs, and releases that publish themselves to PyPI.

### Added

- **PyPI publishing**: a tag push now builds and publishes `kotools` via GitHub
  OIDC trusted publishing (`.github/workflows/publish.yml`, no tokens) — this
  release is the first through the pipeline; `uv tool install kotools` from
  here on.
- `ko ai "<prompt>"` — the default agent: every toolset (web, papers, HN,
  movies, read-only ~/code files) plus its own memory; medium-tier model,
  hard-capped at 30 model requests per run. One-shot or REPL.
- `ko agent repo` — repo-explorer agent: "how does repo X do Y" over `~/code`
  (read-only by construction; ripgrep search; knows the refs/CLAUDE.md map;
  cites file:line; basic tier — under a cent a run).
- Agent memory: research + repo agents keep a per-agent markdown workspace
  (`memory.md` anchor injected each run + free-form notes; append/edit tools
  with a uniqueness guard), plus a shared hand-edited `~/.config/ko/memory.md`
  injected into every memory-carrying agent (research, repo, ai). A fresh run
  recalls what a prior run saved. Hardened after a trace-eval + review pass:
  huge/minified files can't blow the context, broken symlinks and rg timeouts
  degrade cleanly, and `grep` grew the `limit` param the model reached for.
- Model tiers: `[llm] basic/medium/smart/ultra` in config.toml (`llm.model_for`) —
  basic (deepseek-v4-flash) drives llm/tv/brief/summarize defaults, smart
  (`~x-ai/grok-latest`) the research agent, ultra (gpt-5.6-sol) is reserved for
  high-stakes calls. **Changed**: the everyday default moves from gemini-flash
  direct to DeepSeek via OpenRouter — one prepaid pool, visible in `ko billing`.
- LLM calls report their cost on stderr — `[model · 66→21 tok · $0.0012]` —
  using OpenRouter's *actual* billed cost when available (genai-prices estimate,
  marked `~`, otherwise). Covers `ko llm`, agents, `ko brief`, `yt -s`; `sessions
  summarize` totals its spend.
- `ko refs` — manage the `~/code/refs` reference-repo folder: bare = parallel
  `--ff-only` pull of all clones (only moved HEADs printed), `setup` bootstraps
  the baked list + `~/.config/ko/refs.txt` on any machine, `add <url>` clones and
  remembers, `list` = TSV. `ko doctor` shows the folder's disk footprint and
  flags clones over 500MB.
- Opt-in LLM telemetry: `[telemetry] enabled = true` + `POSTHOG_API_KEY` sends
  provider-agnostic traces (model/tokens/cost) to PostHog via pydantic-ai OTel
  instrumentation; off by default, metadata-only unless `include_content = true`.
- `[llm] model` and `[agents] model` in config.toml — the default model now has
  a config home (env var still wins); `ko doctor` gains an effective-settings
  footer (each value's source: env/config/default, plus the three dirs) and
  live-checks the OpenRouter key via `/credits` — proves it works and shows
  credits left, without spending tokens.

### Fixed

- A malformed `config.toml` now warns loudly (every command + doctor) instead of
  silently ignoring all keys and settings; README documents the three dirs.

## [2026.7.9] - 2026-07-09

First tagged release — everything since the repo started (2026-04). `ko` is an
opinionated personal CLI of thin SDK wrappers, built to be driven by humans and
AI agents over bash: `--help` is the contract, stdout is data, exit codes mean
things, `--json` where structure matters.

### Added

- **Read anything**: `ko fetch <url>` — articles, PDFs, arxiv, YouTube, DOIs →
  markdown, with Wayback/archive.today fallbacks (bare `ko <url>` routes here);
  `ko doc <file>` — PDF/Office/image → text, local; `ko yt <url>` — transcripts,
  `-s` summarizes.
- **Papers**: `ko papers` — cross-publisher search + citation graph via OpenAlex
  (`search|get|cites|refs|similar`, no key); `ko arxiv` — relevance-ranked search
  + paper → markdown via arxiv2md; `ko hf` — HF Daily Papers.
- **News & social**: `ko hn` — `top` (incl. `--now` live front page), `search`,
  `item` comment trees; `ko x` — search (incl. full-archive), lists by
  name/id/URL, user timelines; `ko tt` — TickTick lists, read-only.
- **Google** (one OAuth token, multi-account): `ko gsheets` read & write with a
  clobber guard; `ko gdocs` Markdown ↔ Docs + comments; `ko cal` agenda/find/add;
  `ko gmail` read-only search/view/thread.
- **AI**: `ko llm` — one-shot, stdin-aware, `-m` any model whose provider key is
  set; `ko agent research|tv` — pydantic-ai agents with saved, resumable
  sessions; `ko agent sessions` — listing with `--tag`/`--search` filters over an
  LLM-built SQLite index (`sessions summarize`); `ko brief` — morning brief;
  `ko prompt` — kickoff briefs; `ko mcp` — inspect/call/overview/auth-info for
  MCP servers.
- **Publish**: `ko publish` — folder → Cloudflare static site (scaffolds,
  optional PIN gate, custom domains).
- **Meta**: `ko doctor` setup status; `ko billing` OpenRouter credits; `ko logs`
  local command log (no args/secrets); `ko tv` ratings + where to stream.
- **Agent contract** (AGENTS.md): stdout=data / stderr=notes, exit 0/1/2,
  `--json` errors as `{error, code}`, bare-arg shortcuts.

[Unreleased]: https://github.com/khalido/kotools/compare/v2026.7.14...HEAD
[2026.7.14]: https://github.com/khalido/kotools/compare/v2026.7.9...v2026.7.14
[2026.7.9]: https://github.com/khalido/kotools/releases/tag/v2026.7.9

