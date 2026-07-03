# ko

A personal toolkit for me and my agents to quickly check or grab stuff — papers, posts, sheets, documents, the web — without leaving the terminal.

Each subcommand wraps the best library or API I found for that job, with the defaults I actually want baked in and only 2–3 flags exposed. Several of the APIs are paid: life is too short to do everything yourself, and at personal scale they cost a few dollars a month. Everything is equally usable by a human skimming `--help` and by an AI agent calling it from bash.

Current subcommands:

- `ko fetch` — URL → clean markdown: articles, PDFs (saved to ~/Downloads + parsed), arxiv links, dead links via Wayback. Shortcut: `ko <url>`
- `ko llm` — one-shot LLM call, stdin-aware: `ko hn item 123 | ko llm "summarize"`
- `ko exa` — semantic web search + URL → markdown (via [Exa](https://exa.ai))
- `ko arxiv` — arxiv search + paper-to-markdown
- `ko hf` — Hugging Face [paper pages](https://huggingface.co/papers): daily feed, semantic search, metadata, markdown (no auth)
- `ko papers` — cross-publisher paper search + citation graph (via [OpenAlex](https://openalex.org); no auth): `search` · `get` (full text if open-access, else metadata card) · `cites`/`refs` · `similar` (needs free `S2_API_KEY`)
- `ko hn` — Hacker News top stories, search, comment trees (via [Algolia](https://hn.algolia.com/api); no auth)
- `ko doc` — PDF/Office/image → plain text (via [liteparse](https://developers.llamaindex.ai/liteparse/); local, fast, no models)
- `ko x` — search recent X posts (via the official [XDK](https://docs.x.com/xdks/python/overview); needs a paid API tier for reads)
- `ko tv` — movie/TV quick check: rating, overview, where to stream (AU default; via [TMDB](https://developer.themoviedb.org))
- `ko tt` — TickTick lists + tasks (read-only) via its hosted MCP — `ko tt lists`, `ko tt items <list>`
- `ko gsheets` — read **& write** Google Sheets via OAuth (`get`/`find` · `set`/`put`/`header`/`add-tab`/`new`/`clear`, with overwrite guards)
- `ko gdocs` — read **& write** Google Docs as Markdown (same OAuth token): `get` a Doc as Markdown (pipe it) · `push` a Markdown file to a **new** Doc, or **update** an existing one in place (diff + confirm) · `replace`/`append` · read `comments` / `reply` · `shade-table` (header/totals)
- `ko cal` — Google Calendar agenda + quick-add (same token): bare `ko cal` = next 7 days · `day`/`find`/`add`/`cals`
- `ko gmail` — read Gmail (read-only, same token): bare = recent inbox · `search "<gmail query>"` · `from <who>` · `view <id>` · `thread <id>` (whole conversation)
- `ko agent` — pydantic-ai agents: `research` (web + papers + HN) and `tv` (what to watch in AU), with saved/resumable sessions
- `ko prompt [name]` — kickoff briefs: my "how I build X" notes, pulled by name to load into an agent or copy-paste. Bare lists; `ko prompt <name>` prints. Add your own in `~/.config/ko/prompts/*.md`
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
| `S2_API_KEY` | `ko papers` (optional) | free | [Semantic Scholar](https://www.semanticscholar.org/product/api) key — adds `tldr` + `similar`; everything else works keyless. |
| — (Google OAuth) | `ko gsheets` | free | Not a key: one-off browser consent, token cached locally. See below. |
| — | `ko arxiv`, `ko hn`, `ko hf`, `ko papers`, `ko doc` | free | No auth at all. |

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
ko gdocs get <id>                                # a Google Doc as markdown (pipe to ko llm, etc.)
ko cal                                          # your next 7 days
ko cal add "Dentist" 2026-07-01T14:00 -m 30     # add a 30-minute event
ko cal find dentist --past                      # when was my last dentist appointment?
ko gmail from alice -n 5                         # recent mail from alice
ko gmail search "is:unread newer_than:2d"        # unread, last 2 days
```

## Google Sheets setup (one-off) — read & write

`ko gsheets` runs as *you* against *your* Google account (OAuth user flow, no service account). One
token grants read **and** write; reads use the narrower read-only scope under the hood.

Each step below links straight to the right console page. They all take a `?project=<project-id>`
query param — paste your project's ID (from step 1) into the links, or just keep the project selected
in the console's top bar. The **gcloud CLI** can do step 2; steps 3–4 are console-only.

1. **Create a Google Cloud project** → https://console.cloud.google.com/projectcreate . Note its
   **project ID** (e.g. `kotools-500611`) — the links below use it.
2. **Enable the APIs** → [API Library](https://console.cloud.google.com/apis/library?project=<project-id>):
   enable *Google Sheets*, *Google Docs* (`ko gdocs`), *Google Calendar* (`ko cal`), *Gmail*
   (`ko gmail`, read-only), and *Google Drive* (the narrow `drive.file` scope only — see below).
   `ko` still can't browse your Drive; one token covers all.
   Fast path with the gcloud CLI:
   ```
   gcloud services enable sheets.googleapis.com docs.googleapis.com calendar-json.googleapis.com gmail.googleapis.com drive.googleapis.com
   ```
3. **Configure + publish the OAuth consent screen** →
   [Auth Platform overview](https://console.cloud.google.com/auth/overview?project=<project-id>)
   (set app name + support email, **User type: External**), then
   [Audience](https://console.cloud.google.com/auth/audience?project=<project-id>) → **Publish app**.
   This step decides whether your refresh token lasts:
   - **Workspace org and only need org accounts?** **User type: Internal** — no token expiry, no publish
     needed. But Internal **can't authorize a personal Gmail** — use External if you want personal + work.
   - **External** (any account): a project left in *Testing* expires its refresh token after **7 days** and
     requires each account added as a **Test user**. **Publish** the app to remove both — it stays
     "unverified" (a warning you click through on your own app), but the token no longer expires.
   - You don't configure *scopes* here — `ko` requests them in code (Sheets/Docs/Calendar +
     Gmail-readonly + Drive `drive.file`). Adding an API later = one re-auth (`ko gsheets auth --logout`
     then `ko gsheets auth`).
4. **Create OAuth credentials** →
   [Credentials](https://console.cloud.google.com/apis/credentials?project=<project-id>) → Create
   Credentials → OAuth client ID → **Application type: Desktop app** → Download the JSON.
5. **Save the JSON** to `~/.config/ko/google_client.json` (or set `KO_GOOGLE_CLIENT_FILE=<path>`).
6. **Run `ko gsheets auth`.** A browser opens; approve (click through the "unverified app" warning). The
   refresh token caches at `~/.local/state/ko/google_token.json` (relocatable via `KO_STATE_DIR`).

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

### Markdown ↔ Google Docs + comments (`ko gdocs push/get/comments`)

`ko` requests the **narrow `drive.file` Drive scope** — and deliberately *not* `drive`/`drive.readonly`.
`drive.file` grants access **only to files `ko` itself creates or that you open by ID** — it still
cannot browse, search, or list your Drive. That's exactly enough for a Markdown-first proposal loop,
without widening the blast radius:

```
ko gdocs push proposal.md --title "Acme — Proposal" --folder Proposals   # .md → formatted Doc
# → review & comment in Google Docs (you + colleagues)
ko gdocs comments <doc>                       # read the feedback back (replies indented, [id] shown)
ko gdocs reply <doc> <comment-id> "on it"     # reply to a thread without leaving the terminal
ko gdocs replace <doc> "Q2" "Q3"              # surgical in-place edit — keeps comment threads
ko gdocs push proposal.md <doc>               # re-push edits in place (same URL; diff+confirm, --force skips)
ko gdocs get <doc> -o proposal.md             # pull the Doc back as real Markdown (tables incl.)
```

Keep the Markdown in git as the source of truth; push to a Doc for review, export to reconcile.
**What converts** (and what doesn't — code blocks and blockquotes don't): see the tested support
matrix in [`docs/gdocs-markdown.md`](docs/gdocs-markdown.md).
`--folder` takes a folder ID, a `/folders/` URL, or a **name** (`Proposals`) that `ko`
find-or-creates. **Caveat:** because `drive.file` only sees files `ko` made, `get`/`comments`
work on docs **`ko` pushed** — not on a pre-existing doc someone else created (that returns 404).
For broad read access you'd need `drive.readonly`, which `ko` intentionally refuses. Images in a
pushed doc embed as base64 and don't round-trip cleanly — fine for text proposals.

### Multiple Google accounts (work + personal)

`ko gsheets` supports several accounts — each keeps its own token, and you flip between them.

- **Pick the active account** (in priority order): `--account`/`-a` on any command
  (`ko gsheets -a personal info <id>`); the `KO_GOOGLE_ACCOUNT` env var; or `[google] account`
  in `~/.config/ko/config.toml`. Unset = `"default"` (the single-account setup above, unchanged).
- **Auth each once:** `ko gsheets -a work auth` then `ko gsheets -a personal auth`. Tokens cache
  per account — `google_token_work.json`, `google_token_personal.json` (the `default` account keeps
  the legacy `google_token.json`). `ko gsheets accounts` lists them (`*` = active).
- **OAuth client (the subtle bit).** One Desktop client can authorize *multiple* accounts, so usually
  a single `~/.config/ko/google_client.json` is all you need — just auth each account into it. The
  exception: if your **work** account is a Workspace org with an **Internal** consent screen, that
  client can't authorize a **personal** Gmail (and vice-versa). Then give that account its own client
  from a separate GCP project, saved as `google_client_<account>.json` (e.g. `google_client_personal.json`);
  `ko` uses the per-account file if present, else falls back to the shared `google_client.json`.
- **Set your usual default** with `[google] account = "work"`, and reach for `-a personal` now and then.

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
