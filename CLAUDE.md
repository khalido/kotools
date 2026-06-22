# ko

Ko's personal opinionated CLI. Thin wrappers around SDKs I use often, built so both I and AI agents (Claude Code via bash) can lean on it.

## Subcommands
- `ko exa search|get` — Exa semantic web search + URL → markdown
- `ko arxiv search|fetch` — arxiv search + paper-to-markdown
- `ko gsheets info|tabs|get|auth` — read Google Sheets via OAuth
- `ko doc <file>` — PDF/Office/image → plain text via liteparse (local, no models). Bare shortcut: `ko <file>` routes here when the arg is an existing file.
- `ko fetch <url>` — URL → markdown, deterministic routing: arxiv→arxiv2md, PDF→~/Downloads+liteparse (`--no-save`), else trafilatura, dead/empty→Wayback (`--archive` forces). Bare shortcut: `ko <url>`.
- `ko llm "<prompt>"` — one-shot LLM, stdin-aware, never has tools. Default `google:gemini-3.5-flash` (`KO_DEFAULT_MODEL`/`-m`; `-m` tab-completes models whose env key is set). OpenRouter's catalog is fetched from its public `/models` and cached at `~/.cache/ko/openrouter_models.json` (24h TTL) so `-m openrouter:<slug>` completes.
- `ko models [--refresh]` — list model strings usable with `-m`, one per line. `--refresh` force-refetches the OpenRouter catalog past the 24h cache (for grabbing a just-released model).
- `ko hn top|search|item` — Hacker News via Algolia (no auth). `top` = hckrnews-style top 10/20 by points; `search` defaults to last 12 months; `item` = story + comment tree as text.
- `ko hf top|search|info|get` — Hugging Face paper pages (no auth). `top` = Daily Papers by upvotes; `search` = semantic; `info` = metadata incl. github/models/datasets; `get` = paper as markdown. Same ids as arxiv — composes with `ko arxiv fetch`.
- `ko tv <title>` — movie/TV rating + overview + regional watch providers (`TMDB_READ_ACCESS_TOKEN`, free; AU default, `--country`).
- `ko x search|list|lists` — X via the official XDK (`X_BEARER_TOKEN`, paid tier for reads). `list <name>` = recent posts from my list; bare shortcut `ko x ai` ≡ `ko x list ai`. Search index ≈ last 7 days.
- `ko agent research|tv|sessions` — pydantic-ai agents. `research` (web+papers+HN, capable model), `tv` (movies/TV in AU, cheap model), no prompt = interactive REPL, `-m` overrides model, `-r <id>` resumes a saved session. `sessions` lists saved sessions. See Agents below.

## Principles
- **Opinionated, not generic.** Do what I actually do, the way I do it. Defaults match my habits.
- **Thin wrappers.** Each module (`exa.py`, `arxiv.py`, `gsheets.py`) is a self-contained domain module over one upstream SDK. Keep parameter names matching the SDK so agent-written code composes with upstream docs.
- **Help lines carry their weight.** Every subcommand and flag has a concise, useful `help=`. Assume humans and agents skim `--help` and nothing else.
- **Human-readable and pipeable by default.** Plain text / TSV. Add `--json` when structure matters. Errors to stderr. `typer.Exit(0)` for "no results" (not an error).
- **One file per domain.** `cli.py` registers subapps; each module owns its logic. No shared `utils/` until three things need the same thing.
- **`--help` is the contract.** Machine-friendly output is a design constraint, not an afterthought.

## Stack
- Python 3.14, `uv`, `ruff`, `pytest`
- `typer` for the CLI
- Module-local `@dataclass` for structured returns
- `@lru_cache` for credential/service singletons

## Adding a subcommand
1. `src/ko/<thing>.py` — thin SDK wrapper + dataclass result types
2. `src/ko/cli.py` — Typer subapp + commands, every flag with `help=`
3. `tests/test_<thing>.py` — smoke test; skip live calls if env var / token missing
4. Deps in `pyproject.toml` under `[project].dependencies`

