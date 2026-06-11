# WORKLOG — ko

Newest first. Big picture only — git commits have the detail.

## 2026-04-24

- Drafted `docs/pydantic-ai.md` — plan for `ko agent` subcommand. Picked pydantic-ai over Claude Agent SDK / smolagents / LangChain: model-agnostic, typed outputs, composes with our existing dataclasses, thin runtime. Sandbox deferred until we add a code-execution tool — `mcp-run-python` (Pydantic team's Deno+Pyodide MCP server) is the likely first pick; E2B/Daytona/Modal if that's too limited.

## 2026-04-22

- Scaffolded repo: Python 3.14, `uv`, `typer`, hatchling build
- Ported `exa` and `arxiv` from `~/code/masters` (mx CLI) with namespace renamed to `ko`
- Added `gsheets` module + `google_auth` module (OAuth installed-app flow, readonly scopes by default, token cache at `~/.config/ko/google_token.json`)
- CLAUDE.md + README.md drafted; 5 tests pass, 1 skipped (gsheets live test, needs OAuth setup)
- Refactored `arxiv.fetch()` to be pure — dropped `out_path` side effect, file write moved into `cli.py`. All domain modules now transport-agnostic (no printing, no file I/O), ready for MCP to import them alongside `cli.py`.
- Added `src/ko/mcp_server.py` stub with wiring sketch + MCP doc links. No runtime code yet — drop in `FastMCP` + `@mcp.tool()` wrappers when ready.
- `uv add "mcp[cli]"` — Python MCP SDK 1.27.0 installed. Brings `mcp dev`/`mcp install` helpers for local debugging.
- Expanded stub with structured-output insight (FastMCP auto-serializes dataclasses — no manual `asdict()`), Railway-friendly transport config (`stateless_http=True, json_response=True`), and deploy notes.
- Verified MCP structured-output behaviour empirically + by reading `mcp/server/fastmcp/utilities/func_metadata.py:394`. Dataclasses get full JSON schema (field names, types, required/optional, nested `$ref`) but **not** per-field descriptions. `Annotated[T, Field(...)]` is stripped (`get_type_hints` called without `include_extras=True`). Pydantic `BaseModel` with `Field(description=...)` is the only way to get per-field docs into the tool schema — deferring that migration until we actually feel the pain.
- `gsheets.get_info()` now returns a `SheetInfo` dataclass (was untyped dict). All domain modules now return typed, MCP-ready shapes.

### Open

- [ ] **PyPI trusted publisher setup** — publish on tag push via GitHub Actions, OIDC (no long-lived tokens).
  - Reserve `ko` on PyPI first (one manual upload from local, or create a pending publisher against an empty project)
  - Add trusted publisher at https://docs.pypi.org/trusted-publishers/adding-a-publisher/ — owner/repo, workflow filename (`publish.yml`), environment name
  - `.github/workflows/publish.yml`: on `push: tags: ['v*']` → `uv sync`, `uv build`, `uv publish` (no token needed with trusted publisher)
  - Release flow: bump `version` in `pyproject.toml`, `git tag vX.Y.Z && git push --tags`
- [ ] **OAuth first-run** — create Desktop OAuth client in GCP, drop JSON at `~/.config/ko/google_client.json`, run `ko gsheets auth`
- [ ] **`uv tool install --editable ~/code/ko`** — put `ko` on PATH so it works from any directory

### Architecture / research

- [ ] **Deploy MCP server to Railway** — once the MCP wrapper is live locally, host it as a personal remote MCP server. Path: expose `mcp.streamable_http_app()` as ASGI, `uvicorn ... --host 0.0.0.0 --port $PORT`, set `$KO_MCP_SECRET` env var for bearer-token auth (simplest interim). Connect from Claude.ai as a Custom Connector. Re-check the auth landscape at deploy time — MCP remote spec still evolving (https://modelcontextprotocol.io/docs/develop/connect-remote-servers).
- [ ] **Expose `ko` tools as an MCP server** — same opinionated wrappers, second interface. CLI for humans + bash-pipes; MCP for AI agents (Claude Desktop, Cursor, Claude Code with MCP) calling natively. Single codebase, dual transport. Probably a `ko mcp` subcommand that runs the server (stdio by default for local Claude Desktop, optional HTTP).
  - Study MCP intro: https://modelcontextprotocol.io/docs/getting-started/intro
  - Study agent-skills scaffolding (may simplify a lot of this): https://modelcontextprotocol.io/docs/develop/build-with-agent-skills
  - Key design question: how to wire Typer subcommands → MCP tools without duplicating definitions. Three options to evaluate:
    1. **Shim layer** — MCP tools shell out to `ko <cmd>` subprocesses. Easiest, but returns strings, loses dataclass structure.
    2. **Shared core functions** — both the Typer layer and the MCP layer wrap the same module functions (`exa.search()`, `gsheets.get_range()`). Cleaner, preserves typing. My current guess for the right answer.
    3. **Auto-derive from Typer** — discover registered commands, introspect signatures, emit as MCP tools. Elegant if it works, possibly brittle.
  - Use Anthropic's Python MCP SDK as the transport
  - Each module (`exa.py`, `arxiv.py`, `gsheets.py`) should remain MCP-agnostic — only `cli.py` and a new `mcp.py` know about their respective transports

### Backlog (from research scan, priority order for Ko's stack)

- [ ] `ko fetch <url>` — URL → clean markdown (Jina Reader). Closes the loop between `ko exa` and `ko arxiv`. Trivial.
- [ ] `ko q "SELECT ..."` — DuckDB ad-hoc SQL over JSON/CSV/Parquet, no DB file. Big for Sheets export munging.
- [ ] `ko prompt <path>` — `files-to-prompt` clone. Agents lean on this pattern.
- [ ] `ko pdf <file>` — PDF → text (pymupdf). Complements `ko arxiv fetch`.
- [ ] `ko hn` — HN Algolia search, NDJSON out. Composes with `ko fetch`.
- [ ] `ko scholar` — Semantic Scholar citation graph (what arxiv can't give).
- [ ] `ko summarise` — opinionated Claude summariser (opposite philosophy to simonw/llm).
- [ ] Later: `ko clip`, `ko note`, `ko standup`, `ko schema`, `ko embed`.

### Skip (considered, not a fit)

- `ko gh` — `gh` CLI already excellent, has `--json`, Claude Code uses it natively
- `ko jira` / `ko linear` — volatile APIs, painful auth, low return
- `ko atuin` / `ko zoxide` — shell-integrated, can't meaningfully wrap from Python
- `ko translate` — existing CLIs fine; API cost doesn't justify
