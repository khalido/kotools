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
  tabular commands emit TSV, with rows padded to a true rectangle (a short row never
  shifts your column index).
- **`--json` for structured output.** Most read commands take `--json`. The shape is a
  **JSON array of objects** unless the command's `--help` says otherwise (a few return a
  single object — e.g. `ko tv --json` is `{top, matches}`, `ko hn item --json` is
  `{story, comments}`, `ko papers get --json` is a single work object, `ko hf info --json`
  is a single paper object — and `ko gsheets get --json` is a 2D array).
  - Under `--json`, **errors are a JSON object on stderr**: `{"error": "...", "code": "..."}`.
  - Under `--json`, **empty results are `[]` on stdout** (plus a note on stderr), so a
    downstream `| jq` never chokes.
- **A command that writes a file prints that file's path to stdout** — nothing else — so
  it composes: `ko gmail draft new … -a "$(ko pdf status.md)"`. Notes about the render
  (e.g. an unresolved letterhead brand) go to stderr, where they can't corrupt the path.

## Exit codes

- `0` — success, **including empty results** (empty is not an error).
- `1` — runtime error (network, auth, bad upstream). One clean line on stderr; no traceback.
  An unset API key lands here too — including mid-agent-run (`ko ai`/`ko agent`), which
  aborts rather than quietly working around the missing tool. The line names the env var
  and where to get a key; `ko doctor` lists them all.
- `2` — usage error (bad flags/args; Typer prints usage to stderr).

## Limits / pagination

- List commands take `-n` **or** `--n` (default ~10) to bound output — both work everywhere,
  so you never have to guess (`ko gmail` also still accepts `--max`). `ko cal --days/-d` is a
  time *window*, not a result count.

## Bare-argument shortcuts (not visible in `--help`)

`ko` routes a leading bare argument deterministically (explicit command names always win):

- `ko <url>` → `ko fetch <url>` (URL → markdown; YouTube URLs return the transcript, or fail cleanly when captions are off)
- `ko <file>` → `ko doc <file>` (PDF/Office/image → text), when the arg is an existing file
- `ko x <name>` → `ko x list <name>` (anything after `x` that isn't an x subcommand is a list name)
- `ko tt` (alias `ko todo`) → `ko tt lists`; `ko tt <name>` → `ko tt items <name>` (anything that isn't a tt subcommand — `lists`/`items`/`add`/`done`/`search` — is a list name)
- `ko <group> help` → `ko <group> --help` (trailing `help` prints help at **group** level, nested groups included: `ko help`, `ko exa help`, `ko gmail draft help`). After a leaf command it's an ordinary argument, so `ko hn search help` searches for the word "help" — use `--help`, which works at every level, to get help there.
- `-h` is a synonym for `--help` everywhere.
- `ko md-to-pdf` ≡ `ko pdf` (same command, spelled out — markdown *in*, PDF *out*; `ko doc` runs the other direction)

## Auth

Google commands (`gsheets`/`gdocs`/`cal`/`gmail`) share one OAuth token per account.
`ko gsheets auth` grants it (covers Sheets+Docs+Calendar read+write, Gmail read + `gmail.compose`
for drafts — sending stays opt-in behind `[gmail] allow_send`).
No browser? Destructive/outward commands (`gsheets delete-tab`, `gmail draft send`, `gdocs push` over an existing doc, `publish rm`) confirm on a TTY — but non-TTY callers never hang: they exit 2 with a `--yes`/`--force` hint. The only unavoidable interactive step is the one-time OAuth consent.

## Working on the code

- Python 3.14, `uv`, `ruff`, `pytest`, `typer`.
- Install/run: `uv run ko ...`. Test: `uv run pytest -q`. Lint: `uv run ruff check`.
- One module per domain under `src/ko/`; `cli.py` registers the Typer subapps. See
  `CLAUDE.md` for the full design principles and how to add a subcommand.
