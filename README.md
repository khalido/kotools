# ko

**A personal command-line toolkit for the things I look up or grab all day** — papers, X, Hacker News, the web, Google (Sheets/Docs/Calendar/Gmail), documents — **without opening a browser tab.**

It's **opinionated, not generic**: each command is a thin wrapper over the *one* library or API I settled on for that job, with my preferred defaults baked in and only a couple of flags exposed. Built so a human skimming `--help` and an **AI agent calling it from bash** get the same clean, pipeable output — plain text / TSV by default, `--json` when you want structure, errors to stderr. A few of the APIs are paid; at personal scale that's a few dollars a month, and life's too short to reimplement them.

Full docs: **[kotools.khalido.dev](https://kotools.khalido.dev/)**.

## The tools

### Read & search

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
| `ko pdf` | markdown → print-ready PDF (A4, styles, code highlighting) | `ko pdf notes.md`; `--style report` for the letterhead + Contents look |
| `ko yt` | YouTube → transcript (free, no key); `-s` summarizes | "what does this 40-min talk actually say" |
| `ko tv` | movie/TV rating + where to stream ([TMDB](https://developer.themoviedb.org), AU) | "worth watching, and where can I?" |
| `ko tt` | TickTick lists + tasks, read **& write** (Open API v1) | `ko tt today` — my open tasks; `ko tt add "…" --due tomorrow` |

### AI agents

| Command | What it does | What I use it for |
|---|---|---|
| `ko ai` | **the default agent** — every tool above + memory | "find X, read it, tell me what matters" |
| `ko agent` | specialist agents (`research`/`repo`/`tv`), resumable | deep literature digs; "how does repo X do Y" |
| `ko llm` | one-shot LLM, stdin-aware, never has tools | `… \| ko llm "summarize"` inside a pipe |
| `ko brief` | morning brief: calendar + mail + HN + papers → one summary | the day's triage in one read |

### Google

| Command | What it does | What I use it for |
|---|---|---|
| `ko gsheets` | read **& write** Google Sheets (OAuth) | dump/read data + formulas, overwrite-guarded |
| `ko gdocs` | Markdown ↔ Google Docs + comments + tabs | push a proposal `.md` → Doc → read feedback back; per-tab get/edit |
| `ko cal` | agenda merged across your Google accounts + quick-add | next 7 days; `ko cal use sfl Work` → where `cal add` lands |
| `ko gmail` | Gmail threads + attachments + drafts | `ko gmail from alice`; `ko gmail thread <id>`; `ko gmail draft reply <thread> --md note.md` |

### Make & manage

| Command | What it does | What I use it for |
|---|---|---|
| `ko publish` | scaffold + deploy a site to Cloudflare | ship a landing page / write-up / mini-tool |
| `ko prompt` | my "how I build X" kickoff briefs | `ko prompt research-papers` → load into an agent |
| `ko refs` | manage `~/code/refs` (reference repo clones) | `ko refs` pulls all; `add <url>` clones + remembers |

Utilities: `ko doctor` (setup status — run it first), `ko models` (model strings for `-m`), `ko billing` (credits left), `ko logs`, `ko mcp` (inspect/call MCP servers). Every LLM call prints its cost to stderr — OpenRouter's actual billed cost when available. Agents keep their own markdown memory (plus a shared `~/.config/ko/memory.md` about you) and save resumable sessions; `ko agent sessions summarize` builds a searchable index of them.

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
| `OPENROUTER_API_KEY` | `ko llm`, `ko ai`, agents, `ko brief` — the default for everything | 💰 | One prepaid pool, any model. Baked model tiers ride on it: basic = deepseek-v4-flash (llm/brief/summarize), medium = gpt-5.6-luna (`ko ai`), smart = grok-latest (research). Override per-tier in `[llm]`; `-m` per run. `ko billing`/doctor show $ left; every call prints its actual cost to stderr. |
| `GEMINI_API_KEY` | `-m google:…` escape hatch | 💰 | Direct Gemini (`-m google:gemini-3.5-flash`), or for Gemini-only features later. |
| `X_BEARER_TOKEN` | `ko x` | 💰 | X API v2 Bearer Token. Pay-per-use since 2026 (prepaid credits, ~$0.005/post read). [developer.x.com](https://developer.x.com) |
| `TMDB_READ_ACCESS_TOKEN` | `ko tv` | free | v4 Read Access Token from [TMDB settings](https://www.themoviedb.org/settings/api). |
| `TICKTICK_API_KEY` | `ko tt` | (TickTick sub) | TickTick → avatar → Settings → Account → API Token. One token authenticates both the Open API (reads + `add`/`done`) and the hosted MCP (`search`). |
| `S2_API_KEY` | `ko papers` (optional) | free | [Semantic Scholar](https://www.semanticscholar.org/product/api) key — adds `tldr` + `similar`; everything else works keyless. |
| `REDDIT_CLIENT_ID` + `REDDIT_CLIENT_SECRET` | `ko reddit` (planned) | free (100 req/min) | The OAuth app **"kotools"** at [developers.reddit.com/apps](https://developers.reddit.com/apps) — script-app client id + secret pair. App-only read token (no user login). Not Devvit: Devvit apps run *on* reddit and authenticate inside reddit's platform — their tokens can't be used from an external CLI. |
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

`ko gsheets` runs as *you* against *your* Google account (OAuth user flow). One token grants read
**and** write; reads use the narrower read-only scope under the hood. Service accounts are supported
too, as the deliberate exception for automation — see below.

Each step below links straight to the right console page. They all take a `?project=<project-id>`
query param — paste your project's ID (from step 1) into the links, or just keep the project selected
in the console's top bar. The **gcloud CLI** can do step 2; steps 3–4 are console-only.

1. **Create a Google Cloud project** → https://console.cloud.google.com/projectcreate . Note its
   **project ID** (e.g. `kotools-500611`) — the links below use it.
2. **Enable the APIs** → [API Library](https://console.cloud.google.com/apis/library?project=<project-id>):
   enable *Google Sheets*, *Google Docs* (`ko gdocs`), *Google Calendar* (`ko cal`), *Gmail*
   (`ko gmail` — reads are read-only; `gmail.compose` adds drafts, sending stays opt-in), and
   *Google Drive* (`ko drive` folder mirror, Docs round-trip). One token covers all.
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
     Gmail-readonly + Drive). Adding an API later = one re-auth (`ko google auth <name>`). Drive's `drive` scope is one Google calls *restricted*: fine in
     Testing or Workspace-Internal; publishing an External app with it triggers Google's verification.
4. **Create OAuth credentials** →
   [Credentials](https://console.cloud.google.com/apis/credentials?project=<project-id>) → Create
   Credentials → OAuth client ID → **Application type: Desktop app** → Download the JSON.
5. **Save the JSON** to `~/.config/ko/google_client.json` (or set `KO_GOOGLE_CLIENT_FILE=<path>`).
6. **Run `ko google auth <name>`** — `<name>` is a short tag you'll use with `-a` (`home`, `sfl`). A
   browser opens; approve (click through the "unverified app" warning). The refresh token caches at
   `~/.local/state/ko/google_token_<name>.json` (relocatable via `KO_STATE_DIR`); your first account
   becomes the default.

**Reuse on other machines:** that token file is portable — copy it to another machine's same path (or
point `KO_STATE_DIR` at it) to reuse the auth with no re-consent.

**Already authed read-only?** Re-run `ko google auth <name>` to upgrade to read+write.

**Write safety.** `set` / `put` / `header` / `add-tab` / `new` / `clear` need the read+write grant. The
shape-aware writers **refuse to overwrite non-empty cells** (the error lists exactly which) unless you
pass `--overwrite` — including formulas that currently display blank.

**Why OAuth by default?** A service account needs every sheet explicitly shared with its email — fine
for bots, tedious for a personal read/write-anywhere CLI. OAuth gives access to anything the signed-in
account can see. **Scope to one folder?** No — OAuth scopes are per-API, not per-resource; a service
account with individual share grants is the way to get tighter control.

**When you do want one** — automation, or a doc none of your own accounts can reach:

```bash
ko google auth sfl-app --service-account ~/Downloads/key.json
```

That copies the key to `~/.local/state/ko/google_sa_sfl-app.json` (owner-only; state, not the
dotfile-synced config dir — an SA key authenticates by itself on any machine) and prints its
`client_email`. Share the Sheets/Docs/Drive files you want it to reach with that address, then use
`-a sfl-app` on any command. No browser, no token. `ko google logout sfl-app` unregisters it. Caveat: Gmail and
personal Calendars need domain-wide delegation — sharing alone won't let an SA read them.

### Markdown ↔ Google Docs + comments (`ko gdocs push/get/comments`)

A Markdown-first proposal loop:

```
ko gdocs push proposal.md --title "Acme — Proposal" --folder Proposals   # .md → formatted Doc
# → review & comment in Google Docs (you + colleagues)
ko gdocs comments <doc>                       # read the feedback back (replies indented, [id] shown)
ko gdocs reply <doc> <comment-id> "on it"     # reply to a thread without leaving the terminal
ko gdocs replace <doc> "Q2" "Q3"              # surgical in-place edit — keeps comment threads
ko gdocs push proposal.md <doc>               # re-push edits in place (same URL; diff+confirm, --force skips)
ko gdocs get <doc> -o proposal.md             # pull the Doc back as real Markdown (tables incl.)

# Tabs — per-client shared docs keep one tab per client/topic:
ko gdocs tabs <doc>                           # list tabs (index, title, tabId; sub-tabs indented)
ko gdocs add-tab <doc> "Acme"                 # add a tab (Docs API; refuses a duplicate title)
ko gdocs get <doc> --tab Acme                 # read just that tab (title or tabId, case-insensitive)
ko gdocs append <doc> "note" --tab Acme       # append to one tab's end
ko gdocs replace <doc> "Q2" "Q3" --tab Acme   # scope the replace to one tab (refuses if it'd orphan a comment; --force)
```

Keep the Markdown in git as the source of truth; push to a Doc for review, export to reconcile.
**What converts** (and what doesn't — code blocks and blockquotes don't): see the tested support
matrix in [`docs/gdocs-markdown.md`](docs/gdocs-markdown.md).
`--folder` takes a folder ID, a `/folders/` URL, or a **name** (`Proposals`) that `ko`
find-or-creates. Images in a pushed doc embed as base64 and don't round-trip cleanly — fine for
text proposals.

### Mirror a Drive folder (`ko drive ls/pull/push`)

Keep a local copy of a client's Drive folder (say a gitignored `data/acme/`) and refresh it with
one command; push edits back without breaking links:

```
ko drive ls <folder-url>                      # everything under it, recursively (TSV: path, size, modified, id)
ko drive pull <folder-url> data/acme          # download new + changed; never deletes; Docs/Sheets/Slides → docx/xlsx/pptx
ko drive pull data/acme                       # refresh — the dir remembers its folder
ko drive push data/acme                       # upload what changed here; existing files update IN PLACE (same id, sharing kept)
ko drive push data/acme --dry-run             # the plan, nothing uploaded
```

A `.ko-drive.json` manifest in the dir records each file as of the last sync, so a file changed on
*both* sides is detected and refused (pull: left alone; push: nothing uploads) until you pass
`--force` — Drive wins on pull, local wins on push. Nothing is ever deleted on either side, and
a Google-native doc never pushes back up (`ko gdocs push` is the Markdown→Doc path).

Scope: `ko` now asks for full Drive (`drive.readonly` on read-only tokens) — widened 2026-09 from the
per-file `drive.file`, so a token from before then re-consents once. Want it narrower? A
service-account profile (`-a <name>`) only sees folders shared with its `client_email`.

### Multiple Google accounts

Each account gets a short tag and its own token; `ko google` is the one place to manage them.

```bash
ko google auth home        # sign in (browser) as tag `home`
ko google auth sfl
ko google use sfl          # default on this machine until you switch
ko gmail -a home           # one call against another account
ko google accounts         # who's signed in (`*` = active), and what each grant can do
ko google rename work kd   # retag; keeps the token
```

- **Which account a command uses:** `-a <tag>` → `KO_GOOGLE_ACCOUNT` → `ko google use` (stored in
  `~/.local/state/ko/settings.json`, per machine) → `[google] account` in config.toml → `default`.
  A tag with no token is an error listing the signed-in ones — a typo never opens a browser.
- **`ko gmail` names the account** on stderr (`# gmail: sfl · you@sfl.com`) and in `--json` rows.
- **`ko cal` merges every signed-in account** (an invite on two accounts shows once, tagged with both);
  `-a` narrows to one, and a dead account is a stderr warning rather than an empty agenda. New
  events go to `ko cal use <tag> [calendar]`, set apart from `ko google use` so switching mail
  accounts doesn't move your events. `ko brief` covers every account too.
- **OAuth client (the subtle bit).** One Desktop client can authorize *multiple* accounts, so usually
  a single `~/.config/ko/google_client.json` is all you need. The exception: an **Internal** consent
  screen only authorizes its own Workspace org. Then give that account its own client from a separate
  GCP project, saved as `google_client_<tag>.json`; `ko` uses it if present, else the shared one.
  Accounts in *other* Workspace orgs (clients) can also be blocked by their admin's API controls —
  the fix is on their side: trust the kotools client ID.

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

Python 3.15, `uv`, `ruff`, `pytest`, `typer`. Candidate subcommands and design notes: [docs/ideas.md](docs/ideas.md).

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).
