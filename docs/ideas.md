# ko — ideas

The single list of candidate subcommands. WORKLOG tracks what happened; this tracks what might.

**The thesis:** for each thing I do often, research the best open-source library/API *once*, bake it in with defaults I've already picked, expose 2–3 flags max. Then neither I nor my agents ever think about it again. Agents calling `ko fetch` instead of curl'ing raw HTML saves tokens and parsing pain.

**Skill or no skill:** the goal is a CLI so intuitive that `--help` *is* the skill — short, opinionated, example-rich. If that fails, ship a `using-ko` skill later. Treat needing a skill as a design smell.

## Next up

- [ ] **`ko fetch <url>`** — URL → clean markdown. ~40 lines total. (Research done 2026-06-11.)
  - Extraction: **trafilatura** (pin ≥2.1) — tops every independent benchmark (F1 0.958 Scrapinghub, 0.841 WCXB-2025), native markdown out, built-in fetcher, one call:
    `trafilatura.extract(trafilatura.fetch_url(url), output_format="markdown", include_links=True, include_tables=True)`
  - Rejected: readability+markdownify (dominated), html2text (converter not extractor, GPL), docling (~1 GB PyTorch for web pages, no), markitdown (no boilerplate removal), Crawl4AI (drags in Chromium).
  - Wayback (`--archive`, also auto-fallback on HTTP errors):
    1. Resolve: `GET archive.org/wayback/available?url={url}` (+`&timestamp=YYYYMMDD` for `--date`) → `archived_snapshots.closest`; filter `status=="200"`; empty dict = no capture.
    2. Fetch raw HTML via the **`id_` suffix**: `web.archive.org/web/{timestamp}id_/{url}` — no toolbar/rewritten links; mandatory or trafilatura ingests archive chrome.
    - Gotchas: ~60 req/min then 429 + 1-hr IP block (honor Retry-After, set User-Agent); NYT/FT/Guardian self-excluded from recent archiving.
  - Maybe later: `--jina` escape hatch (`r.jina.ai/<url>`) for JS-heavy pages — cloud, slow (~8 s), but handles what static extraction can't.
- [ ] **`ko yt <url>`** — YouTube → transcript / summary.
  - Transcript: **youtube-transcript-api** (v1.2.4) — hits YouTube's transcript endpoint directly, no video download; auto-captions, manual subs, translation. (yt-dlp is overkill for transcript-only.)
  - `--summarise`: pydantic-ai over the transcript (we already ship pydantic-ai).
  - Fallback when no transcript exists: Gemini native video understanding — pass URL as `file_data` part + JSON prompt. Pattern proven in `~/code/yaad/yaad/api/gemini.py` (`get_youtube_summary`, gemini-3-flash, ~fractions of a cent per video).
- [ ] **`ko x <list>`** — fetch recent posts from X via the official **XDK** (`uv add xdk`, X API v2, my API key in env).
  - `Client(...)` → `client.posts.recent_search(query=...)`, post lookup, list timeline (X API v2 `lists/{id}/tweets`); auto-pagination built in. Auto-generated SDK, endpoints mirror the v2 REST docs.
  - `ko x ai --days 3` → posts from my AI list (real X List ID, or named handle-set in `~/.config/ko/config.toml`), last 3 days, TSV/`--json` out. Raw posts, deterministic — Layer 1, agents consume directly.
  - ⚠️ Verify my API tier covers reads: X API free tier is ~write-only; Basic ($200/mo) is the usual read floor. If that's not on, the fallback is xai-sdk's `x_search(from_date, allowed_x_handles=[...])` — Grok-mediated server-side search, per-request pricing (`response.cost_usd`), genuinely cheap, but returns a synthesized digest + citations rather than raw posts. Could even be both: `ko x` (raw, XDK) and a `ko ai` skill for the digest.
  - Flags: `--days`, `--n`, `--json`. That's it.
- [ ] **Bare-link shortcut: `ko <url>`** — paste a link as the only arg, ko detects it's a URL (deterministic: scheme/domain pattern, no LLM) and routes to `ko fetch`, which sniffs YouTube/PDF/article/dead-link under the hood. The "I just want the markdown of this thing" zero-thought path.

## Backlog (priority order; library picks researched 2026-06-11)

