# WORKLOG — ko

Newest first. Big picture only — git commits have the detail; candidate ideas and decisions live in `docs/ideas.md`.

## 2026-06-13

- **Adopted XDG-style dir split** (`src/ko/dirs.py`, researched against httpie/yt-dlp/llm/poetry): `~/.config/ko` = synced config, `~/.local/state/ko` = tokens + id caches (google token + x cache auto-migrate there — avoids llm's everything-in-one-synced-dir anti-pattern), `~/.cache/ko` = disposable. Env overrides on all three.
- **Shipped `ko doctor`** — rich table of every tool: what it does, what it needs (key/binary/auth), current status; plus the count of models `-m` can tab-complete given the keys in env.
- **Shipped `ko fetch`** — the long-designed universal URL → markdown: trafilatura for articles, arxiv links → arxiv2md, PDF links → ~/Downloads + liteparse, Wayback fallback for dead/empty pages. Bare `ko <url>` routes here. All four paths live-verified. (Wayback availability API quirk: rejects percent-encoded url= — query built by hand.)
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

- [ ] **PyPI trusted publisher** — tag-push GitHub Action (`publish.yml`), OIDC, no long-lived tokens: https://docs.pypi.org/trusted-publishers/
- [ ] **MCP server (`ko mcp`)** — shared-core-functions approach: CLI and MCP both wrap the same module functions. stdio for local clients; HTTP on Railway later. Stub + notes in `src/ko/mcp_server.py`.
