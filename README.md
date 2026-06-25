# ko

A personal toolkit for me and my agents to quickly check or grab stuff — papers, posts, sheets, documents, the web — without leaving the terminal.

Each subcommand wraps the best library or API I found for that job, with the defaults I actually want baked in and only 2–3 flags exposed. Several of the APIs are paid: life is too short to do everything yourself, and at personal scale they cost a few dollars a month. Everything is equally usable by a human skimming `--help` and by an AI agent calling it from bash.

Current subcommands:

- `ko fetch` — URL → clean markdown: articles, PDFs (saved to ~/Downloads + parsed), arxiv links, dead links via Wayback. Shortcut: `ko <url>`
- `ko llm` — one-shot LLM call, stdin-aware: `ko hn item 123 | ko llm "summarize"`
- `ko exa` — semantic web search + URL → markdown (via [Exa](https://exa.ai))
- `ko arxiv` — arxiv search + paper-to-markdown
- `ko hf` — Hugging Face [paper pages](https://huggingface.co/papers): daily feed, semantic search, metadata, markdown (no auth)
- `ko hn` — Hacker News top stories, search, comment trees (via [Algolia](https://hn.algolia.com/api); no auth)
- `ko doc` — PDF/Office/image → plain text (via [liteparse](https://developers.llamaindex.ai/liteparse/); local, fast, no models)
- `ko x` — search recent X posts (via the official [XDK](https://docs.x.com/xdks/python/overview); needs a paid API tier for reads)
- `ko tv` — movie/TV quick check: rating, overview, where to stream (AU default; via [TMDB](https://developer.themoviedb.org))
- `ko tt` — TickTick lists + tasks (read-only) via its hosted MCP — `ko tt lists`, `ko tt items <list>`
- `ko gsheets` — read **& write** Google Sheets via OAuth (`get`/`find` · `set`/`put`/`header`/`add-tab`/`new`/`clear`, with overwrite guards)
- `ko agent` — pydantic-ai agents: `research` (web + papers + HN) and `tv` (what to watch in AU), with saved/resumable sessions
- `ko models` — list model strings usable with `-m` (incl. the live OpenRouter catalog)
- `ko publish` — scaffold a site (static / markdown / Hono worker, optional PIN gate) and deploy it to Cloudflare; `ko publish preview` runs it locally first
- `ko doctor` — every tool's setup status (keys, binaries, auth) — run this first

## Install

```bash
uv tool install kotools     # or one-off, no install: uvx kotools
```

The package is `kotools`; the command it installs is **`ko`**. Run `ko doctor` first to see what's set up.

**For development** (editable clone):

```bash
uv tool install --editable /path/to/kotools   # or: uvx --from /path/to/kotools ko --help
```

## API keys

Keys live in environment variables (shell profile or `.env`) or in `~/.config/ko/config.toml` under a `[keys]` table (env always wins) — never in the repo. `ko doctor` shows each key's source (env / config / missing). What you need depends on what you use; most of ko works with no keys at all.

| Env var | Used by | Paid? | Notes |
|---|---|---|---|
| `EXA_API_KEY` | `ko exa`, agents | 💰 | Search $7/1k requests (contents for 10 results included); standalone contents $1/1k pages. [exa.ai](https://exa.ai) |
| `OPENROUTER_API_KEY` | `ko agent` (default), `ko llm` | 💰 | One key, any model. Default agent model is `openrouter:z-ai/glm-5.2`; `-m` overrides. |
| `GEMINI_API_KEY` | `ko llm` (default), `ko agent tv` | 💰 | `ko llm` default is `google:gemini-3.5-flash` (`-m`/`KO_DEFAULT_MODEL`); also the `tv` agent's default. |
| `X_BEARER_TOKEN` | `ko x` | 💰 | X API v2 Bearer Token. Reads need a paid tier (free is ~write-only). [developer.x.com](https://developer.x.com) |
| `TMDB_READ_ACCESS_TOKEN` | `ko tv` | free | v4 Read Access Token from [TMDB settings](https://www.themoviedb.org/settings/api). |
| `TICKTICK_API_KEY` | `ko tt` | (TickTick sub) | TickTick app → Account → MCP → generate. Read-only here. |
| — (Google OAuth) | `ko gsheets` | free | Not a key: one-off browser consent, token cached locally. See below. |
| — | `ko arxiv`, `ko hn`, `ko hf`, `ko doc` | free | No auth at all. |

## Quick start

```bash
# papers: discover on HF, read via arxiv (best quality), parse anything else locally
ko hf top                         # today's Daily Papers by upvotes
ko hf search "agent memory" --long
ko hf info 2412.20138             # upvotes, github + stars, linked models/datasets
ko arxiv search "tool use benchmark" --since 12 --long
ko arxiv fetch 2604.02460 -o paper.md

# Hacker News
ko hn top                         # top 10 of the last 24h (hckrnews-style)
ko hn top --n 20 --days 7         # top 20 of the week
ko hn search "agent memory" --min-comments 50
ko hn item 48480978               # story + comment tree (first column of top/search)

# any URL → markdown (free, local extraction; Wayback fallback for dead links)
ko https://example.com/post       # bare URLs route to fetch
ko fetch https://x.com/paper.pdf  # PDFs download to ~/Downloads + parse
ko fetch --archive https://dead-link.com/page   # straight to the Wayback Machine

# one-shot LLM over anything (default: Gemini flash; -m for any model)
ko hn item 48480978 | ko llm "summarize the debate, what's the consensus?"
ko fetch https://example.com/post | ko llm "key claims as bullets"

# documents — fully local, no auth
ko doc report.pdf                 # PDF/Office/image → plain text
ko report.pdf                     # same: bare file args route to doc
ko doc slides.pptx -p 1-5 -o slides.txt   # Office needs `brew install --cask libreoffice`

# X — needs X_BEARER_TOKEN (paid tier for reads)
ko x ai                           # recent posts from your list named "ai"
ko x lists                        # see your lists
ko x search "claude code" --top   # search last 7 days by relevancy

# web search — needs EXA_API_KEY
ko exa search "claude code hooks" --since 3
ko exa get https://example.com/post       # URL → clean markdown (handles PDF URLs too)

# movies/TV — needs TMDB_READ_ACCESS_TOKEN (free)
ko tv "dune"                    # rating + overview + where to stream in AU
ko tv "the bear" --tv -c US     # TV only, US providers

# Google Sheets — read & write, needs one-off OAuth (see below)
# (example ID is Google's public sample sheet)
ko gsheets info 1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms
ko gsheets get 1Bxi... 'Class Data!A1:F6' --json
ko gsheets find <id> "Smith"                    # search every tab → tab, ref, cell
ko gsheets set <id> 'Sheet1!A1' '=SUM(B:B)'     # write a cell (formulas parse)
echo '{"Sheet1!A1": [["Name","Score"],["Ann",9]]}' | ko gsheets put <id>   # bulk write
```

## Google Sheets setup (one-off) — read & write

`ko gsheets` runs as *you* against *your* Google account (OAuth user flow, no service account). One
token grants read **and** write; reads use the narrower read-only scope under the hood.

1. **Create a Google Cloud project.** https://console.cloud.google.com/projectcreate
2. **Enable the APIs.** APIs & Services → Library → enable *Google Sheets API* and *Google Drive API*.
3. **Configure the OAuth consent screen** (APIs & Services → OAuth consent screen). This step decides
   whether your refresh token lasts:
   - **Workspace org?** Set **User type: Internal** — only your org's users, and **no token expiry**.
   - **Personal Gmail (no org)?** User type must be **External**; add **yourself as a Test user**.
     ⚠️ An External app left in *Testing* expires its refresh token after **7 days** (re-auth weekly).
     To avoid that, **Publish** the app (consent screen → Publish — for a personal Desktop app you can
     ignore Google's verification; "unverified app" is just a warning you click through).
4. **Create OAuth credentials.** Credentials → Create Credentials → OAuth client ID → **Desktop app**. Download the JSON.
5. **Save the JSON** to `~/.config/ko/google_client.json` (or set `KO_GOOGLE_CLIENT_FILE=<path>`).
6. **Run `ko gsheets auth`.** A browser opens; approve. The refresh token caches at
   `~/.local/state/ko/google_token.json` (relocatable via `KO_STATE_DIR`).

**Reuse on other machines:** that token file is portable — copy it to another machine's same path (or
point `KO_STATE_DIR` at it) to reuse the auth with no re-consent.

**Already authed read-only?** `ko gsheets auth --logout`, then `ko gsheets auth` to upgrade to read+write.

**Write safety.** `set` / `put` / `header` / `add-tab` / `new` / `clear` need the read+write grant. The
shape-aware writers **refuse to overwrite non-empty cells** (the error lists exactly which) unless you
pass `--overwrite` — including formulas that currently display blank.

**Why OAuth, not a service account?** A service account needs every sheet explicitly shared with its
email — fine for bots, tedious for a personal read/write-anywhere CLI. OAuth gives access to anything
the signed-in account can see. **Scope to one folder?** No — OAuth scopes are per-API, not per-resource;
use a service account with individual share grants if you need tighter control.

## Output conventions

- **Default output is human-readable** and designed to pipe (`ko gsheets get` emits TSV, `ko arxiv search` emits short line format).
- **`--json` everywhere** structured data would help. Agents should prefer `--json`.
- **Errors go to stderr.** Empty results are not errors — exit `0` with a friendly message.
- **Exit codes:** `0` success, `1` runtime error (auth, network, API), `2` usage error.

## Dev

```bash
cd kotools
uv sync
npm install            # pinned wrangler for `ko publish` (needs Node; via fnm/nvm/etc.)
uv run pytest          # offline by default; KO_LIVE_TESTS=1 enables live-API tests
uv run ko --help
```

Python 3.14, `uv`, `ruff`, `pytest`, `typer`. Candidate subcommands and design notes: [docs/ideas.md](docs/ideas.md).

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).
