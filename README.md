# ko

**A personal command-line toolkit for the things I look up or grab all day** — papers, X, Hacker News, the web, Google (Sheets/Docs/Calendar/Gmail), documents — **without opening a browser tab.**

It's **opinionated, not generic**: each command is a thin wrapper over the *one* library or API I settled on for that job, with my preferred defaults baked in and only a couple of flags exposed. Built so a human skimming `--help` and an **AI agent calling it from bash** get the same clean, pipeable output — plain text / TSV by default, `--json` when you want structure, errors to stderr. A few of the APIs are paid; at personal scale that's a few dollars a month, and life's too short to reimplement them.

Reach for `ko` when you want the answer, not the website. `ko doctor` shows what's set up; `ko <cmd> --help` is the contract.

## The tools

| Command | What it does | What I use it for |
|---|---|---|
| `ko papers` | cross-publisher search + citation graph ([OpenAlex](https://openalex.org), no key) | "state of the art on X" — find a seed paper, snowball `cites`/`refs` |
| `ko arxiv` | arxiv relevance search + paper → markdown | pull a specific paper to read or feed an agent |
| `ko hf` | [HF Daily Papers](https://huggingface.co/papers): trending ML + linked code/models | what's hot in ML today, with repos to try |
| `ko hn` | Hacker News top / search / comment trees (no key) | practitioner signal + the actual discussion thread |
| `ko x` | X search, lists, user timelines (official [XDK](https://docs.x.com/xdks/python/overview)) | `ko x ai` = my AI list; search a list back months |
| `ko exa` | semantic web search + URL → markdown ([Exa](https://exa.ai)) | find posts/lab-pages a keyword search misses |
| `ko fetch` | any URL → clean markdown (PDF, arxiv, Wayback fallback) | `ko <url>` — read a page or PDF as text |
| `ko doc` | PDF/Office/image → text, fully local (no models) | `ko report.pdf` — no upload, no key |
| `ko llm` | one-shot LLM, stdin-aware, never has tools | `… \| ko llm "summarize"` inside a pipe |
| `ko agent` | pydantic-ai agents (`research` / `tv`), resumable | a question that needs multi-source digging |
| `ko tv` | movie/TV rating + where to stream ([TMDB](https://developer.themoviedb.org), AU) | "worth watching, and where can I?" |
| `ko gsheets` | read **& write** Google Sheets (OAuth) | dump/read data + formulas, overwrite-guarded |
| `ko gdocs` | Markdown ↔ Google Docs + comments | push a proposal `.md` → Doc → read feedback back |
| `ko cal` | Google Calendar agenda + quick-add | next 7 days; "when was my last dentist?" |
| `ko gmail` | read Gmail (read-only) | `ko gmail from alice` — inbox triage in the terminal |
| `ko tt` | TickTick lists + tasks (read-only, via MCP) | `ko tt today` — my open tasks |
| `ko publish` | scaffold + deploy a site to Cloudflare | ship a landing page / write-up / mini-tool |
| `ko prompt` | my "how I build X" kickoff briefs | `ko prompt research-papers` → load into an agent |

Utilities: `ko doctor` (setup status — run it first), `ko models` (model strings for `-m`), `ko billing` (credits left), `ko logs`, `ko mcp` (inspect/call MCP servers).

## It composes

Plain text / TSV by default (add `--json` for structure), so `ko` pipes into itself, into `ko llm`, and into standard tools:

```bash
ko hn item 48480978 | ko llm "summarize the debate; what's the consensus?"
ko fetch https://example.com/post | ko llm "key claims as bullets"
ko papers search "retrieval augmented generation" --json | jq -r '.[].doi'
ko x search "claude code" --list ai --days 90     # my AI list, 3 months back
```

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
| `X_BEARER_TOKEN` | `ko x` | 💰 | X API v2 Bearer Token. Pay-per-use since 2026 (prepaid credits, ~$0.005/post read). [developer.x.com](https://developer.x.com) |
| `TMDB_READ_ACCESS_TOKEN` | `ko tv` | free | v4 Read Access Token from [TMDB settings](https://www.themoviedb.org/settings/api). |
| `TICKTICK_API_KEY` | `ko tt` | (TickTick sub) | TickTick app → Account → MCP → generate. Read-only here. |
| `S2_API_KEY` | `ko papers` (optional) | free | [Semantic Scholar](https://www.semanticscholar.org/product/api) key — adds `tldr` + `similar`; everything else works keyless. |
| — (Google OAuth) | `ko gsheets` | free | Not a key: one-off browser consent, token cached locally. See below. |
| — | `ko arxiv`, `ko hn`, `ko hf`, `ko papers`, `ko doc` | free | No auth at all. |

## Where ko keeps things

Three XDG-style dirs, three jobs (same pattern as `gh`/`opencode`; identical on macOS and Linux):

| Dir | Job | Holds |
|---|---|---|
| `~/.config/ko/` | your config — safe to dotfile-sync | `config.toml` (keys + settings), `google_client.json`, `mcp.json`, `prompts/` overrides |
| `~/.local/state/ko/` | machine-local state — never sync | OAuth tokens, agent sessions, `ko.db`, command logs, publish registry |
| `~/.cache/ko/` | disposable — safe to delete | model catalogs and other regenerable caches |

Each is overridable via `KO_CONFIG_DIR` / `KO_STATE_DIR` / `KO_CACHE_DIR`. `ko doctor`
prints all three, plus every effective setting and where it resolved from
(env / config / default) — and warns if `config.toml` is malformed.

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
