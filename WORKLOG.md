# WORKLOG — ko

Newest first. Big picture only — git commits have the detail; candidate ideas and decisions live in `docs/ideas.md`.

## 2026-06-25

- **`ko publish` hardening + scaffold polish** (post-Opus-review on the `--hono` tier):
  - Fixed a real bug — `ko publish <hono-dir> --name foo` rewrote the worker's `wrangler.jsonc`
    with the *static* config, dropping `main`/`./public`/`KO_PIN` (gated site → public, served repo
    root). `deploy()` now routes to the hono config writer when the folder is a worker.
  - `run_worker_first` is now `bool(pin)` — required for a gated site (else `/README.md` bypasses
    the PIN), wasteful for an open one (assets serve from the edge).
  - **PIN rotation** `ko publish --pin new|<digits>`; gate cookie bumped 30→90 days.
  - **Scaffold styling**: static `style.css` now has a base element layer (so plain HTML looks
    good) + `.card`/`.pill` starters; heavy JS split into its own `app.js` module (mount `<div>` +
    `<script type=module>`); grey-text guard in the CLAUDE.md (keep prose ≥ zinc-400). Same
    component-split guidance for `--md`/`--hono`.
  - **Cached data endpoint** in the `--hono` scaffold: `/api/data` caches an upstream API at the
    edge (Cache API, lazy/access-driven, `DATA_TTL`) — no cron, no KV. Keys stay server-side.
  - **`ko publish preview [dir]`** — `wrangler dev` over real http (ES modules + `fetch()` work,
    unlike `file://`); runs the real worker for `--hono`.
  - **`.assetsignore`** for static/md/bare (assets dir is `.`) so the `.wrangler/` preview dir,
    `wrangler.jsonc` and `CLAUDE.md` aren't served at the site root.
  - Docs: Cloudflare setup-for-another-user (token template + DNS perm), preview/`file://` note,
    and `docs/publish-ai.md` capturing the (deferred) "simple `ask(prompt)` on a published site"
    plan — Workers AI (zero key) vs OpenRouter (secret-on-deploy).

## 2026-06-22

