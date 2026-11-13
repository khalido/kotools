# Changelog

All notable changes to `ko` (kotools) are documented in this file. It is the
canonical release record — GitHub release notes are drawn from it. WORKLOG.md is
the dev journal (why/decisions); this is what changed, release-grained.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/2.0.0/).
Versioning is [CalVer](https://calver.org/), PEP 440-normalized: `YYYY.M.D`
(tag `vYYYY.M.D`; a second release the same day appends `.1`). The version covers
the **CLI contract** (commands, flags, output shapes, exit codes — see AGENTS.md);
breaking changes to it carry a `**Breaking:**` marker inline under their type.
Cut a release with the repo's `/release` skill.

## [Unreleased]

## [2026.11.13] - 2026-11-13

A flight lookup, inline images in Gmail drafts, a faster start, and clearer errors.

### Added
- `ko flight EK604 [date]`: a flight status card from OAG (airports, scheduled and estimated/actual times in each airport's local time, terminal, gate, codeshares and multi-leg flights). No date = the flight in progress or next to leave; `--json` for scripts. Needs `OAG_FLIGHT_INFO_KEY`, which `ko doctor` checks.
- `ko gmail draft --md` embeds local images (`![alt](shot.png)`, path relative to the .md file) inline in the mail; png, jpeg, gif and webp only, http(s) images stay remote.

### Changed
- Commands start about 80 ms faster (~212 → ~135 ms): HTTP clients, Google sign-in and the arxiv SDK load on first use.
- `ko pdf`, `ko publish` and `ko prompt` share one front-matter parser that keeps values exactly as written. A block that doesn't parse (an unquoted `: ` or ` #`, or a nested value) is skipped with a note naming the file and line, and the content still comes back. Windows line endings, a byte-order mark and list `tags:` now work.
- Packages only install once they're a day old (was 3 days); `ko pkg info` reports that floor. pydantic-ai is now at least 2.66, for its security fixes.

### Fixed
- A missing key or bad config exits as `code: "config"`, and a Google sign-in problem as `code: "auth"` with its `fix`, instead of a plain `error`; one that reaches the top level is one line, not a traceback. A bug in ko itself now shows a traceback.
- A Google command whose cached token won't do says the real reason (unreadable, read-only grant, no refresh token, refresh refused), not always "expired".
- `ko gmail` replies use the right account's address under `-a`, and sending a draft is refused outside the CLI too unless `allow_send` is on.
- Smaller fixes: `ko cal` and gmail thread lists note an empty `--json` result on stderr; `ko gdocs get -o` creates missing folders; `ko doctor --json` skips the live calls only the human report uses; `ko x` says when X bills more posts than the `-n` you asked for (minimum 10, or 5 for a user timeline); several `--json` outputs no longer crash on a date or path.

## [2026.11.7] - 2026-11-07

Model calls move to the Vercel AI Gateway, `ko doc` OCRs scans with a spend stop, and YouTube, Drive, PDF, decision model and package lookups arrive. The PyPI release 2026.7.27 crashed on startup; this one fixes it.

### Added
- `ko doc` reads scanned pages: pages that look image-based go to DeepSeek v4.1 Flash on the gateway. A job over 50 OCR pages or an estimated $0.25 at the high end stops with its cost (exit 2); `--yes` spends it (`[ocr] max_pages`, `max_usd`). `-e vision` (Apple, free) and `-e tesseract` are local, with no stop.
- `ko doc` also gains `<!-- page N -->` markers, `--md`, `--json`, `--deep` (docling for multi-column layouts and figures, `--describe` for Gemini captions), and reads `.docx` directly, no LibreOffice.
- `ko ocr serve`: liteparse's OCR server on 127.0.0.1:8828 for other projects (`--ocr-server-url`).
- `ko fetch` uses a page's own markdown version when it has one: the `<link rel="alternate" type="text/markdown">` it names, else `<page>.md`. Each answer is checked, and a bad one falls back to extraction. One extra request per page; the `<page>.md` guess is skipped for site roots, URLs with a query and file paths.
- `ko fetch --public-only`, for a URL you didn't choose: refuses local and private addresses on every redirect, skips the browser fallback and the cache, saves no PDF.
- `ko fetch --live-only`, for a link shared privately: if its site fails, the URL goes nowhere else, and a scanned PDF's pages aren't sent for OCR. `--timeout <s>` caps a URL, fallbacks included. `--json` rows gain `title`, and a failed row gains `code` (`blocked`, `not_found`, `network`, `not_public`, `timeout`, `error`).
- `ko fetch` takes several URLs, has `--json` and a 1-hour cache (`--fresh` skips it), and tries the browser, Exa and the archives in parallel when a page is walled. Wikipedia pages use the plain-text extract (about 4x fewer tokens).
- `ko sum <url|file>...` fetches and summarizes in one move, and reads stdin. `ko llm` gains `-f` (files, images, audio, video, URLs), `-x` (first code block) and `--schema <file.json>`.
- `ko decide "<question>"`: a decision model (Jev) gives a typed answer with probabilities: yes/no, `--choice`, `--score`, `--tags`; `-m` compares any `ko llm` model. Wording guide: `ko prompt decide`. `ko judge` is an alias.
- `ko models info <name>...` shows the newest matching gateway model: price, each host's speed, top HN threads. `ko pkg info <name>...` shows npm/PyPI latest and date (and what the 3-day floor picks), your locked version, advisories, stars, last push, release notes. Neither needs a key.
- `ko yt` is now a group; `ko yt <url>` is still the transcript. `info <url>...` gives metadata plus a transcript excerpt (`--full`), `channel` lists new uploads (`--since`, Shorts left out unless `--shorts`). `info` and `channel` need `GOOGLE_CLOUD_API_KEY`.
- `ko drive ls|pull|push`: mirror a Drive folder locally and push edits back in place (same file id, sharing kept). A `.ko-drive.json` manifest refuses both-sides-changed; nothing is deleted on either side.
- `ko google auth|use|accounts|rename|logout`: one sign-in for every Google command, with short account tags and `-a <tag>` on each. `accounts` and `ko doctor` show each account's address and what its grant can do; a command with no usable token fails in one line when there is no terminal. `auth --service-account key.json` signs in a service account.
- `ko cal` merges every signed-in account; `ko cal use` sets where `ko cal add` lands. `ko gmail` lists threads, and `draft reply` answers the latest message (`--msg` picks another).
- `ko gmail draft new|reply --attach`, `--md` tables, `--json` fields `labels` and `list_unsubscribe`; `draft send` refuses a body that says "attached" with no attachment (`--yes` overrides). `ko gdocs replace --all-tabs`.
- `ko pdf <file.md>` (alias `md-to-pdf`): markdown to a print-ready PDF with `--style note|report|bare`, `--letterhead`, `--brand`; prints only the path it wrote.
- `ko tt add|done|search` (alias `ko todo`): create and complete TickTick tasks; bare `ko tt` lists your lists.
- `ko exa search` gains `--type`, `--category`, `--max-age-hours`, `--highlights`. `ko prompt` gains `ko-cli`, `code-review` and `decide`, and resolves short names.
- `ko doctor` prints the versions it runs (ko, Python, pydantic-ai, mcp and the MCP spec) and checks pango for `ko pdf`; `ko billing` shows the key's own spend cap. Every command logs one wide event (`ko logs`).
- `ko publish` keeps a never-publish list at the top of the served folder's `.assetsignore` (dotfiles, `*.env`, keys, tooling, junk), refreshed on every deploy and preview. Your own lines below it win, so `!.env` publishes it.

### Changed
- **Breaking:** `ko` requires Python 3.15.
- **Breaking:** every LLM call goes through the Vercel AI Gateway (`VERCEL_AI_GATEWAY_API_KEY`). Tiers are re-cut by price: basic `deepseek-v4.1-flash`, medium `gpt-6.1-terra`, smart `kimi-k3.1-pro`; the `ultra` tier is gone. `ko ai` runs on basic (`-m` for hard questions). `ko decide` keeps `OPENROUTER_API_KEY`; `ko llm -m jev` points to `ko decide`.
- **Breaking:** `ko doc` no longer OCRs every page with Tesseract. It OCRs only pages that look image-based, through the paid DeepSeek default above; `--no-ocr` still skips OCR and the new `--ocr` forces every page. Over the spend stop, `ko fetch` and `ko llm -f` keep the text layers and note what wasn't read.
- **Breaking:** the Drive OAuth scope widens from `drive.file` to `drive.readonly` (read token) / `drive` (write token). Existing tokens re-consent once; `ko gdocs get` and `comments` now work on docs ko didn't create. Sign-in also requests `youtube.readonly`; old tokens keep working.
- **Breaking:** `ko hn search --since <months>` is now `-y/--years` (default 1, `-y 0` = all time). Search ranks title matches with 20+ points by points.
- **Breaking:** `ko gdocs replace` and `append` on a multi-tab doc refuse without `--tab` (or `--all-tabs` on replace). `ko cal --json` times are in the configured timezone with an offset.
- **Breaking:** `ko yt -s` now has Gemini watch the video (3.8 Flash on the gateway, about $0.09 to $0.15 each; cached, `--refresh`) instead of summarising the transcript, free tier first when `GEMINI_FREE_API_KEY` is set.
- `ko mcp` runs on pydantic-ai's MCP client and probes the 2026-07-28 spec (`server/discover`, then `initialize`).
- `ko publish` sites use Tailwind 4 and current CDN versions; `--pin` and `--name` edit `wrangler.jsonc` in place, keeping bindings and comments.

### Fixed
- The PyPI release 2026.7.27 crashed on startup: `uv tool install` ignores the lock and picked up `mcp` 2.x, which removed an import. `ko mcp` no longer imports it; CI now installs the built wheel before publishing and reinstalls from PyPI weekly.
- `ko x search|list|user` failed before sending a request once `xdk` renamed `tweet_fields` to `post_fields`.
- `ko ai` and `ko agent` are told to wrap up three requests before their request limit and answer from what they found; before, hitting the limit failed the run with nothing to show.
- `ko fetch` keeps code blocks on docs pages (uv, Python and Svelte docs lost theirs). Extraction still loses code inside tab widgets on a site with no markdown version.
- `ko publish`: Chart.js scripts now run in the `--md` shell, and `ko publish rm` validates names instead of deleting on an empty or path-like one.
- `ko hn`: `-c` was ignored under the date window; comments follow HN's ranking, not oldest-first; `top --now` uses HN's real front-page order; `item` takes a pasted URL.
- `ko cal` agenda starts at the start of today, shows 7 day headers for 7 days, and reads a one-day all-day event as one day.
- Smaller fixes: `ko fetch` treats a bot-check page as a wall, rejects a reddit thread from the wrong subreddit and takes arXiv authors from the API; `ko doc` no longer stops silently at 1,000 pages and keeps `.docx` list numbers and field text; `ko llm` raises on cut-off output; `ko gdocs push` rejects a missing file or empty Markdown (exit 2); `ko pkg info` survives a repo deps.dev doesn't know; `--help` shows `[bracketed]` text.

### Removed
- **Breaking:** `ko gsheets|gdocs|cal|gmail auth` and `accounts`; use `ko google`.

### Security
- `ko publish` PIN gate: a correct PIN redirected to the path alone, dropping the query string, and a path of `//evil.com` made it an open redirect. It now sends a `303` to the full same-site URL (GitHub issue #3).
- `ko publish` md and static sites could serve `.env` and `.git/config`; the never-publish list above keeps them out.

## [2026.7.27] - 2026-07-27

The Google-suite-goes-read-write release: Sheets tab ops + print-to-PDF, draft-first
Gmail, per-tab Docs — plus per-agent memory for `tv` and `llms.txt` on published sites.

### Added

- `ko agent tv` now keeps its own markdown memory (like ai/research/repo) — it
  remembers what it already recommended and your reactions, so repeat runs stop
  re-pitching the same titles and lean toward what you liked.
- Every agent now gets a shared preamble in its instructions: today's date (in the
  `[cal] timezone`), what a ko agent is, and terminal-brevity guidance — computed
  per run, so agents stop guessing the date when stamping memory entries.
- `ko publish rm <name|dir>` — delete a published site from Cloudflare (its custom
  domain + DNS record detach with it) and forget it locally; TTY confirm, `--yes`
  when scripted.
- `ko publish list --cf` — reconcile against the Cloudflare account: appends Workers
  published elsewhere (custom domains resolved). The TSV also gains a last-published
  column.
- `--md`/`--hono` sites generate an agent-facing `llms.txt` on every publish (llmstxt.org
  shape) from the pages' front matter (`title:`/`description:`/`tags:`) or H1 + first
  paragraph; sites of ≤2 pages inline their truncated content so one fetch reads the
  whole site. A hand-authored `llms.txt` (marker line removed) is never touched; the
  md shell now strips front matter before rendering.
- `ko gsheets dup-tab|delete-tab|insert-rows|insert-cols|replace` — tab & bulk ops:
  `dup-tab` clones a tab with formatting/formulas intact (the new-invoice move),
  `insert-rows`/`insert-cols` inherit formatting from the neighbour (`--no-inherit`
  for blank), `replace` = find&replace scoped `--tab` (or explicit `--all-tabs`),
  `delete-tab` verifies the tab exists then confirms (`--yes` when piped).
- `ko gsheets pdf <sheet> [tab] [-o out]` — print-ready PDF via the sheet's export
  endpoint (A4 portrait, fit-to-width; `--size`/`--landscape`/`--gridlines`); the
  existing OAuth token works, no new scope.
- `ko gmail attach <id> [n] [-o DIR]` — list attachments (TSV/`--json`) and download
  (never overwrites — appends `-1`, `-2`, …) under the existing readonly scope;
  pairs with `ko doc <file>` for stuck client PDFs.
- `ko gmail draft new|reply|list|show|update|send` — draft-first mail (needs
  `gmail.compose`: `ko gmail auth --logout` then `auth`, once per account). Replies
  thread correctly (threadId + In-Reply-To/References + `Re:`); `--all` = reply-all,
  `--cc` adds extras; `--md` = markdown body → multipart/alternative; `update`
  preserves to/cc/bcc/subject/threading for anything not overridden (`--to`/`--cc`
  repair a recipient list) and refuses to drop attachments unless `--force`.
  **Sending is opt-in**: `draft send` refuses unless `[gmail] allow_send = true` /
  `KO_GMAIL_SEND=1`, then confirms with the full recipient list — agents draft,
  a human sends from Gmail.
- Gmail reads now show Cc (and drafts' Bcc) everywhere: `view`, `thread`,
  `draft show|list`, every `--json`, and the send confirmation.
- `ko gdocs tabs` / `add-tab <title>` / `--tab <title|tabId>` on `get`/`replace`/
  `append` — per-tab Docs ops (per-client shared docs keep one tab each);
  `replace --tab` refuses if it would orphan an open comment's quoted anchor
  (`--force` overrides); `push` stays whole-doc (Drive's markdown import can't
  target a tab).

### Fixed

- `--json` errors from bare `ko cal`, `cal day|find|cals`, and `gdocs comments` now
  emit the documented `{error, code}` object instead of plain text.
- `ko gmail draft list` crashed on every call — `drafts.get` rejects the
  `metadataHeaders` param (a `messages.get`-only filter).
- `ko gsheets pdf` filenames no longer break when a tab title contains `/`.

### Removed

- Unused `gdocs.get_text` / `gdocs.create_doc` library functions — superseded by the
  Drive markdown export/push paths (`ko gdocs get`/`push`).

## [2026.7.14] - 2026-07-14

The money-and-agents release: model tiers with per-call cost notes, agents that
remember between runs, and releases that publish themselves to PyPI.

### Added

- **PyPI publishing**: a tag push now builds and publishes `kotools` via GitHub
  OIDC trusted publishing (`.github/workflows/publish.yml`, no tokens) — this
  release is the first through the pipeline; `uv tool install kotools` from
  here on.
- `ko ai "<prompt>"` — the default agent: every toolset (web, papers, HN,
  movies, read-only ~/code files) plus its own memory; medium-tier model,
  hard-capped at 30 model requests per run. One-shot or REPL.
- `ko agent repo` — repo-explorer agent: "how does repo X do Y" over `~/code`
  (read-only by construction; ripgrep search; knows the refs/CLAUDE.md map;
  cites file:line; basic tier — under a cent a run).
- Agent memory: research + repo agents keep a per-agent markdown workspace
  (`memory.md` anchor injected each run + free-form notes; append/edit tools
  with a uniqueness guard), plus a shared hand-edited `~/.config/ko/memory.md`
  injected into every memory-carrying agent (research, repo, ai). A fresh run
  recalls what a prior run saved. Hardened after a trace-eval + review pass:
  huge/minified files can't blow the context, broken symlinks and rg timeouts
  degrade cleanly, and `grep` grew the `limit` param the model reached for.
- Model tiers: `[llm] basic/medium/smart/ultra` in config.toml (`llm.model_for`) —
  basic (deepseek-v4-flash) drives llm/tv/brief/summarize defaults, smart
  (`~x-ai/grok-latest`) the research agent, ultra (gpt-5.6-sol) is reserved for
  high-stakes calls. **Changed**: the everyday default moves from gemini-flash
  direct to DeepSeek via OpenRouter — one prepaid pool, visible in `ko billing`.
- LLM calls report their cost on stderr — `[model · 66→21 tok · $0.0012]` —
  using OpenRouter's *actual* billed cost when available (genai-prices estimate,
  marked `~`, otherwise). Covers `ko llm`, agents, `ko brief`, `yt -s`; `sessions
  summarize` totals its spend.
- `ko refs` — manage the `~/code/refs` reference-repo folder: bare = parallel
  `--ff-only` pull of all clones (only moved HEADs printed), `setup` bootstraps
  the baked list + `~/.config/ko/refs.txt` on any machine, `add <url>` clones and
  remembers, `list` = TSV. `ko doctor` shows the folder's disk footprint and
  flags clones over 500MB.
- Opt-in LLM telemetry: `[telemetry] enabled = true` + `POSTHOG_API_KEY` sends
  provider-agnostic traces (model/tokens/cost) to PostHog via pydantic-ai OTel
  instrumentation; off by default, metadata-only unless `include_content = true`.
- `[llm] model` and `[agents] model` in config.toml — the default model now has
  a config home (env var still wins); `ko doctor` gains an effective-settings
  footer (each value's source: env/config/default, plus the three dirs) and
  live-checks the OpenRouter key via `/credits` — proves it works and shows
  credits left, without spending tokens.

### Fixed

- A malformed `config.toml` now warns loudly (every command + doctor) instead of
  silently ignoring all keys and settings; README documents the three dirs.

## [2026.7.9] - 2026-07-09

First tagged release — everything since the repo started (2026-04). `ko` is an
opinionated personal CLI of thin SDK wrappers, built to be driven by humans and
AI agents over bash: `--help` is the contract, stdout is data, exit codes mean
things, `--json` where structure matters.

### Added

- **Read anything**: `ko fetch <url>` — articles, PDFs, arxiv, YouTube, DOIs →
  markdown, with Wayback/archive.today fallbacks (bare `ko <url>` routes here);
  `ko doc <file>` — PDF/Office/image → text, local; `ko yt <url>` — transcripts,
  `-s` summarizes.
- **Papers**: `ko papers` — cross-publisher search + citation graph via OpenAlex
  (`search|get|cites|refs|similar`, no key); `ko arxiv` — relevance-ranked search
  + paper → markdown via arxiv2md; `ko hf` — HF Daily Papers.
- **News & social**: `ko hn` — `top` (incl. `--now` live front page), `search`,
  `item` comment trees; `ko x` — search (incl. full-archive), lists by
  name/id/URL, user timelines; `ko tt` — TickTick lists, read-only.
- **Google** (one OAuth token, multi-account): `ko gsheets` read & write with a
  clobber guard; `ko gdocs` Markdown ↔ Docs + comments; `ko cal` agenda/find/add;
  `ko gmail` read-only search/view/thread.
- **AI**: `ko llm` — one-shot, stdin-aware, `-m` any model whose provider key is
  set; `ko agent research|tv` — pydantic-ai agents with saved, resumable
  sessions; `ko agent sessions` — listing with `--tag`/`--search` filters over an
  LLM-built SQLite index (`sessions summarize`); `ko brief` — morning brief;
  `ko prompt` — kickoff briefs; `ko mcp` — inspect/call/overview/auth-info for
  MCP servers.
- **Publish**: `ko publish` — folder → Cloudflare static site (scaffolds,
  optional PIN gate, custom domains).
- **Meta**: `ko doctor` setup status; `ko billing` OpenRouter credits; `ko logs`
  local command log (no args/secrets); `ko tv` ratings + where to stream.
- **Agent contract** (AGENTS.md): stdout=data / stderr=notes, exit 0/1/2,
  `--json` errors as `{error, code}`, bare-arg shortcuts.

[Unreleased]: https://github.com/khalido/kotools/compare/v2026.11.13...HEAD
[2026.11.13]: https://github.com/khalido/kotools/compare/v2026.11.7...v2026.11.13
[2026.11.7]: https://github.com/khalido/kotools/compare/v2026.7.27...v2026.11.7
[2026.7.27]: https://github.com/khalido/kotools/compare/v2026.7.14...v2026.7.27
[2026.7.14]: https://github.com/khalido/kotools/compare/v2026.7.9...v2026.7.14
[2026.7.9]: https://github.com/khalido/kotools/releases/tag/v2026.7.9