- [ ] **`ko q "SELECT ..."`** — **duckdb** Python package: SQL over CSV/JSON/Parquet, zero schema setup, pipeable. (duckdb already in my brew list — confirmed habit.)
- [ ] **`ko rss <feed>`** — feed → TSV/markdown via feedparser. RSS parsing is solved-but-fiddly; agents do it badly with curl. (NetNewsWire user.)
- [x] **`ko doc <file>`** — SHIPPED 2026-06-11 (was "ko pdf"). **liteparse** (LlamaIndex, v2, Rust): fully local, no models, spatial reading order, OCR fallback; PDF native, Office/images via LibreOffice/ImageMagick conversion (liteparse errors helpfully if missing). Plain-text output (`result.text` — no markdown mode; fine for agent consumption). Bare shortcut: `ko <file>` routes to doc. Quality escalation still available: marker (uv tool — shell out), Mistral OCR (~$0.001/page) for hard scans.
- [ ] **`ko hn`** — direct Algolia REST via httpx (`hn.algolia.com/api/v1/search`, `search_by_date`, `items/{id}` for comment trees). No auth, **zero new deps** — every Python HN wrapper is unmaintained. Composes with `ko fetch`.
- [ ] **`ko prompt <path>`** — depend on **files-to-prompt** (simonw, v0.6) rather than rebuild: `-c` Claude XML, `-m` markdown fences, feature-stable.
- [ ] **`ko scholar`** — **OpenAlex REST** (250M works, free, no key) for breadth + **semanticscholar** PyPI for TLDRs/citation graph.
- [ ] **`ko summarise`** — composition, not a wrapper: pipe `ko fetch`/`ko pdf` output into the existing pydantic-ai agent. One good prompt, not a prompt framework.
- [ ] Later: `ko clip`, `ko note`, `ko standup`, `ko schema`, `ko embed`.

## New candidates (HN scan, 2026-06-11 — gap-verified, not committed to)

HN consensus 2025–26: CLI beats MCP for agent tooling (~10–32× lower token cost) — ko's exact thesis. These had confirmed gaps (no decent CLI exists) + ready SDKs:

- **`ko cal`** — Google Calendar readonly. Reuses our existing OAuth flow + token cache, just one more scope; `gcalcli` is display-oriented, not pipeable. Cheapest possible add.
- **`ko fred`** — FRED economic data (844K series, free key, no CLI anywhere). Time-series → TSV matches the gsheets pattern.
- **`ko certs`** — crt.sh cert-transparency/subdomain lookup. No SDK, undocumented JSON endpoint, maximally curl-hostile.
- **`ko translate`** — DeepL, 500K chars/mo free (un-skipped: the free tier kills the old cost objection). Official CLI is Node/display-y; Python `deepl` SDK is clean. `ko fetch url | ko translate --to en`.
- Weaker / only-if-I-need-them: `ko stocks` (Polygon), `ko trends` (pytrends, scrapes unofficial endpoint), `ko notion`, `ko ask` (Perplexity Sonar — overlaps exa).

## `ko ai` — the agent layer (design sketch, 2026-06-11)

ko grows two explicit layers:

- **Layer 1 — deterministic plumbing.** `ko exa`, `ko fetch`, `ko yt`, `ko hn`… No LLM ever. Pipeable, cheap, safe for agents to call blind.
- **Layer 2 — `ko ai "<prompt>"`.** A pydantic-ai agent whose tools *are* the Layer 1 module functions. Every new subcommand = a new agent tool for free, because `cli.py` and the agent wrap the same functions (same trick as the MCP plan). Absorbs/renames today's `ko agent`.

Decisions:

- **Explicit `ko ai`, not bare `ko "<prompt>"`** — a typo'd subcommand must never silently spend tokens, and agents calling Layer 1 need certainty it stays deterministic. Intelligence is opt-in.
- **Skills as markdown recipes** at `~/.config/ko/skills/<name>.md` — frontmatter (description, allowed tools) + prompt body with `{args}`. `ko ai hn opus` → loads `hn.md`, runs: search HN for "opus" by date, keep >50-comment discussions, read top ≤5 (urls + comment trees), summarise + key highlights. Editable text, not code — same pattern as Claude skills, applied to my own CLI.
- **SQLite at `~/.local/share/ko/ko.db`** — two tables, pays rent twice:
  - `conversations`: pydantic-ai message histories (JSON-serializable natively) → `ko ai --continue`/`--resume` for free.
  - `fetches`: (url, ts, markdown) — doubles as the **Layer 1 cache**: repeat `ko fetch` = instant + free (agents re-fetch constantly). `--no-cache` to bypass.
  - Boundary: cache + history only. Knowledge store / saved links is yaad's job — don't rebuild yaad inside ko.
