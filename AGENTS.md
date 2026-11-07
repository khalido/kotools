# AGENTS.md — driving `ko` from a shell

`ko` is a CLI of thin SDK wrappers: web search and fetch, papers, HN, X, YouTube, Google Sheets/Docs/Drive/Calendar/Gmail, publishing, LLM calls and agents. It's built to be driven by agents over bash as much as by a person. This file is the contract across commands; `ko <cmd> --help` is the contract for one command.

## First move

Run `ko doctor`. It reports, per tool, which keys and binaries it needs and whether they're present. If a command fails on auth or a missing key, `ko doctor` says why.

## Output contract

- **stdout is data; stderr is everything else.** Errors, notes and "no results" messages go to stderr, so stdout pipes into `jq`, `cut` or `awk` clean.
- **Default format is plain text or TSV**, safe for `cut -f N`. Tabular commands such as `ko gsheets get` pad rows to a true rectangle, so a short row never shifts a column index.
- **`--json` for structure.** Most read commands take it. The shape is a JSON array of objects unless the command's `--help` says otherwise. Exceptions: `ko tv --json` is `{top, matches}`, `ko hn item --json` is `{story, comments}`, `ko papers get --json` and `ko hf info --json` are single objects, and `ko gsheets get --json` is a 2D array.
  - Under `--json`, **errors are a JSON object on stderr**: `{"error": "...", "code": "..."}`, plus `"fix": "<command>"` when one command resolves it (e.g. `ko google auth sfl`). `code` is a coarse label (`auth`, `network`, `usage`, `not_found`, `error`). In text mode the fix reads "— run `<command>`".
  - Under `--json`, **empty results are `[]` on stdout** (plus a note on stderr), so a downstream `jq` never chokes.
  - `ko fetch --json` keeps one row per URL. A failed row has `code`: `blocked`, `not_found`, `network`, `not_public`, `timeout` or `error`. Use `--live-only` for a privately shared link, and `--timeout` to cap a URL.
- **A command that writes a file prints only that file's path to stdout**, so it composes: `ko gmail draft new … -a "$(ko pdf status.md)"`. Notes about the render go to stderr.

## Exit codes

- `0`: success, including empty results.
- `1`: runtime error (network, auth, bad upstream). One clean line on stderr, no traceback. An unset API key lands here too, including mid-run in `ko ai` and `ko agent`, which abort rather than work around the missing tool. The line names the env var and where to get a key.
- `2`: usage error (bad flags or args; Typer prints usage to stderr).

## What costs money

Paid per call: `ko llm`, `ko sum`, `ko decide`, `ko ai`, `ko agent`, `ko brief`, `ko yt -s` (cached per video), `ko exa`, `ko x`. `ko doc` (and `ko fetch` or `ko llm -f` of a PDF) pays only for pages with no text layer: a gateway model OCRs them at about $0.001 a page. Over 50 such pages or $0.25 it stops before spending and names the cost: `ko doc` exits 2, `--yes` to spend it, `-e vision` for free Apple OCR. `ko fetch`, `ko llm -f` and agent tools return the text layers with a first line saying what wasn't read; if there's no text at all, they fail with that message, and an agent tool returns it as a note. Every LLM call prints its cost to stderr, e.g. `[deepseek/deepseek-v4.1-flash-1017 · 93→5 tok · <$0.0001]`. Everything else is free: arxiv, HN, HF, papers, fetch, pkg, tv, YouTube transcripts and the Google commands. A typo'd subcommand never falls through to an LLM.

## Diagnosing a failure

`ko logs` keeps one JSON event per command: argv, caller (your `AI_AGENT` value and session id), duration, exit code, model, tokens, cost, and the error label and message. Level `error` means something broke (crash, upstream, network); `warning` means a fix the caller can make (usage, auth, config, missing key).

```bash
ko logs --errors -n 5          # recent failures, one line each
ko logs -n 1 --json            # the last command, every field
```

## Limits

List commands take `-n` or `--n` to bound output (default around 10); both spellings work everywhere, and `ko gmail` also takes `--max`. `ko cal --days/-d` is a time window, not a result count.

## Bare-argument shortcuts (not in `--help`)

`ko` routes a leading bare argument deterministically; an explicit command name always wins.

- `ko <url>` → `ko fetch <url>` (URL → markdown; a YouTube URL returns the transcript, or fails cleanly when captions are off).
- `ko <file>` → `ko doc <file>` (PDF/Office/image → text), when the argument is an existing file.
- `ko yt <url>` → `ko yt transcript <url>`. Anything after `yt` that isn't `info`, `channel` or `transcript` is a video.
- `ko x <name>` → `ko x list <name>`. Anything after `x` that isn't an x subcommand is a list name.
- `ko tt` (alias `ko todo`) → `ko tt lists`; `ko tt <name>` → `ko tt items <name>`. Anything that isn't `lists`, `items`, `add`, `done` or `search` is a list name.
- `ko <group> help` → `ko <group> --help`, at group level including nested groups (`ko help`, `ko exa help`, `ko gmail draft help`). After a leaf command `help` is an ordinary argument: `ko hn search help` searches for "help". `--help` works at every level.
- `ko publish [dir]` → `ko publish up [dir]`. Anything after `publish` that isn't a publish subcommand is a folder or a flag for `up`.
- `-h` is a synonym for `--help` everywhere.
- `ko md-to-pdf` ≡ `ko pdf` (markdown in, PDF out; `ko doc` goes the other way).
- `ko websearch` ≡ `ko exa search` and `ko webfetch` ≡ `ko fetch`: the `WebSearch`/`WebFetch` names you already know. Same flags, same output; the `exa` group still holds `get` and the search guide (`ko prompt exa`).

## Auth

Google commands (`gsheets`, `gdocs`, `drive`, `cal`, `gmail`) share one OAuth token per account. Accounts are short tags (`-a sfl`), managed with `ko google` (`auth`, `use`, `accounts`). `ko gmail` names the account it read on stderr. `ko google auth <tag>` grants Sheets, Docs, Drive and Calendar read-write, plus Gmail read and `gmail.compose` for drafts; sending stays opt-in behind `[gmail] allow_send`.

Destructive or outward commands (`gsheets delete-tab`, `gmail draft send`, `gdocs push` over an existing doc, `drive push`, `publish rm`) confirm on a TTY. Non-TTY callers never hang: they exit 2 with a `--yes`/`--force` hint. The only unavoidable interactive step is the one-time OAuth consent.

## Working on the code

- Python 3.15, `uv`, `ruff`, `pytest`, `typer`.
- Run: `uv run ko ...`. Test: `uv run pytest -q`. Lint: `uv run ruff check`.
- One module per domain under `src/ko/`; `cli.py` registers the Typer subapps. `CLAUDE.md` has the design principles and how to add a subcommand.

## In CI

For the issue agent (`.github/workflows/fx.yml`), not a laptop:

- `uv run pytest -q` and `uv run ruff check` are the whole gate; both must pass.
- The runner has no API keys and no Google token, so live tests skip by design. A skip is not a failure, and `ko doctor` will report most things missing. Don't try to authenticate.
- Never run a command that writes to a real service (`gdocs push`, `drive push`, `gmail draft send`, `publish`). Read the code instead of running the CLI.
- Decisions and rationale live in `WORKLOG.md`; candidates in `docs/ideas.md`. Cite the design doc that covers an issue instead of re-deriving it. Anything that needs taste is KO's call: say so instead of guessing.