- **Renamed `ko-tools` → `kotools`** (folder + PyPI name; command stays `ko`; `kotools` is free on PyPI). Recreated the venv — the folder rename orphaned console-script shebangs.
- **Slimmed to `pydantic-ai-slim[google,openrouter]`** and dropped the global `prerelease = "allow"`: scoped prereleases to just the pydantic-ai v2 beta line (its sibling `pydantic-graph` referenced explicitly), so pydantic/mcp resolve to stable again. Loosened the beta pin to track new betas; upgraded all deps + bumped floors.
- **OpenRouter in `ko llm`** — `openrouter:` models aren't in pydantic-ai's static `known_model_names()`, so we fetch OR's public `/models` (no auth) and cache it (24h TTL) to feed `-m` autocomplete. New `ko models [--refresh]`.
- **`config.toml [keys]`** — API keys as a fallback to env (env wins), injected into the environment at startup; `ko doctor` now shows each key's source (env/config/missing) and lists all tools.
- **Live tests for exa/tv/x** (x behind `KO_LIVE_TESTS`, paid reads). The x live test caught a real bug — the XDK returns dicts not objects; fixed with a dict-or-object accessor. Found `TMDB_READ_ACCESS_TOKEN` isn't exported to subprocesses (config.toml fixes it).
- **Agent layer refactored to toolsets** — tools declared once as `FunctionToolset`s (`agents/_toolsets.py`: web/papers/news/tmdb), agents compose subsets (`research.py`, `tv.py`), shared run/stream/repl in `_shared.py`. v2 research (subagents on the refs): code factories beat YAML agent-spec (specs can't reference custom toolsets); capabilities deferred. Fixed `run_stream_sync` (not a context manager in v2); made tools resilient (one flaky source no longer aborts a run); per-agent models with a working `-m` (passed per-run, not baked in).
- **Sessions** (`sessions.py`) — every agent turn dumped to `~/.local/state/ko/sessions/<id>.json` (full trace via `ModelMessagesTypeAdapter`); `-r <id>` resumes, `ko agent sessions` lists. Flat files, no DB yet.
- Both agents live-verified (research across HF/arxiv/HN; tv on gemini-flash). README + CLAUDE.md updated.

## 2026-06-13

- **Adopted XDG-style dir split** (`src/ko/dirs.py`, researched against httpie/yt-dlp/llm/poetry): `~/.config/ko` = synced config, `~/.local/state/ko` = tokens + id caches (google token + x cache auto-migrate there — avoids llm's everything-in-one-synced-dir anti-pattern), `~/.cache/ko` = disposable. Env overrides on all three.
- **Shipped `ko doctor`** — rich table of every tool: what it does, what it needs (key/binary/auth), current status; plus the count of models `-m` can tab-complete given the keys in env.
- **Shipped `ko fetch`** — the long-designed universal URL → markdown: trafilatura for articles, arxiv links → arxiv2md, PDF links → ~/Downloads + liteparse, Wayback fallback for dead/empty pages. Bare `ko <url>` routes here. All four paths live-verified. (Wayback availability API quirk: rejects percent-encoded url= — query built by hand.) Then added archive.today for the paywall case: dead-link→Wayback, paywall→archive.today-then-Wayback, `--archive-is` to force; best-effort (datacenter IPs hit its CAPTCHA, residential works).
- **Shipped `ko llm`** — stdin-aware one-shot (no tools ever): `ko hn item 123 | ko llm "summarize"`. v2-pattern model-less Agent, default `google:gemini-3.5-flash`, `-m` tab-completes only models whose env key is set. Live-verified. Every Layer-1 command now pipes into analysis.
- **Shipped `ko tv`** — movie/TV quick check (rating, overview, regional watch providers, AU default), ported from my chota-bot TypeScript tool. TMDB v4 bearer token, free tier; providers data is JustWatch via TMDB. Live-verified.

## 2026-06-12

- **Shipped `ko x search|list|lists`** — X via the official XDK (tweepy rejected: unmaintained). `list <name>` reads my lists by name (the daily habit), name→id cached to spare rate limits; `ko x ai` bare shortcut via the main() dispatcher. Home timeline skipped (needs OAuth user-context, not app-only bearer). Live test pending a token in env.
- **Switched to pydantic-ai v2 (2.0.0b7)** before building the agent layer — v2 is harness-first with capabilities as the core primitive, which is exactly the ko ai design. Zero code changes needed (research agent + tests pass as-is); pinned exact beta, un-pin at stable v2.
- Pre-publish cleanup: GPL-3.0-or-later license, private references redacted, live arxiv test now opt-in (`KO_LIVE_TESTS=1`), PyPI urls + classifiers.
- Research day, findings in `docs/ideas.md`: HF papers coverage measured (complement to arxiv, not a mirror); paperswithcode.co turns out to have a full undocumented API (`/openapi.json`) → `ko pwc` candidate; Exa Monitors assessed (runs are pollable — no webhook infra needed for v1). Decided on two per-tool artifacts: `docs/<tool>.md` knowledge base (why this library, alternatives, pricing, experiments) and `skills/<name>/SKILL.md` agent-facing usage skills (agentskills.io format, wrappable as pydantic-ai Capabilities).

## 2026-06-11

- **Shipped `ko hf`** — Hugging Face paper pages: `top` (Daily Papers by upvotes), `search` (semantic), `info` (metadata + linked code/models/datasets), `get` (markdown). No auth. Same ids as arxiv, so it composes with `ko arxiv fetch`.
- **Shipped `ko hn`** — Hacker News via Algolia: `top` (hckrnews-style top-N by points), `search` (12-month default), `item` (comment tree as text). No auth.
- **Shipped `ko doc`** — PDF/Office/image → plain text via liteparse (local, no models), plus the bare-arg shortcut: `ko paper.pdf` routes to `doc`.
- First commits. Package renamed `ko-tools` (PyPI `ko` is squatted), command stays `ko`. Installed editable as a uv tool; fixed `arxiv2md` resolution (uv tool installs don't put dependency scripts on PATH — resolve from our own venv).
- Started `docs/ideas.md` as the single candidate list (backlog moved there from this file).

## 2026-04-24

- Drafted `docs/pydantic-ai.md` — agent plan. Picked pydantic-ai over Claude Agent SDK / smolagents / LangChain: model-agnostic, typed outputs, thin runtime. Sandbox deferred until we add a code-execution tool.

## 2026-04-22

- Scaffolded repo: Python 3.14, `uv`, `typer`, hatchling. Ported `exa` + `arxiv` from a private predecessor CLI; added `gsheets` + `google_auth` (OAuth installed-app flow, readonly scopes, token cached at `~/.config/ko/google_token.json`).
- Domain modules kept transport-agnostic (no printing, no file I/O) so a future MCP server imports them alongside `cli.py`; wiring stub in `src/ko/mcp_server.py`.
- MCP structured-output finding: FastMCP auto-serializes dataclasses to a full JSON schema, but per-field descriptions require pydantic `BaseModel` + `Field(description=...)` — migration deferred until we feel the pain.

## Open

- [ ] **kotools cloud backend (Cloudflare Worker + D1 + R2 on khalido.dev)** — store useful state in the cloud, not just locally: the publish registry (today `~/.local/state/ko/publish.json` → `ko publish list`), session summaries, agent memory. Local stays source-of-truth/cache; cloud is the sync + shared layer agents can read/write. Natural home for the `ko sessions summarize` index (D1 instead of local SQLite). This is also the worker that the publish tool's `--hono`/D1/R2 path would build on.
- [ ] **Cloudflare Sandbox for agent code execution** — <https://developers.cloudflare.com/sandbox/> — a sandboxed place for agents to run throwaway code without local blast radius (the "agent writes + runs a script" need). Pairs with the cloud backend.
- [ ] **`ko sessions summarize` → SQLite (`~/.local/state/ko/ko.db`)** — after a session, run a cheap-model pass that writes a row: `title`, a one-line `summary` (lightweight memory — the useful takeaway), `tags` (open-ended; refine the useful set over time), plus id/agent/model/timestamps. Enables "take me back to that one" and tag/topic filtering ("anything about python or HN"). The JSON session files stay the source of truth; SQLite is the index + memory layer. pydantic-ai does the summarize/tag pass.
- [ ] **PyPI trusted publisher** — tag-push GitHub Action (`publish.yml`), OIDC, no long-lived tokens: https://docs.pypi.org/trusted-publishers/
- [ ] **MCP server (`ko mcp`)** — shared-core-functions approach: CLI and MCP both wrap the same module functions. stdio for local clients; HTTP on Railway later. Stub + notes in `src/ko/mcp_server.py`.