## Agents (`src/ko/agents/`)
- **Tools declared once** as `FunctionToolset`s in `_toolsets.py` (`web` = exa+fetch, `papers` = arxiv+hf, `news` = hn, `tmdb`); agents compose the subset they need. A toolset is stateless and shared by reference; each carries its own `instructions=`.
- **One file per agent** (`research.py`, `tv.py`): ~15 lines — `Agent(model, instructions=..., toolsets=[...])`. Per-agent default model (research = capable, tv = cheap); `-m`/`KO_AGENT_MODEL` overrides per run (passed to the run call, not baked into the Agent — the Agent reads its default at import).
- **`_shared.py`** — generic run/stream/repl over any Agent; streams pretty markdown to a TTY, plain text when piped.
- **Resilient tools** — each wraps its call in `_try`, returning an error note instead of raising, so one flaky source (rate limit, timeout) doesn't abort a run; the model routes to another source.
- **Sessions** (`src/ko/sessions.py`) — every turn dumps `all_messages()` (full tool trace) to `~/.local/state/ko/sessions/<id>.json` via pydantic-ai's `ModelMessagesTypeAdapter`. Resume with `-r <id>`; list with `ko agent sessions`. Flat files in state (not config — generated, not hand-edited).
- **No YAML agent-spec**: pydantic-ai specs can't reference custom toolsets, so code factories win. Capabilities (lifecycle hooks) are deferred until we need request/tool interception.

## Adding an agent
1. Add/extend a `FunctionToolset` in `agents/_toolsets.py` if new tools are needed.
2. `agents/<name>.py` — `Agent(model, instructions=..., toolsets=[...])` + thin `run`/`repl` binding `_shared`.
3. Export in `agents/__init__.py`; add a `@agent_app.command` in `cli.py`.

## Google auth (gsheets)
- OAuth user flow (desktop app). First command triggers browser consent, token cached at `~/.local/state/ko/google_token.json`.
- Scopes read-only by default: `spreadsheets.readonly` + `drive.readonly`.
- **Scope is per-API, not per-folder.** Google OAuth can't be restricted to a single Drive folder. Access is "anything this Google account can see." If that's too broad, use a service account (separate tool) and share specific sheets with it.
- Prereq: OAuth 2.0 Desktop client JSON at `~/.config/ko/google_client.json` (or set `KO_GOOGLE_CLIENT_FILE`). Full setup in README.

## Directories (`src/ko/dirs.py`)
- `~/.config/ko/` — user-editable config, dotfile-sync-safe (`KO_CONFIG_DIR`). `config.toml` `[keys]` table supplies API keys/tokens as a fallback to env vars (env always wins); loaded into the environment at startup so SDKs that read `os.environ` directly pick them up. `ko doctor` shows each key's source: env / config / missing.
- `~/.local/state/ko/` — tokens + id caches, never synced (`KO_STATE_DIR`); old config-dir state files auto-migrate
- `~/.cache/ko/` — disposable (`KO_CACHE_DIR`)
- `ko doctor` shows per-tool setup status (keys set, binaries found, auth done)

## Agent notes
- `ko --help` / `ko <cmd> --help` are the contract
- `ko gsheets get ... ` defaults to TSV — `cut -f N` safe
- Exit codes: `0` success (incl. empty results), `1` runtime error, `2` usage error
- No interactive prompts inside commands (except the one-time OAuth browser popup)

## Not yet
- **`ko sessions summarize`** → local SQLite (`~/.local/state/ko/ko.db`): one-line summary + tags per session, as a lightweight memory + filter layer ("show me sessions about python/HN"). See WORKLOG Open.
- **MCP server (`ko mcp`)** — `mcp_server.py` is scaffolded but not wired; expose the same module functions over MCP.
- **default agent** (all toolsets) + **tmdb lists by id** in config. Full candidate list + priorities in `docs/ideas.md`.
