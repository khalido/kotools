# ko

**My command-line toolkit for what I look up or grab all day** — papers, X, Hacker News, the web, YouTube, Google (Sheets, Docs, Drive, Calendar, Gmail), documents — without opening a browser tab.

Each command wraps the one library or API I settled on for that job, with my defaults baked in and a couple of flags exposed. I and AI agents calling it over bash get the same output: plain text or TSV by default, `--json` for structure, errors on stderr. A few of the APIs are paid; at personal scale that's a few dollars a month, and life's too short to reimplement them.

Full docs: **[kotools.khalido.dev](https://kotools.khalido.dev/)**.

## Commands

### Read and search

| Command | What it does | What I use it for |
|---|---|---|
| `ko papers` | cross-publisher search + citation graph ([OpenAlex](https://openalex.org), no key) | state of the art on X: find a seed paper, snowball `cites`/`refs` |
| `ko arxiv` | arxiv relevance search + paper → markdown | pull a paper to read or feed an agent |
| `ko hf` | [HF Daily Papers](https://huggingface.co/papers): trending ML papers with linked code and models | what's new in ML today, with repos to try |
| `ko hn` | Hacker News top, search, comment trees (no key) | practitioner signal and the discussion itself |
| `ko x` | X search, lists, user timelines (official [XDK](https://docs.x.com/xdks/python/overview)) | `ko x ai` = my AI list; search a list months back |
| `ko exa` | semantic web search + URL → markdown ([Exa](https://exa.ai)); alias `ko websearch` | posts and lab pages a keyword search misses |
| `ko fetch` | URL → markdown, routed by host (arxiv, YouTube, PDF, Reddit, Wikipedia); archive.today for paywalls, Wayback for dead links | `ko <url>`: read a page or PDF as text |
| `ko sum` | fetch + summarize in one step (Gemini Flash-Lite); takes URLs, files or stdin | `ko sum <url>`, `ko hn item 123 \| ko sum` |
| `ko doc` | PDF / Office / image → text, page-marked for citing; pages with no text layer are OCR'd (DeepSeek on the gateway, ~$0.001 a page; stops and asks over 50 pages or $0.25; `-e vision` for free Apple OCR); `--deep` for multi-column layouts | `ko report.pdf` |
| `ko ocr serve` | liteparse's OCR server API on 127.0.0.1:8828, for any project running liteparse | `lit parse scan.pdf --ocr-server-url http://127.0.0.1:8828/ocr` |
| `ko yt` | YouTube transcript (free, cached); `-s` has Gemini 3.8 Flash watch the video and write up every point (~10–15c, cached; prompt: `ko prompt yt-condense`); `info` and `channel` for metadata | what a 40-minute talk actually says, captions or not |
| `ko tv` | movie/TV rating + where to stream ([TMDB](https://developer.themoviedb.org), AU) | worth watching, and where |
| `ko tt` | TickTick lists and tasks, read and write (Open API v1) | `ko tt` lists my lists; `ko tt add "…" --due tomorrow` |
| `ko pkg info` | npm/PyPI package: latest version, release date, advisories, stars, last push, release notes (no key) | before adopting or upgrading a dependency |

### AI

| Command | What it does | What I use it for |
|---|---|---|
| `ko ai` | the default agent: every tool above + its own memory | find X, read it, tell me what matters |
| `ko agent` | specialist agents (`research`, `repo`, `tv`), resumable sessions | deep literature digs; how repo X does Y |
| `ko llm` | one-shot LLM, stdin-aware, no tools; `-f` attaches files, `--schema` constrains JSON | `… \| ko llm "summarize"` inside a pipe |
| `ko judge` | a typed answer with probabilities: yes/no, `--choice`, `--score`, `--tags` (Jev) | `ko judge "is this a paywall page?" -f page.md` |
| `ko brief` | morning brief: calendar + mail + HN + papers → one summary | the day's triage in one read |
| `ko models` | model strings for `-m`; `info <name>` = price, hosts' speed, top HN threads | picking or comparing models |

### Google

| Command | What it does | What I use it for |
|---|---|---|
| `ko gsheets` | read and write Google Sheets | dump data and formulas; writes refuse to overwrite |
| `ko gdocs` | Markdown ↔ Google Docs, comments, tabs | push a proposal `.md`, read feedback back; per-tab get/edit |
| `ko drive` | mirror a Drive folder locally, push edits back in place | a client's folder in `data/acme/`, refreshed in one command |
| `ko cal` | agenda merged across Google accounts + quick-add | next 7 days; `ko cal use sfl Work` sets where `cal add` lands |
| `ko gmail` | Gmail threads, attachments, drafts; sending is opt-in | `ko gmail from alice`; `ko gmail draft reply <thread> --md note.md` |
| `ko google` | sign in and pick accounts (short tags, `-a <tag>`) | `ko google auth home`, `ko google use sfl` |

### Make and manage

| Command | What it does | What I use it for |
|---|---|---|
| `ko pdf` | markdown → print-ready PDF (A4, styles, code highlighting) | `ko pdf notes.md`; `--style report` for letterhead + contents |
| `ko publish` | scaffold + deploy a site to Cloudflare | a landing page, write-up or mini-tool |
| `ko prompt` | my "how I build X" kickoff briefs | `ko prompt research-papers` → load into an agent |
| `ko refs` | manage `~/code/refs` (reference repo clones) | `ko refs` pulls all; `add <url>` clones + remembers |
| `ko mcp` | inspect and call MCP servers | what a server offers before wiring it in |

Utilities: `ko doctor` (setup status; run it first), `ko billing` (credit left), `ko logs` (recent commands). Every LLM call prints its cost to stderr: an estimate from the gateway's listed price, or OpenRouter's billed cost. `ko logs` keeps one JSON event per command (argv, caller, duration, exit code, model, tokens, cost, error) in `~/.local/state/ko/logs/`; `ko logs --errors` lists failures. Agents keep their own markdown memory, plus a shared `~/.config/ko/memory.md` about you, and save resumable sessions; `ko agent sessions summarize` indexes them.

## It composes

Plain text and TSV by default, `--json` for structure, so `ko` pipes into itself, into `ko llm` and into standard tools:

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

Keys come from environment variables or a `[keys]` table in `~/.config/ko/config.toml`; env wins. `ko doctor` shows where each key resolved from. Most of ko needs no key.

| Env var | Used by | Paid? | Notes |
|---|---|---|---|
| `VERCEL_AI_GATEWAY_API_KEY` | `ko llm`, `ko ai`, agents, `ko sum`, `ko brief`, `ko yt -s`, `ko doc` OCR | 💰 | [Vercel AI Gateway](https://vercel.com/docs/ai-gateway): one prepaid pool, any model (`vercel ai-gateway api-keys create`). Tiers by price: basic = deepseek-v4.1-flash (`ko llm`, `ko ai`, `ko brief`), medium = gpt-6.1-terra, smart = kimi-k3.1-pro (research). Tested models and their host order live in `src/ko/models.py`. Override a tier in `[llm]`, or `-m` per run. |
| `EXA_API_KEY` | `ko exa`, agents | 💰 | $7 per 1k searches (contents for 10 results included); contents alone $1 per 1k pages. [exa.ai](https://exa.ai) |
| `X_BEARER_TOKEN` | `ko x` | 💰 | X API v2 bearer token. Pay-per-use: prepaid credits, ~$0.005 per post read. [developer.x.com](https://developer.x.com) |
| `OPENROUTER_API_KEY` | `ko judge` (Jev) | 💰 | Jev's endpoint is only on OpenRouter. |
| `GEMINI_API_KEY` | `ko doc --deep --describe`, `-m google:…` | 💰 | Direct Gemini on a paid project, so Google doesn't train on what's sent. |
| `GEMINI_FREE_API_KEY` | `ko yt -s` (optional) | free | A key from an AI Studio project without billing. When set, `-s` tries Google's free tier first and falls back to the gateway on 503/429. Free-tier content may be used by Google, so ko sends it public video only. Never name it `GEMINI_API_KEY`: Google's SDK reads that automatically. |
| `GOOGLE_CLOUD_API_KEY` | `ko yt info`, `ko yt channel` | free | One Cloud API key for public Google data (YouTube Data API). |
| `TMDB_READ_ACCESS_TOKEN` | `ko tv` | free | v4 read access token from [TMDB settings](https://www.themoviedb.org/settings/api). |
| `TICKTICK_API_KEY` | `ko tt` | (TickTick sub) | TickTick → avatar → Settings → Account → API Token. Also works for TickTick's hosted MCP and CLI. |
| `S2_API_KEY` | `ko papers` (optional) | free | [Semantic Scholar](https://www.semanticscholar.org/product/api): adds `tldr` and `similar`. |
| `REDDIT_CLIENT_ID` + `REDDIT_CLIENT_SECRET` | `ko fetch` Reddit (planned) | free (100 req/min) | Script app **"kotools"** at [developers.reddit.com/apps](https://developers.reddit.com/apps), app-only read token. Not Devvit: Devvit tokens only work inside Reddit's platform. |
| — (Google OAuth) | `ko gsheets`, `gdocs`, `drive`, `cal`, `gmail` | free | Browser consent once per account; token cached locally. See below. |
| — | `ko arxiv`, `ko hn`, `ko hf`, `ko papers`, `ko doc` (text layer, or `-e vision`), `ko pkg`, `ko yt` transcripts | free | No auth. |

## Where ko keeps things

Three XDG-style dirs, one job each (same pattern as `gh` and `opencode`; identical on macOS and Linux):

| Dir | Job | Holds |
|---|---|---|
| `~/.config/ko/` | your config; sync it privately if it holds keys | `config.toml` (settings, optional `[keys]`), `google_client.json`, `mcp.json`, `prompts/` overrides |
| `~/.local/state/ko/` | machine-local state; never sync | OAuth tokens, agent sessions, `ko.db`, command logs, publish registry |
| `~/.cache/ko/` | disposable | model catalogs, YouTube transcripts and write-ups |

Override each with `KO_CONFIG_DIR`, `KO_STATE_DIR`, `KO_CACHE_DIR`. `ko doctor` prints all three, every effective setting and where it resolved from (env, settings, config, default), and warns if `config.toml` is malformed.

## Google setup (one-off)

`ko gsheets`, `gdocs`, `drive`, `cal` and `gmail` run as you, against your Google accounts (OAuth user flow). One token per account covers all five, read and write; reads use read-only scopes. Service accounts are supported for automation (below).

The console links take `?project=<project-id>`: paste your project ID, or keep the project selected in the console's top bar. The gcloud CLI can do step 2; steps 3 and 4 are console-only.

1. **Create a Google Cloud project**: https://console.cloud.google.com/projectcreate. Note its project ID (e.g. `kotools-500611`).
2. **Enable the APIs** in the [API Library](https://console.cloud.google.com/apis/library?project=<project-id>): Google Sheets, Google Docs, Google Calendar, Gmail (reads are read-only; `gmail.compose` adds drafts; sending is opt-in) and Google Drive. With gcloud:
   ```
   gcloud services enable sheets.googleapis.com docs.googleapis.com calendar-json.googleapis.com gmail.googleapis.com drive.googleapis.com
   ```
3. **Configure and publish the OAuth consent screen** in the [Auth Platform overview](https://console.cloud.google.com/auth/overview?project=<project-id>): app name, support email, **User type: External**. Then [Audience](https://console.cloud.google.com/auth/audience?project=<project-id>) → **Publish app**. This step decides how long your refresh token lasts:
   - **Workspace accounts only:** choose **Internal**. No token expiry, no publishing. Internal can't authorize a personal Gmail, so pick External if you need both.
   - **External** in *Testing*: refresh tokens expire after **7 days**, and each account must be added as a test user. **Publish** to remove both. The app stays "unverified" (a warning you click through on your own app), but the token stops expiring.
   - Scopes aren't set here; `ko` requests them in code. Adding an API later means one re-auth (`ko google auth <name>`). Drive's full `drive` scope is *restricted*: fine in Testing or Internal, but publishing an External app with it triggers Google's verification.
4. **Create OAuth credentials** in [Credentials](https://console.cloud.google.com/apis/credentials?project=<project-id>): Create Credentials → OAuth client ID → **Desktop app** → download the JSON.
5. **Save the JSON** to `~/.config/ko/google_client.json`, or set `KO_GOOGLE_CLIENT_FILE=<path>`.
6. **Run `ko google auth <name>`.** `<name>` is the short tag you'll pass to `-a` (`home`, `sfl`). Approve in the browser. The refresh token lands in `~/.local/state/ko/google_token_<name>.json`; your first account becomes the default.

**Another machine:** copy the token file to the same path there (or point `KO_STATE_DIR` at it). No re-consent.

**Authed read-only earlier?** Re-run `ko google auth <name>` to get read and write.

**Write safety.** `set`, `put`, `header`, `add-tab`, `new` and `clear` need the read-write grant. The shape-aware writers refuse to overwrite non-empty cells, including formulas that display blank, and list which ones; `--overwrite` forces it.

**Why OAuth, not a service account?** A service account needs every file shared with its email: fine for bots, tedious for a personal CLI. OAuth reaches anything the signed-in account can see. OAuth scopes are per API, never per folder; a service account with explicit shares is the way to narrow access.

**When you want one** (automation, or a doc none of your accounts can reach):

```bash
ko google auth sfl-app --service-account ~/Downloads/key.json
```

This copies the key to `~/.local/state/ko/google_sa_sfl-app.json` (owner-only, in state rather than the config dir, since the key authenticates on its own) and prints its `client_email`. Share the files it needs with that address, then pass `-a sfl-app`. No browser, no token. `ko google logout sfl-app` removes it. Gmail and personal calendars need domain-wide delegation; sharing alone won't reach them.

### Markdown ↔ Google Docs (`ko gdocs`)

A Markdown-first proposal loop:

```
ko gdocs push proposal.md --title "Acme — Proposal" --folder Proposals   # .md → formatted Doc
# review and comment in Google Docs
ko gdocs comments <doc>                       # read the feedback (replies indented, [id] shown)
ko gdocs reply <doc> <comment-id> "on it"     # reply to a thread
ko gdocs replace <doc> "Q2" "Q3"              # in-place edit; keeps comment threads
ko gdocs push proposal.md <doc>               # re-push edits to the same URL (shows a diff, asks; --force skips)
ko gdocs get <doc> -o proposal.md             # pull the Doc back as Markdown, tables included

# Tabs: per-client shared docs keep one tab per client or topic
ko gdocs tabs <doc>                           # list tabs (index, title, tabId; sub-tabs indented)
ko gdocs add-tab <doc> "Acme"                 # add a tab (refuses a duplicate title)
ko gdocs get <doc> --tab Acme                 # read one tab (title or tabId, case-insensitive)
ko gdocs append <doc> "note" --tab Acme       # append to one tab
ko gdocs replace <doc> "Q2" "Q3" --tab Acme   # replace within one tab (refuses if it would orphan a comment; --force)
```

Keep the Markdown in git as the source of truth: push to a Doc for review, pull back to reconcile. Code blocks and blockquotes don't convert; the tested support matrix is in [`docs/gdocs-markdown.md`](docs/gdocs-markdown.md). `--folder` takes a folder ID, a `/folders/` URL or a name (`Proposals`), which `ko` finds or creates. Images embed as base64 and don't round-trip cleanly.

### Mirror a Drive folder (`ko drive`)

Keep a local copy of a client's Drive folder (say a gitignored `data/acme/`), refresh it in one command, and push edits back without breaking links:

```
ko drive ls <folder-url>                      # everything under it, recursively (TSV: path, size, modified, id)
ko drive pull <folder-url> data/acme          # download new and changed files; never deletes; Docs/Sheets/Slides → docx/xlsx/pptx
ko drive pull data/acme                       # refresh: the dir remembers its folder
ko drive push data/acme                       # upload local changes; existing files update in place (same id, sharing kept)
ko drive push data/acme --dry-run             # show the plan, upload nothing
```

A `.ko-drive.json` manifest records each file as of the last sync. A file changed on both sides is refused (pull leaves it, push uploads nothing) until you pass `--force`: Drive wins on pull, local wins on push. Nothing is deleted on either side. Google-native docs never push back up; `ko gdocs push` is the Markdown → Doc path.

Scope: full Drive (`drive.readonly` on read-only tokens) since 2026-09; tokens from before re-consent once. For narrower access, a service-account profile (`-a <name>`) sees only folders shared with its `client_email`.

### Multiple Google accounts

Each account gets a short tag and its own token; `ko google` manages them.

```bash
ko google auth home        # sign in (browser) as `home`
ko google auth sfl
ko google use sfl          # default on this machine until you switch
ko gmail -a home           # one call against another account
ko google accounts         # who's signed in (`*` = active) and what each grant can do
ko google rename work kd   # retag; keeps the token
```

- **Which account a command uses:** `-a <tag>` → `KO_GOOGLE_ACCOUNT` → `ko google use` (in `~/.local/state/ko/settings.json`, per machine) → `[google] account` in config.toml → `default`. A tag with no token is an error listing the signed-in ones; a typo never opens a browser.
- **`ko gmail` names the account** on stderr (`# gmail: sfl · you@sfl.com`) and in `--json` rows.
- **`ko cal` merges every signed-in account.** An invite on two accounts shows once, tagged with both. `-a` narrows to one; a dead account is a stderr warning, not an empty agenda. New events go where `ko cal use <tag> [calendar]` says, separate from `ko google use`, so switching mail accounts doesn't move your events. `ko brief` covers every account too.
- **OAuth clients.** One Desktop client authorizes any number of accounts, so one `~/.config/ko/google_client.json` usually does. The exception is an **Internal** consent screen, which only authorizes its own Workspace org: give that account a client from a separate project, saved as `google_client_<tag>.json`; `ko` prefers it when present. Accounts in other Workspace orgs can be blocked by their admin's API controls; the fix is theirs (trust the kotools client ID).

## Output conventions

- **stdout is data, stderr is everything else.** Default output is plain text or TSV, built to pipe (`ko gsheets get` emits TSV).
- **`--json`** wherever structure helps; agents should prefer it. Errors under `--json` are `{"error": …, "code": …}` on stderr, with a `fix` command when there is one.
- **Empty results are success:** exit `0` with a note on stderr.
- **Exit codes:** `0` success, `1` runtime error (auth, network, API), `2` usage error.

[AGENTS.md](AGENTS.md) has the full conventions for agents.

## Dev

```bash
cd kotools
uv sync
npm install            # pinned wrangler for `ko publish` (needs Node; via fnm/nvm/etc.)
uv run pytest          # offline by default; KO_LIVE_TESTS=1 enables live-API tests
uv run ko --help
```

Python 3.15, `uv`, `ruff`, `pytest`, `typer`. Rationale and measurements for each decision: [WORKLOG.md](WORKLOG.md). Candidates and design notes: [docs/ideas.md](docs/ideas.md).

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).