- **Smart routing lives in `ko fetch`, not the agent.** No `ko linktopdf`/`linktoyt`: `ko fetch <anything>` sniffs deterministically — youtube.com/youtu.be → transcript, content-type PDF → pymupdf4llm, else trafilatura, dead link → Wayback. One universal "thing → markdown" command. Smart ≠ fuzzy. Plus the bare-link shortcut: `ko <url>` (top-level URL detection) routes straight to fetch.

### Implementation notes (research 2026-06-11: pydantic.dev docs + refs/pydantic-ai source + sift/yaad prior art)

The skills system is ~zero custom code — pydantic-ai 2026 ships the pieces:

- **Skills = `Capability` with `defer_loading=True`.** Each skill collapses to a one-line catalog entry (id + description) until the model calls `load_capability(id)` — context stays lean no matter how many skills exist. Alternative for declarative files: `Agent.from_file(<yaml>)` (proven in sift — 15-line sector YAMLs, `sift/run.py:77`).
- **Tool grouping: one `FunctionToolset` per module** (`exa`, `arxiv`, `fetch`, `yt`, `hn`), each carrying its own `instructions=`. Per-skill allowed-tools = `FilteredToolset` or per-run `toolsets=[...]` (run-time injection is supported).
- **REPL: reuse `clai`'s `run_chat()`** (`pydantic_ai/_cli/__init__.py`, ~100 importable lines: prompt_toolkit + rich.Live markdown streaming + /multiline /cp /markdown). It takes `message_history=` — exactly the injection point for our SQLite resume. Don't hand-roll what `agents/research.py` started.
- **Persistence (blessed API):** `ModelMessagesTypeAdapter.dump_json(messages)` / `.validate_json(blob)`; store one blob per exchange keyed by session (canonical example: `examples/chat_app.py`). Built-in `conversation_id` threads runs → `ko ai --continue`. `ReinjectSystemPrompt()` capability handles resumed sessions. **Record `pydantic_ai_version` per conversation** — old versions can't read newer transcripts (sift learned this: `sift/run.py` run.json).
- **Steal from sift (`~/code/jabberwocky/sift/run.py`):** `UsageLimits(request_limit=N)` runaway cap; cost-per-run via `ModelResponse.cost().total_price` (genai-prices, already in our tree); retrying httpx transport (`AsyncTenacityTransport` + `wait_retry_after`); *the runner writes outputs, the agent never does*.
- **From pydantic-deepagents: skip the framework** (autonomous subagent teams — overkill). Steal two guards eventually: stuck-loop detection (identical repeated tool calls → break) and large-tool-output eviction.
- **Streaming:** simple path is 3 lines (`rich.Live` + `result.stream_output()` → `Markdown`); upgrade to `agent.iter()` only when we want "calling exa_search…" progress lines.

## Infra

- [ ] PyPI trusted publisher + tag-push GitHub Action (plan in WORKLOG 2026-04-22).
- [ ] MCP server exposing the same modules (`mcp_server.py` stub has the wiring sketch). CLI for humans + bash; MCP for native agent calls. Maybe Railway-hosted later.

## Skip (considered, not a fit)

- `ko gh` — `gh` already excellent, agents use it natively
- `ko jira` / `ko linear` — volatile APIs, painful auth, low return
- `ko atuin` / `ko zoxide` — shell-integrated, can't wrap from Python
- Tavily / Brave search / Firecrawl / IPinfo / CoinGecko wrappers — official CLIs now exist (checked 2026-06)
- newspaper3k (dead since 2018), every Python HN wrapper (unmaintained), docling-for-HTML
- Big famous tools generally (rg, fzf, jq…) — wrap only API/SDK-shaped things that *lack* a good CLI (the Exa rule)
