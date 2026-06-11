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

## Backlog (priority order; library picks researched 2026-06-11)

- [ ] **`ko q "SELECT ..."`** — **duckdb** Python package: SQL over CSV/JSON/Parquet, zero schema setup, pipeable. (duckdb already in my brew list — confirmed habit.)
- [ ] **`ko rss <feed>`** — feed → TSV/markdown via feedparser. RSS parsing is solved-but-fiddly; agents do it badly with curl. (NetNewsWire user.)
- [ ] **`ko pdf <file>`** — **pymupdf4llm**: PDF → markdown in one call, no ML, megabytes not gigabytes. Quality escalation paths: marker (already a uv tool — shell out), or Mistral OCR (~$0.001/page) for hard scans. docling rejected (~1 GB PyTorch install).
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
