# ko — ideas

The single list of candidate subcommands. WORKLOG tracks what happened; this tracks what might.

**The thesis:** for each thing I do often, research the best open-source library/API *once*, bake it in with defaults I've already picked, expose 2–3 flags max. Then neither I nor my agents ever think about it again. Agents calling `ko fetch` instead of curl'ing raw HTML saves tokens and parsing pain.

**Skill or no skill:** the goal is a CLI so intuitive that `--help` *is* the skill — short, opinionated, example-rich. If that fails, ship a `using-ko` skill later. Treat needing a skill as a design smell.

## Next up

- [ ] **`ko fetch <url>`** — URL → clean markdown.
  - Primary: best-in-class extraction lib (research in flight — trafilatura vs readability+markdownify vs jina).
  - `--archive`: resolve via Wayback Machine availability API, fetch the snapshot (use the `id_` raw-HTML suffix), return *that* as markdown. Also the fallback when the live fetch 404s/paywalls.
  - Flags: `--archive`, maybe `--date` (closest snapshot), `--max-chars`. That's it.
- [ ] **`ko yt <url>`** — YouTube → transcript / summary.
  - Transcript: grab directly from YouTube (youtube-transcript-api or similar — no parsing, agents use output as-is).
  - `--summarise`: pydantic-ai over the transcript (we already ship pydantic-ai).
  - Fallback when no transcript exists: Gemini native video understanding — pass URL as `file_data` part + JSON prompt. Pattern proven in `~/code/yaad/yaad/api/gemini.py` (`get_youtube_summary`, gemini-3-flash, ~fractions of a cent per video).

## Backlog (priority order)

- [ ] **`ko q "SELECT ..."`** — DuckDB ad-hoc SQL over JSON/CSV/Parquet, no DB file. (duckdb already in my brew list — confirmed habit.)
- [ ] **`ko rss <feed>`** — feed → TSV/markdown via feedparser. RSS parsing is solved-but-fiddly; agents do it badly with curl. (NetNewsWire user.)
- [ ] **`ko pdf <file>`** — PDF → text/markdown. Fast path: pymupdf4llm. Quality path: marker (already installed as a uv tool — maybe just shell out). Complements `ko arxiv fetch`.
- [ ] **`ko hn`** — HN Algolia search, NDJSON out. Composes with `ko fetch`.
- [ ] **`ko prompt <path>`** — files-to-prompt clone for stuffing repos/dirs into context.
- [ ] **`ko scholar`** — Semantic Scholar citation graph (what arxiv can't give).
- [ ] **`ko summarise`** — opinionated summariser via pydantic-ai. One good prompt, not a prompt framework.
- [ ] Later: `ko clip`, `ko note`, `ko standup`, `ko schema`, `ko embed`.

## Infra

- [ ] PyPI trusted publisher + tag-push GitHub Action (plan in WORKLOG 2026-04-22).
- [ ] MCP server exposing the same modules (`mcp_server.py` stub has the wiring sketch). CLI for humans + bash; MCP for native agent calls. Maybe Railway-hosted later.

## Skip (considered, not a fit)

- `ko gh` — `gh` already excellent, agents use it natively
- `ko jira` / `ko linear` — volatile APIs, painful auth, low return
- `ko atuin` / `ko zoxide` — shell-integrated, can't wrap from Python
- `ko translate` — existing CLIs fine; API cost doesn't justify
- Big famous tools generally (rg, fzf, jq…) — wrap only API/SDK-shaped things that *lack* a good CLI (the Exa rule)
