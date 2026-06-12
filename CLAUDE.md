# ko

Ko's personal opinionated CLI. Thin wrappers around SDKs I use often, built so both I and AI agents (Claude Code via bash) can lean on it.

## Subcommands
- `ko exa search|get` — Exa semantic web search + URL → markdown
- `ko arxiv search|fetch` — arxiv search + paper-to-markdown
- `ko gsheets info|tabs|get|auth` — read Google Sheets via OAuth
- `ko doc <file>` — PDF/Office/image → plain text via liteparse (local, no models). Bare shortcut: `ko <file>` routes here when the arg is an existing file.
- `ko fetch <url>` — URL → markdown, deterministic routing: arxiv→arxiv2md, PDF→~/Downloads+liteparse (`--no-save`), else trafilatura, dead/empty→Wayback (`--archive` forces). Bare shortcut: `ko <url>`.
- `ko llm "<prompt>"` — one-shot LLM, stdin-aware, never has tools. Default `google:gemini-3.5-flash` (`KO_DEFAULT_MODEL`/`-m`; `-m` tab-completes models whose env key is set).
- `ko hn top|search|item` — Hacker News via Algolia (no auth). `top` = hckrnews-style top 10/20 by points; `search` defaults to last 12 months; `item` = story + comment tree as text.
- `ko hf top|search|info|get` — Hugging Face paper pages (no auth). `top` = Daily Papers by upvotes; `search` = semantic; `info` = metadata incl. github/models/datasets; `get` = paper as markdown. Same ids as arxiv — composes with `ko arxiv fetch`.
- `ko tv <title>` — movie/TV rating + overview + regional watch providers (`TMDB_READ_ACCESS_TOKEN`, free; AU default, `--country`).
- `ko x search|list|lists` — X via the official XDK (`X_BEARER_TOKEN`, paid tier for reads). `list <name>` = recent posts from my list; bare shortcut `ko x ai` ≡ `ko x list ai`. Search index ≈ last 7 days.

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

## Google auth (gsheets)
- OAuth user flow (desktop app). First command triggers browser consent, token cached at `~/.local/state/ko/google_token.json`.
- Scopes read-only by default: `spreadsheets.readonly` + `drive.readonly`.
- **Scope is per-API, not per-folder.** Google OAuth can't be restricted to a single Drive folder. Access is "anything this Google account can see." If that's too broad, use a service account (separate tool) and share specific sheets with it.
- Prereq: OAuth 2.0 Desktop client JSON at `~/.config/ko/google_client.json` (or set `KO_GOOGLE_CLIENT_FILE`). Full setup in README.

## Directories (`src/ko/dirs.py`)
- `~/.config/ko/` — user-editable config, dotfile-sync-safe (`KO_CONFIG_DIR`)
- `~/.local/state/ko/` — tokens + id caches, never synced (`KO_STATE_DIR`); old config-dir state files auto-migrate
- `~/.cache/ko/` — disposable (`KO_CACHE_DIR`)
- `ko doctor` shows per-tool setup status (keys set, binaries found, auth done)

## Agent notes
- `ko --help` / `ko <cmd> --help` are the contract
- `ko gsheets get ... ` defaults to TSV — `cut -f N` safe
- Exit codes: `0` success (incl. empty results), `1` runtime error, `2` usage error
- No interactive prompts inside commands (except the one-time OAuth browser popup)

## Not yet
- Next up: `ko fetch` (URL → markdown, Wayback fallback) and `ko llm` (stdin-aware one-shot) — the pair that unblocks `ko ai`. Full candidate list + priorities in `docs/ideas.md`.
