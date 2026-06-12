# ko

A personal toolkit for me and my agents to quickly check or grab stuff — papers, posts, sheets, documents, the web — without leaving the terminal.

Each subcommand wraps the best library or API I found for that job, with the defaults I actually want baked in and only 2–3 flags exposed. Several of the APIs are paid: life is too short to do everything yourself, and at personal scale they cost a few dollars a month. Everything is equally usable by a human skimming `--help` and by an AI agent calling it from bash.

Current subcommands:

- `ko exa` — semantic web search + URL → markdown (via [Exa](https://exa.ai))
- `ko arxiv` — arxiv search + paper-to-markdown
- `ko hf` — Hugging Face [paper pages](https://huggingface.co/papers): daily feed, semantic search, metadata, markdown (no auth)
- `ko hn` — Hacker News top stories, search, comment trees (via [Algolia](https://hn.algolia.com/api); no auth)
- `ko doc` — PDF/Office/image → plain text (via [liteparse](https://developers.llamaindex.ai/liteparse/); local, fast, no models)
- `ko x` — search recent X posts (via the official [XDK](https://docs.x.com/xdks/python/overview); needs a paid API tier for reads)
- `ko gsheets` — read Google Sheets via OAuth
- `ko agent` — pydantic-ai research agent (early; the agent layer is growing)

## Install

```bash
# from the repo
uv tool install --editable /path/to/ko

# or one-off
uvx --from /path/to/ko ko --help
```

(Once published to PyPI: `uv tool install ko-tools` — the package is `ko-tools`, the command it installs is still `ko`.)

## API keys

Keys live in environment variables (shell profile or `.env` — never in the repo). What you need depends on what you use; most of ko works with no keys at all.

| Env var | Used by | Paid? | Notes |
|---|---|---|---|
| `EXA_API_KEY` | `ko exa` | 💰 | Search $7/1k requests (contents for 10 results included); standalone contents $1/1k pages. [exa.ai](https://exa.ai) |
| `ANTHROPIC_API_KEY` | `ko agent` | 💰 | Default agent model. |
| `OPENROUTER_API_KEY` | `ko agent` (planned default) | 💰 | One key, any model — the easy way to explore new models via pydantic-ai. |
| `GEMINI_API_KEY` | planned `ko yt` fallback | 💰 | Gemini native video understanding when no transcript exists. |
| `X_BEARER_TOKEN` | `ko x` | 💰 | X API v2 Bearer Token. Reads need a paid tier (free is ~write-only). [developer.x.com](https://developer.x.com) |
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

# documents — fully local, no auth
ko doc report.pdf                 # PDF/Office/image → plain text
ko report.pdf                     # same: bare file args route to doc
ko doc slides.pptx -p 1-5 -o slides.txt   # Office needs `brew install --cask libreoffice`

# web search — needs EXA_API_KEY
ko exa search "claude code hooks" --since 3
ko exa get https://example.com/post       # URL → clean markdown (handles PDF URLs too)

# Google Sheets — needs one-off OAuth (see below)
# (example ID is Google's public sample sheet)
ko gsheets info 1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms
ko gsheets get 1Bxi... 'Class Data!A1:F6'
ko gsheets get 1Bxi... 'Class Data!A1:F6' --json
```

## Google Sheets setup (one-off)

`ko gsheets` runs as *you* against *your* Google account (OAuth user flow). Scopes are read-only by default.

1. **Create a Google Cloud project** (if you don't have one). https://console.cloud.google.com/projectcreate
2. **Enable the APIs.** APIs & Services → Library → enable *Google Sheets API* and *Google Drive API*.
3. **Create OAuth credentials.** APIs & Services → Credentials → Create Credentials → OAuth client ID → Application type: **Desktop app**. Download the JSON.
4. **Save the JSON** to `~/.config/ko/google_client.json` (or set `KO_GOOGLE_CLIENT_FILE=<path>`).
5. **Run `ko gsheets auth`.** A browser window opens; approve; you're done. The refresh token is cached at `~/.config/ko/google_token.json`.

Logout / re-auth: `ko gsheets auth --logout`.

**Why OAuth and not a service account?** A service account needs every sheet explicitly shared with its email address — fine for bots, tedious for a personal read-anywhere CLI. OAuth gives you access to anything the signed-in Google account can see. If that's too broad, use a service account instead (future `ko gsheets` flag).

**Can I scope to one folder?** No — Google's OAuth scopes are per-API (`drive.readonly`), not per-resource. Service accounts with individual share grants are the workaround if you need tighter control.

## Output conventions

- **Default output is human-readable** and designed to pipe (`ko gsheets get` emits TSV, `ko arxiv search` emits short line format).
- **`--json` everywhere** structured data would help. Agents should prefer `--json`.
- **Errors go to stderr.** Empty results are not errors — exit `0` with a friendly message.
- **Exit codes:** `0` success, `1` runtime error (auth, network, API), `2` usage error.

## Dev

```bash
cd ko
uv sync
uv run pytest          # offline by default; KO_LIVE_TESTS=1 enables live-API tests
uv run ko --help
```

Python 3.14, `uv`, `ruff`, `pytest`, `typer`. Candidate subcommands and design notes: [docs/ideas.md](docs/ideas.md).

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).
