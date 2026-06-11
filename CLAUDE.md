# ko

Ko's personal opinionated CLI. Thin wrappers around SDKs I use often, built so both I and AI agents (Claude Code via bash) can lean on it.

## Subcommands
- `ko exa search|get` — Exa semantic web search + URL → markdown
- `ko arxiv search|fetch` — arxiv search + paper-to-markdown
- `ko gsheets info|tabs|get|auth` — read Google Sheets via OAuth

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
- OAuth user flow (desktop app). First command triggers browser consent, token cached at `~/.config/ko/google_token.json`.
- Scopes read-only by default: `spreadsheets.readonly` + `drive.readonly`.
- **Scope is per-API, not per-folder.** Google OAuth can't be restricted to a single Drive folder. Access is "anything this Google account can see." If that's too broad, use a service account (separate tool) and share specific sheets with it.
- Prereq: OAuth 2.0 Desktop client JSON at `~/.config/ko/google_client.json` (or set `KO_GOOGLE_CLIENT_FILE`). Full setup in README.

## Agent notes
- `ko --help` / `ko <cmd> --help` are the contract
- `ko gsheets get ... ` defaults to TSV — `cut -f N` safe
- Exit codes: `0` success (incl. empty results), `1` runtime error, `2` usage error
- No interactive prompts inside commands (except the one-time OAuth browser popup)

## Not yet
- `ko fetch` (URL → markdown via Jina/Exa), `ko pdf` (PDF → text), `ko q` (DuckDB SQL over JSON/CSV), `ko prompt` (files-to-prompt), `ko hn`, `ko note`, `ko scholar`. See WORKLOG for priorities.
