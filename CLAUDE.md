# ko

Ko's personal opinionated CLI. Thin wrappers around SDKs I use often, built so both I and AI agents (Claude Code via bash) can lean on it.

## Subcommands
- `ko exa search|get` — Exa semantic web search + URL → markdown
- `ko arxiv search|fetch` — arxiv search + paper-to-markdown
- `ko gsheets info|tabs|get|find | set|put|header|add-tab|new|clear|auth` — read **& write** Google Sheets via OAuth. Accepts a URL or bare ID. Reads use the readonly scope; writes need read+write (`ko gsheets auth` grants it by default). Shape-aware writers (`set`/`put`, library `write_values`/`write_ranges`/`write_df`) refuse to clobber non-empty cells unless `--overwrite` — the error lists them. `write_df` duck-types polars **or** pandas (neither is a dep). Generic port from the NibbleEdge `ne` tool; no project-specific shortcuts.
- `ko doc <file>` — PDF/Office/image → plain text via liteparse (local, no models). Bare shortcut: `ko <file>` routes here when the arg is an existing file.
- `ko fetch <url>` — URL → markdown, deterministic routing: arxiv→arxiv2md, PDF→~/Downloads+liteparse (`--no-save`), else trafilatura, dead/empty→Wayback (`--archive` forces). Bare shortcut: `ko <url>`.
- `ko llm "<prompt>"` — one-shot LLM, stdin-aware, never has tools. Default `google:gemini-3.5-flash` (`KO_DEFAULT_MODEL`/`-m`; `-m` tab-completes models whose env key is set). OpenRouter's catalog is fetched from its public `/models` and cached at `~/.cache/ko/openrouter_models.json` (24h TTL) so `-m openrouter:<slug>` completes.
- `ko models [--refresh]` — list model strings usable with `-m`, one per line. `--refresh` force-refetches the OpenRouter catalog past the 24h cache (for grabbing a just-released model).
- `ko hn top|search|item` — Hacker News via Algolia (no auth). `top` = hckrnews-style top 10/20 by points; `search` defaults to last 12 months; `item` = story + comment tree as text.
- `ko hf top|search|info|get` — Hugging Face paper pages (no auth). `top` = Daily Papers by upvotes; `search` = semantic; `info` = metadata incl. github/models/datasets; `get` = paper as markdown. Same ids as arxiv — composes with `ko arxiv fetch`.
- `ko tv <title>` — movie/TV rating + overview + regional watch providers (`TMDB_READ_ACCESS_TOKEN`, free; AU default, `--country`).
- `ko x search|list|lists` — X via the official XDK (`X_BEARER_TOKEN`, paid tier for reads). `list <name>` = recent posts from my list; bare shortcut `ko x ai` ≡ `ko x list ai`. Search index ≈ last 7 days.
- `ko tt lists|items <list>` — TickTick (read-only) via its hosted MCP (`TICKTICK_API_KEY`, env or config.toml). `lists` = your lists; `items <list>` = open tasks (name substring or id). Bare shortcut `ko tt <list>` ≡ `ko tt items <list>`. Read-only by design — manage tasks in TickTick itself. We're an MCP *client* here (`mcp` SDK over Streamable HTTP).
- `ko gdocs get|info|append|replace|new` — read **& write** Google Docs via OAuth (shared token with gsheets/cal; Docs scope). URL or ID; `get --md` = light markdown. Minimal — structural editing is the web UI's job.
- `ko cal [agenda] | day | find | add | cals` — Google Calendar agenda + quick-add (shared token; `calendar.readonly` + `calendar.events`). Bare `ko cal` = next 7 days across calendars (`--days`/`--today`); `find <text>` = events matching a title substring, forward or `--past` ('when was my last X'; borrowed from chota-bot); `add "title" <when>` accepts ISO / today / tomorrow (no date lib). Times in `[cal] timezone` (default Australia/Sydney). Minimal — no edit/delete/RSVP.
- `ko gmail [recent] | search | from | view | thread` — read Gmail (**read-only**; shared token, `gmail.readonly`). Bare `ko gmail` = recent inbox, one line/msg (id, date, from, subject + snippet; `-n`, `--unread`); `search "<query>"` passes Gmail's own syntax verbatim (`from:`/`newer_than:7d`/`is:unread`); `from <who>` shortcut; `view <id> [--full]` = one message; `thread <id> [--full]` = the whole conversation, oldest first. `--json` everywhere. Concise on purpose (vs the verbose Gmail MCP). No send/labels/drafts.
- `ko publish new <dir> [--md|--bare|--hono]` / `ko publish preview [dir]` / `ko publish [dir]` / `ko publish list` — publish a folder to Cloudflare. `preview` runs `wrangler dev` locally (real http; `file://` breaks ES modules + fetch). `new` scaffolds: default static (Tailwind+Alpine), `--md` (markdown doc site — generic `?page=` shell, README hub, sidebar TOC), `--bare` (just a CLAUDE.md of hints). Bare `ko publish [dir]` deploys; the folder's own `wrangler.jsonc` makes the name sticky → re-publish overwrites the same URL. Custom domain defaults to `<name>.khalido.dev` (`DEFAULT_DOMAIN`; override via `[publish] domain`). Creds: `KO_CLOUDFLARE_API_TOKEN`/`KO_CLOUDFLARE_ACCOUNT_ID` → config.toml → standard `CLOUDFLARE_*`. **Won't silently take over** an existing Worker name (checks the account; `--force` to override). Needs **wrangler** (pinned repo-local via `npm install`; `KO_WRANGLER` → `node_modules/.bin` → PATH → `npx wrangler@4`). `--hono` adds a Hono worker (API routes, D1/R2, optional `--pin` gate, cached `/api/data`); `ko publish --pin new` rotates the gate PIN. Cloudflare Access private pages + AI on sites deferred — see `docs/publish.md` / `docs/publish-ai.md`.
- `ko agent research|tv|sessions` — pydantic-ai agents. `research` (web+papers+HN, capable model), `tv` (movies/TV in AU, cheap model), no prompt = interactive REPL, `-m` overrides model, `-r <id>` resumes a saved session. `sessions` lists saved sessions. **`ko a …` is a hidden shorthand for `ko agent …`** (e.g. `ko a research "…"`). See Agents below.
- `ko mcp test <url>` — test/inspect an MCP server (ko is itself an MCP client). Connects over Streamable HTTP, lists tools (name + required args + one-line desc), `--call <tool> --arg k=v` to invoke one, `-H 'Authorization: Bearer ...'` for auth, `--json`. On failure prints the server's real HTTP status + body (e.g. a 503 "MCP endpoint is not configured" → server-side issue, not your client). Tools to stdout, banner to stderr.
- `ko prompt [name]` — kickoff briefs: my opinionated "how I build X" notes, pulled by name to load into an agent ("get `ko prompt sveltekit-app`") or copy-paste (`--copy`). Bare `ko prompt` lists (TSV: name, description); `ko prompt <name>` prints the markdown. Briefs ship in `src/ko/prompts/*.md`; add or override per machine in `~/.config/ko/prompts/*.md` (a user file wins by name). A meta-layer on top of generic llms.txt — my picks + the gotchas, with links out to the canonical docs. The tool is just a thin loader; the content is hand-written.

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
- `ko gsheets auth` (or `gdocs`/`cal`/`gmail auth` — same flow) grants **one read+write token per account** covering Sheets + Docs + Calendar (write) and Gmail (**read-only**). **No Drive scope** — can't browse Drive, only docs/sheets by ID. Reads use the narrower readonly scopes; the one token serves all. `--readonly` narrows; upgrade an existing token with `auth --logout` then `auth`. Adding another API later = one re-consent.
- Personal Gmail: an **External** app left in *Testing* expires the refresh token after **7 days** — set the consent screen to **Internal** (Workspace) or **Publish** the app. The token file is portable across machines (copy it, or share via `KO_STATE_DIR`).
- **Multi-account.** Active account = `-a/--account` (any `ko gsheets` cmd, sets `KO_GOOGLE_ACCOUNT`) → `[google] account` → `"default"`. Per-account token `google_token_<account>.json` (legacy `google_token.json` for `default`); per-account client `google_client_<account>.json` falls back to the shared `google_client.json` (only needed when one OAuth client can't authorize an account, e.g. a Workspace-Internal consent screen + a personal Gmail). `ko gsheets accounts` lists authed accounts. All paths via `google_auth.token_file()/client_file()/active_account()`.
- **Scope is per-API, not per-folder.** OAuth grants the tool *your* access within the scope (now Sheets only). It can't be limited to specific folders/files; true per-file scoping needs a service account (share files with its email) — a separate model not used here.
- Prereq: OAuth 2.0 Desktop client JSON at `~/.config/ko/google_client.json` (or set `KO_GOOGLE_CLIENT_FILE`). Full setup in README.

## Directories (`src/ko/dirs.py`)
- `~/.config/ko/` — user-editable config, dotfile-sync-safe (`KO_CONFIG_DIR`). `config.toml` `[keys]` table supplies API keys/tokens as a fallback to env vars (env always wins); loaded into the environment at startup so SDKs that read `os.environ` directly pick them up. `ko doctor` shows each key's source: env / config / missing.
- `~/.local/state/ko/` — tokens + id caches, never synced (`KO_STATE_DIR`); old config-dir state files auto-migrate
- `~/.cache/ko/` — disposable (`KO_CACHE_DIR`)
- `ko doctor` shows per-tool setup status (keys set, binaries found, auth done)

## Agent notes
- **`AGENTS.md`** at the repo root is the agent-facing contract (output/exit-code/`--json` conventions, bare-arg shortcuts). Keep it in sync with the bullets below.
- `ko --help` / `ko <cmd> --help` are the contract
- `ko gsheets get ... ` defaults to TSV — `cut -f N` safe
- `--json` on most read commands → a JSON array of objects (a few return a single object; `gsheets get` is a 2D array). Under `--json`: errors are `{error, code}` on stderr, empty results are `[]` on stdout. stdout = data, stderr = notes/errors.
- Exit codes: `0` success (incl. empty results), `1` runtime error, `2` usage error
- No interactive prompts inside commands (except the one-time OAuth browser popup)

## Not yet
- **`ko sessions summarize`** → local SQLite (`~/.local/state/ko/ko.db`): one-line summary + tags per session, as a lightweight memory + filter layer ("show me sessions about python/HN"). See WORKLOG Open.
- **MCP server (`ko mcp`)** — `mcp_server.py` is scaffolded but not wired; expose the same module functions over MCP.
- **default agent** (all toolsets) + **tmdb lists by id** in config. Full candidate list + priorities in `docs/ideas.md`.
- **`ko gdocs`** — read a Google Doc as text/markdown + append/replace text. New scope `https://www.googleapis.com/auth/documents` on the existing google_auth split. Deferred from the gsheets read/write work to keep that focused.
