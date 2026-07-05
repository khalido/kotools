---
name: python-cli
description: How I build an opinionated Python/Typer CLI — layout, output contract, config, the parts that bite
---
# Python CLI — kickoff

How I build a personal opinionated CLI with Python + Typer. Reference implementation:
`~/code/kotools` — read its `CLAUDE.md` and `AGENTS.md` for the full contract.
`src/ko/` is the canonical pattern for everything below.

## Stack
- Python 3.14, **uv** (never pip — `uv add`, `uv sync`, `uv run ko …`), **ruff**, **pytest**, **typer**
- Module-local `@dataclass` for structured return types — one per domain, never dict soup
- `@lru_cache` for credential/service singletons (SDK clients, OAuth tokens)
- `hatchling` build backend; console-script entry in `pyproject.toml`: `ko = "ko.cli:main"`

## Layout
One file per domain. `cli.py` is the wire-up layer only — it imports domain modules which
self-register their Typer subapps; no business logic lives there. No shared `utils/` until
three things genuinely need it.

```
src/ko/
  _cli_shared.py   # root Typer app + _die / _no_results / _emit_json / _tsv_cell
  cli.py           # entry point: imports subapp modules, _route(), main()
  cli_web.py       # web subcommand group (exa, fetch, arxiv, hn, hf…)
  cli_google.py    # google group (gsheets, gdocs, cal, gmail)
  exa.py           # domain module: thin SDK wrapper + @dataclass result types
  config.py        # key resolution: config.toml [keys] → os.environ at startup
  dirs.py          # config / state / cache dirs, each with a KO_*_DIR override
```

Every flag has `help=`. `--help` is the contract — assume humans and agents skim it
and nothing else.

## Adding a subcommand
1. `src/ko/<thing>.py` — thin SDK wrapper + `@dataclass` result types; keep param names
   matching the upstream SDK so agent-written code composes with upstream docs
2. Register a Typer subapp in the appropriate `cli_*.py`; import it in `cli.py` to
   trigger self-registration
3. `tests/test_<thing>.py` — smoke test; skip live calls behind the service key:
   `@pytest.mark.skipif(not os.environ.get("THING_API_KEY"), reason="…")`
4. Add deps with `uv add <pkg>` (writes to `pyproject.toml` + `uv.lock`)

## Output contract
This is the hardest part to get right. From `AGENTS.md`:
- **stdout = data, stderr = everything else.** A downstream `| jq` or `| cut` must never
  see status text.
- **Default: plain text / TSV** — `cut -f N` safe. Never embed a raw tab or newline inside
  a cell value — use `_tsv_cell(value)` (escapes `\t` → `\\t`, `\n` → `\\n`).
- **`--json`**: a JSON array of objects on stdout. Exceptions documented in `--help`
  (e.g. single-object or 2D-array commands).
- **Empty results**: `typer.Exit(0)`, never exit 1. Under `--json`, emit `[]` on stdout
  + a note on stderr.
- **Exit codes**: `0` success (including empty), `1` runtime error, `2` usage error.
- **No interactive prompts** inside commands (one-time OAuth popup is the sole exception).

The three helpers in `_cli_shared.py` are the reusable spine — use them, don't reinvent:

```python
def _emit_json(items: list) -> None:
    typer.echo(json.dumps([asdict(i) for i in items], default=str))

def _die(msg, *, as_json=False, code="error", exit_code=None) -> NoReturn:
    # JSON {"error":…,"code":…} to stderr under --json, else plain text
    # code="usage" → exit 2, everything else → exit 1

def _no_results(note, as_json) -> NoReturn:
    # exit 0; [] to stdout under --json; note always to stderr
```

## Config and key resolution
`config.py` reads `~/.config/ko/config.toml [keys]` and injects values into `os.environ`
once at startup (`load_keys_into_env()` is called in the root `@app.callback()`). Env
always wins over config. Every SDK that reads `os.environ` directly picks up config values
with no extra plumbing.

```python
from . import config
source = config.key_source("EXA_API_KEY")  # 'env' | 'config' | None
value  = config.get("publish", "domain")   # non-secret section values
```

Never call `os.getenv` directly in a module — use `config.key_source` so `ko doctor`
can report the source (env / config / missing) without re-reading the file.

## Directories
Three dirs, three jobs (`src/ko/dirs.py`):
- `~/.config/ko/` — user-editable, **dotfile-sync-safe** (`KO_CONFIG_DIR`). `config.toml`,
  OAuth client JSON, hand-written prompts live here.
- `~/.local/state/ko/` — tokens + generated files, **never synced** (`KO_STATE_DIR`).
  OAuth tokens, session JSON, JSONL logs.
- `~/.cache/ko/` — disposable, safe to nuke (`KO_CACHE_DIR`). Model catalog, HTTP caches.

Always resolve via `config_dir()`, `state_dir()`, `cache_dir()` — never hardcode paths.
State vs config confusion breaks dotfile sync: tokens go in state, user-edited config goes
in config.

## Bare-arg shortcuts
`_route()` in `cli.py` rewrites `sys.argv[1:]` before dispatch. Explicit command names
always win; shortcuts are deterministic:
- `ko <url>` → `ko fetch <url>` (arg starts with `http://`/`https://`)
- `ko <file>` → `ko doc <file>` (arg is an existing file path)
- `ko x <name>` → `ko x list <name>` (when `<name>` isn't a registered x subcommand)
- `ko <anything> help` → `ko <anything> --help` (trailing `help` at any level)

New shortcuts belong in `_route()`; document them in `AGENTS.md`.

## Tests
Offline by default — `uv run pytest -q` must pass with no service keys set. Gate live
calls behind the service key itself:
```python
@pytest.mark.skipif(not os.environ.get("EXA_API_KEY"), reason="EXA_API_KEY not set")
def test_search_live(): ...
```
For paid or rate-limited APIs (X, arxiv) use `KO_LIVE_TESTS=1` as the gate. Test the CLI
surface via `typer.testing.CliRunner` and pure domain logic directly.

## Installed-tool variant
`[project.scripts]` + `uv tool install --editable .` registers the command globally so
`ko` works from any directory. The `ne` tool at `~/code/nibbleedge-sheets/ne/` follows the
same pattern (`ne = "cli.app:app"`, hatchling). Upgrade: `uv tool upgrade --editable ko`.

## The parts that bite
- **`pretty_exceptions_enable=False`** on the root Typer app — agents parse stderr;
  Typer's default colorized traceback box is not parseable. Catch expected errors
  explicitly, emit via `_die()`, and keep stdout clean.
- **TSV cells** — always escape via `_tsv_cell()` before printing. A raw tab silently
  mis-shapes the row; an agent's `cut -f 2` then returns garbage.
- **Defer heavy imports** inside command functions, not at module top-level. Google client
  libs and pydantic-ai add ~0.3–0.5 s each to startup if imported unconditionally.
- **`no_args_is_help=True`** on the root app — bare `ko` prints help, never errors.
- **`config.load_keys_into_env()` is idempotent** — safe to call in tests; it skips keys
  already in env.

## Docs to load
Typer: https://typer.tiangolo.com/tutorial/ · uv: https://docs.astral.sh/uv/ ·
ruff: https://docs.astral.sh/ruff/
Reference: `~/code/kotools/` (`CLAUDE.md` + `AGENTS.md` + `src/ko/`).
Installed-tool variant: `~/code/nibbleedge-sheets/ne/`.
