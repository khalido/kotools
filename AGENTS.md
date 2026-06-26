# AGENTS.md — driving `ko` from a shell

`ko` is an opinionated CLI of thin SDK wrappers (web search, papers, HN, Google
Sheets/Docs/Calendar/Gmail, X, publishing, LLM/agents). It's built to be driven by
agents over bash as much as by a human. This file is the contract; `ko <cmd> --help`
is the detailed contract for any one command.

## First move

Run `ko doctor` — it reports, per tool, what env/keys/binaries it needs and whether
they're present. If a command fails with an auth/key error, `ko doctor` tells you why.

## Output contract

- **stdout is data; stderr is everything else.** Results go to stdout; errors, notes,
  and "no results" messages go to stderr. You can pipe stdout into `jq`/`cut`/`awk`
  without it being polluted by status text.
- **Default format is plain text / TSV** — `cut -f N` safe. `ko gsheets get` and other
  tabular commands emit TSV.
- **`--json` for structured output.** Most read commands take `--json`. The shape is a
  **JSON array of objects** unless the command's `--help` says otherwise (a few return a
  single object — e.g. `ko tv --json` is `{top, matches}`, `ko hn item --json` is
  `{story, comments}` — and `ko gsheets get --json` is a 2D array).
  - Under `--json`, **errors are a JSON object on stderr**: `{"error": "...", "code": "..."}`.
  - Under `--json`, **empty results are `[]` on stdout** (plus a note on stderr), so a
    downstream `| jq` never chokes.

## Exit codes

- `0` — success, **including empty results** (empty is not an error).
- `1` — runtime error (network, auth, bad upstream). One clean line on stderr; no traceback.
- `2` — usage error (bad flags/args; Typer prints usage to stderr).

## Limits / pagination

- List commands take `--n` (default ~10) to bound output. Note: `ko gmail` / `ko cal`
  currently use `-n` as the short alias; most other commands use `--n` with no short form.
  When in doubt, check `ko <cmd> --help`.

## Bare-argument shortcuts (not visible in `--help`)

`ko` routes a leading bare argument deterministically (explicit command names always win):

- `ko <url>` → `ko fetch <url>` (URL → markdown)
- `ko <file>` → `ko doc <file>` (PDF/Office/image → text), when the arg is an existing file
- `ko x <name>` → `ko x list <name>` (anything after `x` that isn't an x subcommand is a list name)
- `ko <anything> help` → `ko <anything> --help` (trailing `help` prints help at any level: `ko help`, `ko exa help`, `ko exa search help`)

## Auth

Google commands (`gsheets`/`gdocs`/`cal`/`gmail`) share one OAuth token per account.
`ko gsheets auth` grants it (covers Sheets+Docs+Calendar read+write, Gmail read-only).
No browser? The only interactive prompt in the whole CLI is that one-time OAuth consent.

## Working on the code

- Python 3.14, `uv`, `ruff`, `pytest`, `typer`.
- Install/run: `uv run ko ...`. Test: `uv run pytest -q`. Lint: `uv run ruff check`.
- One module per domain under `src/ko/`; `cli.py` registers the Typer subapps. See
  `CLAUDE.md` for the full design principles and how to add a subcommand.
