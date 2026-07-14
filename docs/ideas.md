# ko — ideas

The single list of candidate subcommands. WORKLOG tracks what happened; this tracks what might.

**The thesis:** for each thing I do often, research the best open-source library/API *once*, bake it in with defaults I've already picked, expose 2–3 flags max. Then neither I nor my agents ever think about it again. Agents calling `ko fetch` instead of curl'ing raw HTML saves tokens and parsing pain.

**Skill or no skill:** the goal is a CLI so intuitive that `--help` *is* the skill — short, opinionated, example-rich. If that fails, ship a `using-ko` skill later. Treat needing a skill as a design smell.

## Status & roadmap (updated 2026-06-27)

**Shipped this session (2026-06-22 → 27):** gmail `thread`; Google lib shared-spine refactor (errors/http/id in `google_auth`); CLI agent-friendliness pass (clean errors, no stdout leaks, fuller `--json`, `AGENTS.md`, trailing-`help`); **`ko prompt`** (kickoff briefs); `ko cal --calendar` allowlist; `ko exa search` summaries + human dates; hn `--min-comments` fix; **`ko mcp` inspect/call/overview/servers** (client + `mcp.json` registry with `${ENV}` expansion + an agent that summarizes a server); **`ko billing`** (OpenRouter); **local logging via loguru** + `ko logs`; repo made public + PyPI-ready; `cli.py` split into `_cli_shared`/`cli_google`/`cli_web`/`cli_ai`; `pydantic-ai-slim[mcp]` added.

**The one blocker (USER action):** finish Google auth — APIs enabled ✓ (gcloud, project `kotools-500611`); now **publish the consent app** (External) + `ko gsheets auth` & `-a personal auth`. Everything Google-shaped waits on this.

**Build queue (decided, in order):**
1. **Google auth live** (user) → smoke-test `ko cal` / `gmail` / `gdocs` / `gsheets`.
2. **PyPI publish** (user, token coming): `uv build --no-sources && uv publish`.
3. **`ko note`** — append-and-review Google Doc (tiny; prepend timestamped blocks; needs gdocs auth). See the `ko note` entry below.
4. **`ko brief`** — morning brief: deterministic gather (`cal` filtered + `gmail` unread + `hn`/papers + `tt`) → one cheap-model synthesis in my voice; `--raw` skips the LLM. A scripted pipeline, NOT an agent loop.
5. **`ko ai`** — SHIPPED 2026-07-13 (v1: all toolsets + own memory, medium tier, 30-request cap; skills/MCP-toolsets still future — design below).
6. **Telemetry → PostHog** (auto-on when key set): command-log loguru sink; LLM traces via pydantic-ai `InstrumentationSettings` → local + PostHog (rides on `ko ai`). See Infra (a)/(b).
7. **MCP server** (`ko mcp serve`, FastMCP) + **bundled default servers** (railway/context7); **Telegram bridge** — all ride on `ko ai`.
8. **Far future:** agent fleet / remote sandboxes — `docs/remote.md` (planning) + `docs/fleet.md` (brief).

**Smaller, any-time:** `ko prompt suggest` (project detection) + `include:`; CLI polish (enumerated errors, idempotency field, `has_more`+hint, `-n`/`--n` standardize, `NO_COLOR`); `ko publish --md` mobile nav; `ko gdocs` review/comment (Drive scope). Backlog commands (`yt`, `q`, `news`, `scholar`…) further down.

## Next up

- [x] **`ko fetch <url>`** — SHIPPED 2026-06-13 as designed: trafilatura markdown (live-verified on real articles), arxiv links → arxiv2md, PDF links → ~/Downloads + liteparse (`--no-save`). Bare shortcut `ko <url>` routes here. Gotcha found: the Wayback availability API chokes on percent-encoded `url=` params — query built by hand. `--jina` escape hatch still future.
  - **Fallback chain refined (2026-06-13, archive.today added):** dead link (HTTP error) → Wayback (page is gone). 200-but-empty (paywall) → **archive.today first** (`archive.ph/newest/<url>`, JS-rendered capture beats Wayback on hard paywalls like SMH/NYT/FT — latest snapshot, we don't pick versions), then Wayback. Force with `--archive` (Wayback) / `--archive-is`. ⚠️ archive.today gates datacenter IPs behind a CAPTCHA/429 — best-effort, fails loud and cascades; works from residential (my Mac/home server), flaky from a cloud VM. Verified the block-detection + cascade; live snapshot retrieval is residential-only so untested from the sandbox.
- [x] **`ko llm "<prompt>"`** — SHIPPED 2026-06-13 per the settled design: model-less v2 Agent + per-run `model=`, default `google:gemini-3.5-flash` (`KO_DEFAULT_MODEL`; note v2 renamed the prefix — `google-gla:` is gone), stdin appended as `<input>` block, `-s` swaps system prompt, no tools ever. `-m` tab-completion via `known_model_names()` filtered to providers with env keys set (OpenRouter live-catalog completion still future).
- [x] **`ko x search|list|lists`** — SHIPPED 2026-06-12 (untested live — no X_BEARER_TOKEN in env yet; ⚠️ verify tier covers reads on first run: free is ~write-only, Basic is the read floor; fallback if reads are off-plan: xai-sdk's `x_search` Grok digest). Official XDK. `search` = recent index (~7 days), author expansion, `--top`. `list <name>` = posts from my list by name, owned+followed, case-insensitive, name→id cached at `~/.config/ko/x_cache.json`; bare `ko x ai` sugar via the main() dispatcher. Home timeline deliberately absent: needs OAuth user-context, not app-only bearer.
- [x] **Bare-link shortcut: `ko <url>`** — SHIPPED 2026-06-13 with fetch. — paste a link as the only arg, ko detects it's a URL (deterministic: scheme/domain pattern, no LLM) and routes to `ko fetch`, which sniffs YouTube/PDF/article/dead-link under the hood. The "I just want the markdown of this thing" zero-thought path.
- [x] **`ko papers search|get|cites|refs|similar`** — SHIPPED 2026-07-03 as designed (`docs/papers-cli-design.md`): OpenAlex backbone (no key), S2 tldr/similar behind optional `S2_API_KEY`, DOI routing in `fetch.py` (doi.org URLs → OA copy first — verified live on a Nature DOI that previously extracted to nothing), `papers_search`/`papers_cites` added to the research agent's toolset. Zero new deps. ⚠️ found live: occasional OpenAlex bad-merge records (wrong title, right abstract) — search the title and use the W-id when a record looks off.

## `ko agent repo` — cheap repo-explorer agent (SHIPPED 2026-07-10, as designed below)

Built same day: `agents/_files.py` + `files` toolset + `agents/repo.py` + `ko agent repo`.
Live: answered "how does harness prevent path traversal" with exact quotes + file:line,
using the refs/CLAUDE.md takeaways, for $0.0005/run on deepseek-flash. Design notes kept:

The want: "how does reference repo X do Y / find me something relevant" answered by a
**basic-tier** model with read-only access to `~/code` (+ refs). Decision after a source
dive on `pydantic-ai-harness` (now in refs — takeaways in refs/CLAUDE.md):

- **v1: hand-roll a read-only `files` FunctionToolset** in `_toolsets.py` (~5 tools:
  `list_dir`, `read_file` (line-capped, binary guard), `grep` (shell out to `rg`, capped),
  `find_files`, maybe `git_log`), rooted at `~/code`. **Read-only by construction — no
  write tools exist**, which beats harness's approach (its FileSystem has no `read_only`
  flag; write tools stay in the model's tool list and fail via ModelRetry — token waste
  and confusion for a cheap model). Port ONE thing from harness: its TOCTTOU-safe path
  containment (resolve symlinks with `realpath` BEFORE the `is_relative_to(root)` check).
  Then `ko agent repo` = the usual ~15-line agent file: basic tier, instructions = the
  refs CLAUDE.md explore method (survey cheap → read only the few files that matter →
  cite file:line; docs/ and examples/ first).
- **Don't adopt `pydantic-ai-harness` yet** — same call as fastmcp (adopt late): it's
  explicitly alpha (0.x, "minor releases may break"), imports pydantic-ai *private*
  internals with cross-version shims, and monty (the Rust sandboxed-Python interpreter
  behind CodeMode) is 0.0.17. **Adoption triggers**: harness ~0.1/stable or bundled into
  pydantic-ai; FileSystem grows a real read-only mode; or we want CodeMode.
- **CodeMode: skip for this use case.** Its win is batching many tool calls into one
  model round-trip of generated Python — valuable for strong models with big tool
  surfaces, wrong for a cheap explorer (monty's Python subset: no classes, no time, no
  third-party imports — a flailing hazard for basic-tier models; plain tool calls are
  the right altitude). **But monty itself is the watch item**: a local Rust-sandboxed
  Python is a lighter answer than the Cloudflare Sandbox idea (Infra) for the "agent
  writes + runs a throwaway script" need. Revisit when monty isn't 0.0.x.
- Harness's `RepoContext` (experimental) auto-loads CLAUDE.md/AGENTS.md up the tree —
  nice idea to steal as one cheap tool (`read_repo_context(repo)`) rather than adopt.

## `ko agent repo` friction (from the 2026-07-12 refs-map sweep — 21 real runs, $0.09)

The sweep that filled refs/CLAUDE.md (19 missing + 2 stub entries) doubled as an eval. Candidates,
none urgent:
- [ ] **Hard scope constraint** — the `pi` run confidently described sibling `pi-autoresearch`
  instead (context bleed between adjacent refs). Instructions ask for scope discipline; a real
  `--root refs/<name>` that re-roots the files toolset would make it impossible rather than polite.
- [ ] **Structured output for bulk use** — orchestrators distilling free-markdown answers into map
  bullets is post-processing an agent could skip: `ko agent repo --json` with a small output_type
  (answer, key_files, one_liner). Only worth it if bulk sweeps recur.
- Noted, no action: cheap model ignores "2-3 sentences" brevity asks (returns 3-8 paragraphs —
  prompt-side, not tool-side); cost notes could carry a caller label (orchestrators can label their
  own logs); 4-way parallel `ko agent repo` runs were flawless.

## Backlog (priority order; library picks researched 2026-06-11)

- [ ] **Skills to build** (from the 2026-07-06 mining + Fable review; each lives in its *home repo's*
  `.claude/skills/`, not centralized here — see WORKLOG). Build order:
  1. **invoice** (ops) — scan WORKLOGs → hours by client → next invoice # → CSV + cover email. Bundle
     the tally as a *script* (money needs reproducibility); LLM only does client-mapping + email.
  2. **sheets-bulk-fix** (nibbleedge-sheets) — apply a change across ~88 client sheets via the `ne` loop;
     encodes dry-run-on-1-first + write-then-reread verify + partial-failure resumability.
  3. **pit-privacy-check** (thinker) — audit a change vs the privacy/PIT invariants before commit; wire
     thinker's CLAUDE.md to point at it (maybe fold into a thinker-local code-review wrapper).
  4. **data-pipeline-review** (global, `~/.claude/skills/`) — end-to-end audit for *silently-wrong numbers*
     (hardcoded FY, partial-row→0); highest value-per-fire, thinnest evidence — write short, tune the
     description hard against over-triggering vs code-review/security-review, iterate after 2-3 runs.
  5. **drizzle-migration** (global, optional) — schema edit → generate → *review the SQL* → apply →
     `svelte-kit sync` → repo's gate. Trigger on editing `schema.ts`, not the word "migration".
  - Killed by review: project-orient (habit, CLAUDE.md auto-loads), gh-issue-sync (habit + no session-end
    trigger; the sub-issue `gh api` incantation → a docs snippet instead). Skipped: semver-release, everx-feature-gate.
  - Possible `ko skill new` scaffolder (frontmatter/description templater) if skill authoring becomes frequent.



- [ ] **satteri for `ko publish --md`** (considered 2026-07-14, [satteri.bruits.org](https://satteri.bruits.org/)) —
  Rust-core markdown pipeline with JS plugins (npm/WASM). NOT now: the `--md` scaffold is
  deliberately no-build (markdown-it via CDN + a 15-line client TOC builder), and satteri's
  speed only bites in build pipelines over many files. Trigger = adding pre-rendering
  (static HTML / MDX) to publish; then satteri vs remark is the real comparison. Its docs
  don't advertise a TOC block — TOC would come from its AST plugin hooks either way.
- [ ] **`ko publish` lifecycle + polish** (from the 2026-07-14 pathway review):
  - **`ko publish rm <name|dir>`** — the missing delete verb (`wrangler delete` + registry cleanup
    + custom-domain DNS note). Evidence: `ko-test` from June still live in the registry.
  - **`list` shows age** — registry already stamps `updated_at`, list doesn't print it; a month-stale
    kotools-site went unnoticed until 2026-07-14. Add the column; optional `--check` pings each URL.
  - **Auto-OG at scaffold** — the scaffold CLAUDE.md calls `og:description` "the ONE index.html edit
    worth making"; fill it from title/first paragraph automatically instead.
  - (Considered, skipped: a `ko publish docs` repo-docs pathway — kotools-site being curated separately
    from internal docs/ is deliberate.)
- [ ] **`ko publish` hardening** — deferred from the 2026-07-03 Fable review (real, non-urgent):
  - **`deploy` rewrites `--md` sites to SPA mode** (`publish.py` `ensure_config` defaults `spa=True`;
    md scaffolds are `spa=False`) — renaming an md site silently breaks its 404 handling (every bad
    `?page=` serves index.html). Behavioral bug, most worth fixing.
  - **Registry not recorded when the URL is unparseable** (`if url:` guard) → poisons the *next*
    deploy with a false "already a Worker, use --force".
  - **Takeover guard disarms for `wrangler login` users** (`worker_exists` returns `None` without API
    creds) — the "won't silently take over" promise holds only in the token path.
  - **JSONC name parsed with a first-match regex** — fine for scaffolded files, unsafe for the
    hand-edited D1/R2 configs the module invites.
  - (Done in that pass: subprocess timeouts on npm/​wrangler.)
- [ ] **`ko x` OAuth 2.0 user-context** (deferred 2026-07-05) — the app-only bearer can't reach
  `get_me` (auto-handle), **bookmarks** (X's "save"), likes, home timeline, or *followed* lists;
  all need a UserToken. Mirror the Google OAuth pattern (PKCE local-server flow + token cache +
  refresh; X access tokens ~2h). Worth it mainly for **`ko x bookmarks`** (curated saves) — the
  rest is niche. Skip unless bookmarks/likes become a habit; the bearer already covers lists +
  search + user timelines + full-archive.
- [ ] **Shared `http.get_json()` helper** — hn/hf/papers/tmdb each hand-roll a near-identical httpx
  `_get` (now over the CLAUDE.md "three things" bar). One chokepoint = one place for timeout/retry
  policy, and makes a future `import httpx2 as httpx` swap a one-file change. (httpx2 = Pydantic's
  maintained httpx successor; **wait to migrate** — pydantic-ai still pins plain `httpx` as of v2.4,
  the move lives only on its unmerged `use-httpx2` branch. Trigger: that branch shipping in a release.)
- [ ] **`ko papers bib <id>` / `ko papers watch`** — extensions to the shipped `ko papers`
  (lab-motivated but generic, noted 2026-07-03): `bib` = DOI→BibTeX (doi.org content
  negotiation `Accept: application/x-bibtex`, or from OpenAlex fields; ~flag-sized).
  `watch` = saved queries + only-new-since-last-check (OpenAlex `from_publication_date`
  + state file in `~/.local/state/ko`); "RSS for papers", cron+`ko ai` digestible.
- [ ] **`ko dataset get <doi|zenodo-id>`** — research-dataset fetcher. Zenodo REST is
  keyless (`/api/records/{id}` → files + checksums): list, selectively download,
  verify. figshare/OSF later behind the same verb. First real use: the 2DMatGMM
  in-focus set (10.5281/zenodo.8042834) for the UTS autofocus benchmark.

- [ ] **`ko yt <url>`** — YouTube → transcript / summary.
  - Transcript: **youtube-transcript-api** (v1.2.4) — hits YouTube's transcript endpoint directly, no video download; auto-captions, manual subs, translation. (yt-dlp is overkill for transcript-only.)
  - `--summarise`: pydantic-ai over the transcript (we already ship pydantic-ai).
  - Fallback when no transcript exists: Gemini native video understanding — pass URL as `file_data` part + JSON prompt. Pattern proven in a private project (`get_youtube_summary`: gemini-3-flash, ~fractions of a cent per video).
- [ ] **`ko q "SELECT ..."`** — **duckdb** Python package: SQL over CSV/JSON/Parquet, zero schema setup, pipeable. (duckdb already in my brew list — confirmed habit.)
- [ ] **`ko news` / `ko aus`** — curated news headlines, idea 2026-06-13. The real want: "quickly see today's AU news" — headlines + a line of text + link. Build on feedparser over a *baked* feed bundle, not a generic feed arg: `ko news aus` loads a curated AU set, `ko news ai`/`tech` other bundles. Feeds give headlines+summary free even when the article is paywalled. AU sources: ABC News AU (no paywall, clean RSS — the backbone), Guardian AU (free RSS + a proper free API if we want more), SMH/The Age (Nine — RSS headlines fine; full article via `ko fetch`, which now has the archive.today paywall fallback + the user's note that SMH serves non-JS browsers so plain httpx often works). Composes: `ko news aus | ko llm "what's the gist of today"`, or `ko fetch <link>` to read one. Supersedes the generic `ko rss` below — curation is more ko than a feed arg.
- [ ] **`ko nyt`** — NYT APIs (developer.nytimes.com, free key, generous limits): Top Stories / Most Popular / Article Search return headline + abstract + url, clean JSON, no scraping. A tidy fit *if* I want US news — but the actual habit is AU news, so lower priority than `ko news aus`. Could fold in as a `ko news` source rather than its own command.
- [ ] **`ko rss <feed>`** — generic feed → TSV/markdown via feedparser. Likely absorbed into `ko news`'s engine (curated bundles + a `--feed <url>` escape hatch) rather than a standalone command.
- [x] **`ko doc <file>`** — SHIPPED 2026-06-11 (was "ko pdf"). **liteparse** (LlamaIndex, v2, Rust): fully local, no models, spatial reading order, OCR fallback; PDF native, Office/images via LibreOffice/ImageMagick conversion (liteparse errors helpfully if missing). Plain-text output (`result.text` — no markdown mode; fine for agent consumption). Bare shortcut: `ko <file>` routes to doc. Quality escalation still available: marker (uv tool — shell out), Mistral OCR (~$0.001/page) for hard scans.
  - **Head-to-head vs arxiv2md** (2606.12412, 21pp/6 MB, 2026-06-11): arxiv2md wins decisively *for arxiv* — real markdown structure, clean LaTeX equations, refs stripped (65K chars) because it parses the LaTeX source, not the PDF. liteparse: 3.5 s, decent reading order, but two-column tables flatten messily, equations garble, layout artifacts (103K chars). **Keep both**: `ko arxiv fetch` for arxiv (source beats PDF every time), `ko doc` for everything that only exists as a PDF/scan/Office file.
- [x] **`ko hn`** — SHIPPED 2026-06-11. Algolia REST via httpx: `top` (hckrnews top-10/20 habit — top-by-points per day window; Algolia's `front_page` tag only marks the *current* front page, useless for past days), `search` (relevance, 12-month default, `--min-comments`, `--new`), `item` (comment tree → indented text, hrefs expanded). RSS rejected: no points/comment counts. Composes with `ko fetch` when that ships.
- [ ] **`ko prompt <path>`** — depend on **files-to-prompt** (simonw, v0.6) rather than rebuild: `-c` Claude XML, `-m` markdown fences, feature-stable.
- [x] **`ko hf`** — SHIPPED 2026-06-11 (idea from HF's huggingface-papers skill). hf.co/papers REST, no auth: `top` (Daily Papers, trending = community upvotes), `search` (hybrid semantic), `info` (github + stars, AI summary, linked models/datasets/spaces), `get` (markdown, indexed papers only — else points at `ko arxiv fetch`). Covers the "what AI papers matter today" slice of `ko scholar`.
  - Discovered after shipping (2026-06-12): the official `hf` CLI has `papers ls|search|info|read` — same four ops, same endpoints. Strict Exa-rule reading says we'd have skipped; keeping ours anyway: trending-by-default, composes with `ko arxiv fetch` (cross-tool fallback hints), and no extra CLI install for agents. If `ko hf` ever drifts from the API, deleting it in favour of `hf papers` is fine.
  - **Coverage measured (2026-06-12, sonnet agent):** HF is *not* an arxiv mirror — indexing is community-gated (Daily Papers submission or Hub-README citation). Hit rates: ~33% of day-old AI papers (cs.LG 50%, cs.CL 40%, cs.AI only 10%), ~15% of ordinary 2-week-old cs.AI, 100% of landmark papers, 0% non-AI (math.AG). Lookups ~0.28s, search ~0.75s, no rate-limiting (vs arxiv's 429/503 moods). Search quality clearly beats arxiv export ("agent memory" → 8/8 on-topic vs arxiv's keyword soup). **Division of labour: `ko hf search` for impactful/known AI work, `ko arxiv search` for exhaustive/recent/non-AI.** `ko hf get` returns real markdown (56 headers on TradingAgents, math preserved) via arxiv's HTML version.
- [ ] **`ko scholar`** — **OpenAlex REST** (250M works, free, no key) for breadth + **semanticscholar** PyPI for TLDRs/citation graph. Partially covered by `ko hf` for AI papers; still the pick for citation graphs / non-AI fields.
- [ ] Later: `ko clip`, `ko note`, `ko standup`, `ko schema`, `ko embed`.

## New candidates (HN scan, 2026-06-11 — gap-verified, not committed to)

HN consensus 2025–26: CLI beats MCP for agent tooling (~10–32× lower token cost) — ko's exact thesis. These had confirmed gaps (no decent CLI exists) + ready SDKs:

- [x] **`ko tv`** — SHIPPED 2026-06-13 (ported from my chota-bot TS tool — the "is it good and where do I stream it" check I do often). TMDB v4 Read Access Token (free tier), searches movies+TV merged by popularity, AU watch providers by default (`--country`; data is JustWatch-supplied via TMDB partnership — no scraping). Provider dedupe keeps each service's best offer (stream > free > ads > rent > buy). Live-verified.
- **`ko cal`** — Google Calendar readonly. Reuses our existing OAuth flow + token cache, just one more scope; `gcalcli` is display-oriented, not pipeable. Cheapest possible add.
- **`ko fred`** — FRED economic data (844K series, free key, no CLI anywhere). Time-series → TSV matches the gsheets pattern.
- **`ko certs`** — crt.sh cert-transparency/subdomain lookup. No SDK, undocumented JSON endpoint, maximally curl-hostile.
- **`ko translate`** — DeepL, 500K chars/mo free (un-skipped: the free tier kills the old cost objection). Official CLI is Node/display-y; Python `deepl` SDK is clean. `ko fetch url | ko translate --to en`.
- **`ko pwc`** — paperswithcode.co (Niels/HF's revival of Papers with Code, HN launch 2026-06: news.ycombinator.com/item?id=48443644). Looks API-less but isn't: full FastAPI behind it, **OpenAPI spec at `paperswithcode.co/openapi.json`** (98 paths), all reads anonymous — verified 2026-06-12. Key endpoints: `/api/v1/papers/trending` (github-star-velocity, full paper objects incl. repo), `/api/v1/papers/search?q=` (returns **citation counts** — neither hf nor arxiv has those), `/api/v1/papers/arxiv/{id}`, `/api/v1/tasks/{id}/papers` + `/api/v1/datasets/.../metrics` (**SOTA leaderboards** — the PwC crown jewel), `/api/v1/conferences/`. Unique vs `ko hf`: leaderboards, citations, star-velocity trending, methods taxonomy. Caveats: site is weeks old, API undocumented (could churn — pin to /openapi.json drift), coverage is curated-high-impact only for eval results. Wait for it to stabilize a bit; candidate shape: `ko pwc top|search|sota <task>`.
- Weaker / only-if-I-need-them: `ko stocks` (Polygon), `ko trends` (pytrends, scrapes unofficial endpoint), `ko notion`, `ko ask` (Perplexity Sonar — overlaps exa).

## Research scan (2026-06-26) — agent-native patterns + `ko prompt` evolution

Three Sonnet sub-agents swept HN + Exa + web for what builders are doing in 2026 (dogfooding `ko hn`/`ko exa` — which **found a real bug**: `ko hn search --min-comments` 400'd on a comma-joined Algolia `numericFilters`; fixed 2026-06-26, now a list). Curated to the most promising; the rest were generic or already-done (`--json`, exit codes, stderr, AGENTS.md, TTY-detection).

### Agent-friendly CLI polish (small, do-soon — builds on the 2026-06-26 agent-friendliness pass)
- [ ] **Enumerated errors** — when an arg is a fixed set, name the valid set in the error: `--sort must be one of points|date|relevance (got 'foo')`. Lets an agent self-correct without re-reading `--help`. One helper over the few enum flags. [trevinsays.com/p/10-principles-for-agent-native-clis](https://trevinsays.com/p/10-principles-for-agent-native-clis)
- [ ] **`retryable` / `retry_after` on JSON errors** — extend our `{error, code}` (already shipped) toward RFC-9457 shape: add `retryable: true` + `retry_after` on rate-limit/network errors so an agent runs an explicit retry policy from fields, not prose. Cloudflare measured ~14k→256 token error payloads doing this. [blog.cloudflare.com/rfc-9457-agent-error-pages](https://blog.cloudflare.com/rfc-9457-agent-error-pages/)
- [ ] **Idempotency field on write commands** — `create_doc`/`add-tab`/`cal add`/`publish` return `{"id": ..., "existing": false}` (true on a retry that matched). Agents retry constantly; one field kills duplicate-resource surprises. Add before there are more write commands. [trevinsays.com](https://trevinsays.com/p/10-principles-for-agent-native-clis)
- [ ] **Bounded-list `has_more` + narrowing hint** — `ko hn/exa search --json` should include `{"has_more": true, "hint": "raise --n or add --min-comments"}` so the agent refines instead of asking for everything. The truncation-signal the agent-friendliness review deferred. [trevinsays.com](https://trevinsays.com/p/10-principles-for-agent-native-clis), [wavespeed.ai/blog/posts/how-to-reduce-agent-token-costs-cli](https://wavespeed.ai/blog/posts/how-to-reduce-agent-token-costs-cli/)

### `ko prompt` evolution (the new feature — where to take it)
- [ ] **`ko prompt suggest` (project detection via frontmatter `tags:`)** — add `tags: [svelte, drizzle]` to briefs; `ko prompt suggest` sniffs the cwd (`package.json`/`pyproject.toml`/deps) and surfaces matching briefs ("looks like SvelteKit → `ko prompt sveltekit-app`"). Highest-leverage `ko prompt` add — closes the discoverability gap that no prior-art tool fills. [AGENTS.md v1.1 tags](https://github.com/agentsmd/agents.md/issues/135), [cursor.com/docs/rules](https://cursor.com/docs/rules)
- [ ] **`include:` compose directives** — frontmatter `include: [base-stack, drizzle-gotchas]` inlined at render time. DRY across briefs without a template engine — a `python-cli` and `sveltekit-app` brief share one `my-voice`/`deploy-cloudflare` sub-brief. [mbleigh.dev/posts/context-engineering-with-links](https://mbleigh.dev/posts/context-engineering-with-links/)
- Design validated (no action, just confirms our calls): **pipe the full brief text, not a reference** — Vercel evals: always-in-context beat lazy-invoked skills 100% vs 79%, so `ko prompt <name> | claude` is right; don't add a "register brief" indirection. And **briefs should name the skills to load** (we just did this for `shadcn-svelte`) — `ko prompt` is the human-authored *composition root*; skills are the action layer. [vercel.com/blog/agents-md-outperforms-skills-in-our-agent-evals](https://vercel.com/blog/agents-md-outperforms-skills-in-our-agent-evals)
- Maybe-later, lower conviction: `--compact` (strip prose → frontmatter + bullets for token-tight sessions); `--var key=value` handlebars params (likely YAGNI for kickoff briefs — [promptcmd](https://github.com/tgalal/promptcmd) does the full version); `ko prompt lint` to flag generic/inferable content (keep briefs to non-obvious gotchas). Prior art surveyed (promptcmd, MS PromptKit, promptpm) — all are team registries or prompt-executors; **`ko prompt`'s "personal authored kickoffs by name" niche is genuinely unfilled.**

### `ko ai` layer — strong composition ideas (feed the agent layer below)
- [ ] **`ko ai brief` — the morning brief.** The single best "compose what ko already has" idea: `ko hn top` + `ko cal --today` + `ko gmail recent --unread` + `ko hf top`/`ko arxiv search` (my topics) → one cheap-model synthesis. No new infra, pure composition — exactly Layer 2's thesis. Supersedes/absorbs the old `ko standup` placeholder. [Fisher521/morning-brief](https://github.com/Fisher521/morning-brief), [gokborayilmaz/daily-briefing-agent](https://github.com/gokborayilmaz/daily-briefing-agent)
- [ ] **Personal `context.md` injected at `ko ai` cold-start** — a `~/.config/ko/context.md` (name, tz, topic priorities, writing tone) the agent layer always loads, so it stops re-asking "what do you care about?". A `ko prompt`-shaped sibling for the *agent's* standing context. [lovincyrus/personal-vault](https://github.com/lovincyrus/personal-vault) (skip the encryption at personal scale)
- [ ] **`ko ai critique <draft>`** — re-call `ko llm` with a devil's-advocate system prompt to stress-test a draft before `ko x`/`ko publish`. Thin, and a natural `ko ai` skill. [aaddrick/contrarian](https://github.com/aaddrick/contrarian)
- [ ] **Session self-critique → tool feedback loop (design note 2026-07-09).** After a `ko agent` research run, one extra cheap turn — "critique your tools: what was missing, which defaults fought you?" — saved next to the session file (`~/.local/state/ko/sessions/<id>.critique.md`). The tools' heaviest user is now the model; let it file the bug reports (the 2026-07 review's hn gaps — no `hn_top`, hidden 12-month search window — are exactly what a critique pass would have surfaced). Deliberately **not built yet**: an unconditional extra model call per run silently changes cost/latency, so it wants a `--critique` flag (cli.py flag + ~10 lines in `_shared.run` writing beside `sessions.save`), and the payoff is in *aggregate* review — which wants `ko sessions summarize` + the SQLite layer to exist first. Build the flag when summarize lands; batch-reading critiques is what turns them into `_toolsets.py` fixes.
- Reinforces existing notes: **keyword-first, LLM-second paper triage** (deterministic keyword score → LLM only on papers that clear it — sharpens the "Paper-research skill" below; [ibaaj/arxiv-digest](https://github.com/ibaaj/arxiv-digest)); **trim tool output before the model sees it** (a `truncate_for_agent()` step on verbose tool results — fits the Layer-1-cache wrapper; [zdk/lowfat](https://github.com/zdk/lowfat), HN 48409955).

### From the "where is this going" chats (2026-06-26)
- [ ] **`ko publish --md` mobile nav** — research-notes-to-phone-site is already this command (local md folder → Cloudflare, no Drive). Gap: phone-friendly browse — hamburger menu + a simple tappable list of pages. A scaffold/template tweak, not a new integration. (Drive API enabled 2026-06-26, but this use case doesn't use it.)
  - **Free backup/phone trick:** point the md folder at a **Drive-for-Desktop synced folder** — it's just a local folder to ko (no Drive API), but Drive gives free backup + phone access (Drive app), and `ko publish --md` deploys it (private folder, PIN-gated public site). Works for a *local* agent only — a remote Sprite agent can't reach it (use git/R2 — see `docs/fleet.md`).
- [ ] **`ko gdocs` review/comment (needs Drive scope)** — "have the agent read my doc and comment on it." Key facts: the Docs API has **no suggested-edits** (direct edits only via batchUpdate — insert/delete/replace/style/tables), and **comments live in the Drive API** (`comments`/`replies`), not Docs. So commenting = the non-destructive review channel (agent leaves comments, I decide) and it requires Drive. Pair with `drive.readonly` + a configured folder id for "read the docs in this folder" (per-folder OAuth grant isn't a thing — scope is per-API; the folder limit is ko's behavior, or use a service-account + folder-share for a true grant).
- ko's MCP = **my curated subset**, deliberately NOT Google's giant official Workspace MCP. The point is the 10 tools I actually use (concise, low-context), not 80 generic ones.

### `ko mcp` verbs + investigation upgrades (decided 2026-06-26, implement after the cli refactor)
Disambiguation rule: **a verb that takes `<url>` acts on someone else's server (client); `serve` (no url) makes ko the server.** Keeps "probe" vs "be a server" unconfusable.
- [ ] **Rename `ko mcp test` → `ko mcp inspect <url>`** — clearer than "test" (the ambiguous word) and matches the ecosystem ("MCP **Inspector**"). Brand new, cheap to rename.
- [ ] **Split `call` out as its own verb** — `ko mcp call <url> <tool> [--arg k=v]` (was the `--call` flag). inspect *reads*, call *invokes*.
- [ ] **`ko mcp serve [--http]`** — run ko's OWN server (the FastMCP task, later). The no-url verb. Tool-design *principles* carry from `everx/docs/mcp-tool-design.md` (few powerful tools, progressive disclosure, low tool count, markdown-to-read vs JSON-to-render, helpful errors) — but the *shapes* don't: that's a CRM (find/detail/deep-dive/bulk over entities); ko's surface is SDK-wrapper verbs, so map the discipline, not the four entity tools.
- [ ] **`inspect` = all surfaces, not just tools.** Use the raw `ClientSession` to also `list_resources` (+ `list_resource_templates`) and `list_prompts`, shown only if advertised. Surface the full `initialize` response — protocol, capabilities, and the server's **`instructions`** field. `--tool <name>` dumps one tool's full JSON Schema. Keep the raw-503 error fallback (the best part). Optional: `ko mcp ping <url>` via `session.send_ping()` for liveness+timing.

### `ko mcp ui` — launch the official Inspector, pre-configured (2026-06-27)
- [ ] **`ko mcp ui <name|url>`** — shell out to `npx @modelcontextprotocol/inspector` with the server's transport + URL + headers resolved from `mcp.json` (same pattern as `ko publish preview` → `wrangler dev`). The official Inspector is excellent at the *interactive* path — OAuth click-through, step-by-step — which ko's CLI deliberately doesn't do. So **complement, don't replace**: ko CLI = fast/scriptable/agent + the LLM `overview`; `ko mcp ui` = hand off to the GUI for OAuth/visual debugging. npx/node is an acceptable optional dep (already used for wrangler). Confirm the inspector's pre-fill flags when building. Note (2026-06-27): the Inspector's "Failed to start OAuth flow: Unexpected token '<'" is usually a **stale npx-cached inspector** (OAuth flow churns across versions) — `npx ...@latest`; the server is fine if its `.well-known` metadata + DCR return JSON.

### MCP registry → agent + bundled servers (2026-06-27)
- [ ] **`ko ai` consumes mcp.json servers.** pydantic-ai v2 has `pydantic_ai.mcp` (`MCPServerStreamableHTTP`/`MCPServerStdio`/`MCPServerSSE`). Build the toolset list from `mcp_client.load_servers()` — map each spec to the matching class — and pass to `Agent(toolsets=[...])`. Needs the `pydantic-ai-slim[mcp]` extra (pulls fastmcp; add at agent time). The registry we shipped is the foundation.
- [ ] **Bundled default servers**, `ko prompt` pattern: a curated set shipped in the repo + `~/.config/ko/mcp.json` override. Secrets via **`${ENV_VAR}`** placeholders (expansion already in `load_servers`), so the checked-in file holds no credentials. Candidates: **railway**, **context7** (docs). Skip **exa** — we have the native lib/tool, no MCP needed. Only bundle servers that earn it.
  - **Decided 2026-06-27: don't bake localhost dev ports into the checked-in defaults** (sveltekit `:5180/mcp`, FastMCP default `:8000/mcp`). They're personal + ephemeral — pass ad-hoc (`ko mcp overview http://localhost:5180/mcp`) or alias in personal `~/.config/ko/mcp.json`. Checked-in = universal servers only.
  - **No enable/disable for the CLI** (name one server per command). Enable/disable is a *run-time server selection* for the agent (`ko ai` picks which servers to attach per run), not persistent state.
  - **Validated:** pydantic-ai v2's `load_mcp_toolsets(path)` reads our exact `mcpServers` shape (+ `${VAR}`/`${VAR:-default}`). So one mcp.json serves the CLI registry, `ko mcp overview`, AND the future `ko ai` agent.
- [ ] **`ko billing` — more providers.** v1 is OpenRouter (`/api/v1/credits` balance + `/key` usage). Add a provider = a function returning `Balance`. Check which others expose a simple balance/usage endpoint (Anthropic/OpenAI usage APIs are org/admin-key gated; OpenRouter is the easy aggregate win since it fronts many models). Exa/TMDB/X likely have no public balance endpoint — confirm before adding.

### Infra note
- **Progressive disclosure for `ko mcp`** — when the MCP server lands, expose **one** `ko` tool taking an argv array (agent calls `--help`, drills in, executes) rather than one MCP tool per subcommand. Measured ~91% token cut vs flat tool lists as the CLI grows. [solo.io/blog/keeping-context-and-tokens-low-with-progressive-disclosure-in-agentgateway](https://www.solo.io/blog/keeping-context-and-tokens-low-with-progressive-disclosure-in-agentgateway)

## `ko ai` — the agent layer (design sketch 2026-06-11, updated 2026-06-27: + MCP toolsets, telemetry)

ko grows two explicit layers:

- **Layer 1 — deterministic plumbing.** `ko exa`, `ko fetch`, `ko yt`, `ko hn`… No LLM ever. Pipeable, cheap, safe for agents to call blind.
- **Layer 2 — `ko ai "<prompt>"`.** A pydantic-ai agent whose tools *are* the Layer 1 module functions. Every new subcommand = a new agent tool for free, because `cli.py` and the agent wrap the same functions (same trick as the MCP plan). Absorbs/renames today's `ko agent`.

Decisions:

- **Explicit `ko ai`, not bare `ko "<prompt>"`** — a typo'd subcommand must never silently spend tokens, and agents calling Layer 1 need certainty it stays deterministic. Intelligence is opt-in.
- **Skills as markdown recipes**, agentskills format. Editable text, not code — same pattern as Claude skills, applied to my own CLI.
- **Skills folder strategy (decided 2026-06-12): curated manual drop-in, never an installer.** Repo `skills/` holds the curated set — ours (`ko-tools/`) plus world skills manually copied *after reading them* (skills are prompt-injection vectors; reading-before-adopting is the security model, and git gives provenance + diffable updates). Convention: dropped-in skills get a `source:` URL + date in frontmatter metadata. Optional overlay: `~/.config/ko/skills/` merged at load for private/experimental skills, same loader.
- **Agent declarations as data**: `agents/<name>.toml` — `model`, `system`, `toolsets = ["exa", "hf", …]` (names → module `FunctionToolset` registry), `skills = ["ko-tools", …]` (names → skill-loader capabilities). One ~15-line assembly function builds the `Agent`; `ko ai -a researcher "…"`, default agent when no flag. Deliberately NOT pydantic's `Agent.from_file` YAML — audited: it can't reference custom toolsets/function capabilities, ours must.
- **The hn skill, spec'd (2026-06-12).** `ko ai hn gemini` = everything after `ai` goes to the agent; `hn` matches the skill, `gemini` is the topic. Baked judgment: last ~2 weeks, sorted by time, **drop anything under ~50 comments** (the "actually worth reading" filter), read top ≤5 stories — url content via the free in-house fetcher (`ko fetch`/trafilatura, NOT exa — no paid calls for this) + comment trees via `hn item`. Output contract (terminal): half-page overview — per story the HN link, a line or two on what it says, and interesting comment highlights. `--short` = a few lines + link refs only. Default model for skills like this: cheap tier (gemini-flash-class via OpenRouter), not the smart default.
- **Two callers, two verbosities.** I call `ko ai` from the terminal (half-page default, `--short` available); my *outer* agents (Claude Code) also call it from bash instead of re-deriving the pipeline themselves — they pass `--long` for an expanded summary they can digest. Flags, not caller-detection.
- **Build-order implication:** the hn skill needs the free URL→markdown fetcher, so `ko fetch` (already fully specified above) unblocks `ko ai` v1.
- **SQLite, not DuckDB, at `~/.local/share/ko/ko.db`** (considered 2026-06-11): the db's jobs are transactional KV (conversation blobs, fetch cache) — SQLite's home turf, stdlib, zero deps. DuckDB is columnar/analytical, weaker as a KV store. No loss: `ko q` brings DuckDB anyway, and DuckDB ATTACHes SQLite files directly — analytics over ko.db ("what do I fetch most?") via `ko q` for free. Two tables:
  - `conversations`: pydantic-ai message histories (JSON-serializable natively) → `ko ai --continue`/`--resume` for free.
  - `fetches`: (url, ts, markdown) — doubles as the **Layer 1 cache**: repeat `ko fetch` = instant + free (agents re-fetch constantly). `--no-cache` to bypass.
  - Boundary: cache + history only. Knowledge store / saved links belongs to a separate app — don't rebuild that inside ko.
- **Shared memory store (`ko memory`) — full plan in `docs/memory.md`** (explored 2026-06-27, basic version later). Own, cross-agent memory: libSQL+vector single file (Turso embedded-replica = local-cheap + central-synced), deterministic `ko memory search/add` exposed as an MCP tool, populated overnight by a session-distiller `ko ai` skill (ADD-only, small model, typed facts + decay, ingest-only-from-my-own-work). Reuses the MCP registry + `ko ai` + the log-projection rule — the most "ko" idea in here.
- **"The log is the agent" — one rule, basic version later (notes 2026-06-27, from Omnara/Sierra talks).** Don't over-build this for a personal CLI. The single durable principle: **the raw `all_messages` stream is the immutable source of truth; everything readable is a *projection* of it** — the clean **Q&A view** (the actually-interesting log; the raw tool-call back-and-forth is just plumbing), the model's compacted context, cost, `ko logs`. So: **never overwrite raw with a compaction** — compact into a throwaway fork, keep raw intact. That's it for now; resumability/replay/"stateless workers" come free off this later if we want them. The full version ("log is the agent" as live infra) only matters at the **fleet** layer (durable log = agent, box disposable — see `remote.md`). Validated: ko's one-agent + progressive-disclosure (`ko prompt`, `Capability(defer_loading)`) is the restraint both talks preach. Skip: monitor→authoring→deploy flywheel (enterprise; the personal version is a future "agent reviews my own logs" skill), PCI-style isolation (but **be deliberate that `ko brief`/`ko ai` send Gmail/calendar to a cloud LLM** — maybe a local-model option for the most personal synthesis).
- **Paper-research skill** (idea 2026-06-12, from HF's internal "context-research" pattern): Layer 1 stays dumb — `ko hf search` is one search, full stop. The pipeline lives in a `ko ai` skill: take the question → generate 2–3 keyword-variant queries → run `hf search` (and `arxiv search`/`pwc search`) in parallel → cheap-model subagent triages by relevance + recency → `hf get`/`arxiv fetch` the top few → read methodology/results → synthesize. Relevant for the research-masters use. Later.
- **Smart routing lives in `ko fetch`, not the agent.** No `ko linktopdf`/`linktoyt`: `ko fetch <anything>` sniffs deterministically — youtube.com/youtu.be → transcript, content-type PDF → pymupdf4llm, else trafilatura, dead link → Wayback. One universal "thing → markdown" command. Smart ≠ fuzzy. Plus the bare-link shortcut: `ko <url>` (top-level URL detection) routes straight to fetch.
- **MCP servers as toolsets (validated 2026-06-27).** Beyond the Layer-1 `FunctionToolset`s, the agent can consume *external* MCP servers as tools via pydantic-ai's `load_mcp_toolsets("~/.config/ko/mcp.json")` — the **same registry the CLI uses** (we confirmed it reads our exact `mcpServers` shape + `${VAR}` expansion). Server selection is **run-time** (pass which servers to attach per run), not a persistent enable/disable. Needs the `[mcp]` extra (already added). So `ko ai` gets context7/railway/etc. as tools for free once the registry has them.
- **Telemetry built in (decided 2026-06-27).** `Agent.instrument_all(InstrumentationSettings(tracer_provider=<ours>, include_content=<bool>))` in the agent bootstrap → OTel `gen_ai.*` spans + token/cost metrics fan out to a **local exporter** (file/SQLite for analysis) **and PostHog OTLP** at once. Instrumenting here (not OpenRouter Broadcast) is **provider-agnostic** — captures DeepSeek-direct / Gemini-direct / OR alike, so it survives moving agents off OpenRouter to cheaper APIs. `include_content` is the prompt/response scrub toggle. Full comparison: Infra (b).

### Implementation notes (research 2026-06-11: pydantic.dev docs + pydantic-ai source + private prior art)

**On pydantic-ai v2.0.0b7 as of 2026-06-12** (was 1.87; switched before building anything — v2 is harness-first with `capabilities=[...]` as the core primitive, exactly our skills plan). Verified on upgrade: research agent + all tests pass untouched; clai internals we read are v2-current (diff vs beta: 10 trivial lines); `Capability` imports from `pydantic_ai.capabilities` (not top-level); `known_model_names()` exists (480 qualified names — the `ko llm` completion source, no fallback needed); `openrouter:`/`anthropic:` both resolve with the default extras. Gotchas: pin the exact beta (`==2.0.0b7`, bump deliberately; stable v2 imminent — un-pin then); `[tool.uv] prerelease = "allow"` needed for the slim sub-package; **`openai:` now means the Responses API** (`openai-chat:` for Chat Completions). Upgrade guide: pydantic.dev/docs/ai/changelog/.

The skills system is ~zero custom code — pydantic-ai 2026 ships the pieces:

- **Skills = `Capability` with `defer_loading=True`.** Each skill collapses to a one-line catalog entry (id + description) until the model calls `load_capability(id)` — context stays lean no matter how many skills exist. **v2 correction (source-audited 2026-06-12): no `from_file`/markdown helper on Capability** — we write the ~3-line wrapper ourselves (`Capability(id=, description=frontmatter, instructions=md_body, defer_loading=True)`). Full v2 API facts: `docs/pydantic-ai.md`.
- **Tool grouping: one `FunctionToolset` per module** (`exa`, `arxiv`, `fetch`, `yt`, `hn`), each carrying its own `instructions=`. Per-skill allowed-tools = `FilteredToolset` or per-run `toolsets=[...]` (run-time injection is supported).
- **REPL: compose from `clai`'s pieces, don't call `run_chat()` whole** (read the source 2026-06-12, `pydantic_ai/_cli/__init__.py`, 482 lines). `run_chat()` accepts `message_history=` but only *returns an exit code* — final messages are never exposed, so per-exchange SQLite persistence is impossible through it. Instead write our own ~30-line loop that imports the real workhorses: `ask_agent()` (streams via `agent.iter` + rich.Live, **returns `all_messages()`** — persist after each exchange), `handle_slash_command()` (delegate, then add our own `/model` — in v2 `agent.model` is **read-only**, so keep the choice in a loop variable and pass `model=` per run; history carries over regardless), `CustomAutoSuggest`, and the markdown rendering setup (`SimpleCodeBlock` kills the copy-paste-hostile background). Pass `config_dir=~/.config/ko` for the prompt-history file. Caveat: `pydantic_ai._cli` is a private module — pin the version, acceptable for a personal tool. Bonuses found: `known_model_names()` (feeds typer `-m` autocompletion), `load_agent()` (module:var or YAML via `Agent.from_file`), and `clai web` (browser chat UI for any agent, repeatable `-m` — a free `ko ai web` someday).
- **Persistence (blessed API):** `ModelMessagesTypeAdapter.dump_json(messages)` / `.validate_json(blob)`; store one blob per exchange keyed by session (canonical example: `examples/chat_app.py`). Built-in `conversation_id` threads runs → `ko ai --continue`. `ReinjectSystemPrompt()` capability handles resumed sessions. **Record `pydantic_ai_version` per conversation** — old versions can't read newer transcripts (learned the hard way in a prior pipeline).
- **Steal from a private pydantic-ai pipeline of mine:** `UsageLimits(request_limit=N)` runaway cap; cost-per-run via `ModelResponse.cost().total_price` (genai-prices, already in our tree); retrying httpx transport (`AsyncTenacityTransport` + `wait_retry_after`); *the runner writes outputs, the agent never does*.
- **From pydantic-deepagents: skip the framework** (autonomous subagent teams — overkill). Steal two guards eventually: stuck-loop detection (identical repeated tool calls → break) and large-tool-output eviction.
- **Streaming:** simple path is 3 lines (`rich.Live` + `result.stream_output()` → `Markdown`); upgrade to `agent.iter()` only when we want "calling exa_search…" progress lines.
- **Chat UX, mined from simonw/llm source (2026-06-12):** steal — `!multi`/`!edit` REPL commands (multi-line input + $EDITOR escape); a user `aliases.json` + default-model file (maps cleanly onto pydantic-ai model strings; nicer than env-only); its SQLite shape (`conversations` + `responses` rows with model, tokens, duration → our two-table plan is validated, add those columns); YAML templates with `string.Template` vars (≈ our skills files); reasoning-vs-text stream split (thinking → stderr dim, text → stdout). Skip — `keys.json` store (env vars + pydantic-ai suffice), fragments (until prompt reuse hurts), pluggy plugins (single-owner tool). Notably `llm chat` has **no mid-chat model switch** — our `/model` slash command in `run_chat()` would be a genuine improvement, and it's cheap: pydantic-ai takes `model=` per run, history carries over. Shell completion: typer's `autocompletion=` callback on `-m` over a curated favourites list.

## Per-tool docs: knowledge base vs skills (decided 2026-06-12)

Two separate artifacts per tool — don't conflate them:

- **`docs/<tool>.md` — internal knowledge base / dev & build guide.** For *us when building*: why we chose this library/API, what it's good for, the 2–3 alternatives considered and why they lost, pricing, experiments and findings ("arxiv API is slow, 429s under load — that's why the test is opt-in"), links to official docs/specs/skill files. Free-form, honest, opinionated. Pick a tool back up a year later and this is the context.
- **`skills/ko-tools/` — ONE skill for the whole CLI** (decided 2026-06-12; not one per tool — that would be the design smell). [agentskills.io](https://agentskills.io) standard: `SKILL.md` = the always-loaded summary (what ko is, when to reach for which subcommand, key examples, cost awareness), plus bundled recipe files the standard supports for progressive disclosure — e.g. `recipes/research-tech-news.md`: "1) `ko hn top --n 20` 2) `ko hn search <topic> --min-comments 50` 3) `ko hf top` 4) fetch the interesting urls 5) pipe everything into `ko llm 'summarize…'`" — five-six plain CLI steps ending in a cheap-model collate, executed by *any* outer agent. Consumed two ways: Claude Code loads the standard directly; `ko ai` wraps the same files as pydantic-ai `Capability`s (no native helper in v2 — our own ~3-line wrapper, see `docs/pydantic-ai.md`). `--help` stays the first-line contract.

## Infra

PostHog = the durable backend (logs survive crashes / reinstalls / new machines). **Two separate
integrations — don't conflate them:**

- [ ] **(a) Command logs → PostHog, auto-enabled on env var.** Local logging shipped 2026-06-27 via
  loguru (`logs.py`: one wide event per command, scalar-only, no secrets). The sync is just a second
  **`logger.add(posthog_sink)`** — `logs.setup()` adds it when **`POSTHOG_API_KEY`** (+ `POSTHOG_HOST`,
  default `https://us.i.posthog.com`) is set. The sink forwards each loguru `record` → PostHog
  **capture/batch API** (`POST /capture`) as a `ko_command` event (props: cmd, duration_ms, exit_code,
  error, ko_version), `distinct_id` = a stable anon machine id. Buffer/flush since it's low-volume.
  Easy because it's loguru. Ref: posthog.com/docs/logs/best-practices.
- [ ] **(b) LLM/agent traces → PostHog AI Observability (OTel) — separate product.** PostHog ingests
  OpenTelemetry **`gen_ai.*`** spans → `$ai_generation` events with model, tokens, **cost USD**, latency,
  prompts, responses, tools; multi-step traces reconstruct via `$ai_trace_id`/`$ai_span_id`/`$ai_parent_id`.
  PostHog OTLP endpoint: **`https://us.i.posthog.com/i/v0/ai/otel`** (`Authorization: Bearer <project_token>`).
  Two homes — instrument at the **pydantic-ai layer** is preferred (provider-agnostic + local+cloud + control):
  - **pydantic-ai `InstrumentationSettings` (PREFERRED).** `Agent.instrument_all(InstrumentationSettings(
    tracer_provider=<ours>, include_content=<bool>))` in ko's agent bootstrap covers `ko llm`/`agent`/`ai`.
    Why it wins (matches the multi-provider future): (1) **provider-agnostic** — wraps the model call *inside*
    pydantic-ai, so it captures DeepSeek-direct/Gemini-direct/OR alike (OR Broadcast can't see non-OR calls;
    DeepSeek-direct is a stated goal for cheap agents); (2) **one `tracer_provider`, N exporters** → save
    **locally** (file/SQLite for analysis) *and* to PostHog (OTLP `/i/v0/ai/otel`) at once; (3) **`include_content`**
    natively toggles prompt/completion capture — the scrub PostHog's path lacks. Emits `gen_ai.*` spans +
    token/cost histograms. Cost: ko writes the TracerProvider+exporter setup + an OTel-sdk dep (pydantic-ai
    `logfire` extra bundles OTel). Code: refs/pydantic-ai `models/instrumented.py`.
  - **OpenRouter Broadcast → PostHog (zero ko code, fallback).** OR's native OTel sink (Settings →
    Observability → Broadcast → OTLP + headers; Privacy Mode scrubs content). Server-side, but **OR-routed
    calls only** — disqualified the moment a DeepSeek-direct/Gemini-direct agent exists. Path detail:
    OR posts OTLP `/v1/traces`-style; PostHog wants `/i/v0/ai/otel` (maybe a Collector between).
    Ref: openrouter.ai/docs/guides/features/broadcast/otel-collector.
  Refs: posthog.com/docs/llm-analytics/installation/opentelemetry, /llm-analytics/start-here.
  **Not building yet — research done 2026-06-27.**
- [x] PyPI trusted publisher + tag-push GitHub Action — SHIPPED 2026-07-14 (`.github/workflows/publish.yml`: tag push → `uv build` → `uv publish` via OIDC, environment `pypi`; no tokens).
- [ ] **`ko costs` — local LLM-spend totals** (considered 2026-07-14). What exists: per-run cost
  notes to stderr (`llm.run_cost`, actual-or-estimate), `llm.last_cost` global, full traces in
  session files, `ko billing` = OpenRouter-side today/week/month (misses direct-key `google:`/
  `openai:` calls, no per-command split). Build: (1) attach `last_cost` to the wide event in
  `command_event` (usd, tokens, model, cost_source — scalar binds, ~5 lines); (2) `ko costs`
  aggregates the logs JSONL: today/week/month by cmd + model, `~` marks estimates (retention
  caps it at a rolling month — `ko billing` stays the long-horizon truth); (3) doctor footer
  gets one summary line from the same aggregation.
- [ ] **Tier fallback on 402** — when the OpenRouter pool runs dry, *every* tier fails together (llm default, `ko ai` medium, research smart) since all four route through one prepaid key; observed 2026-07-14 — exercising `ko ai` needed a manual `-m google:gemini-3-flash-preview`. Idea: optional `[llm] fallback` (a direct-key model, e.g. `google:gemini-3-flash-preview`) tried once when an `openrouter:` tier model 402s, with a loud stderr note — keeps the one-pool default, degrades to "still works" when credits run out.
- [ ] MCP server exposing the same modules (`mcp_server.py` stub has the wiring sketch). CLI for humans + bash; MCP for native agent calls. **Use FastMCP** ([gofastmcp.com](https://gofastmcp.com)) — it does **stdio** (local: Claude Code/Desktop on the Mac) *and* **HTTP** (remote: a server on the home box that the laptop connects to) from one definition. Progressive disclosure for the tool surface — one argv-style `ko` tool, not one per subcommand (see Research scan 2026-06-26). Home-server hosting ties in below.
  - **Consolidation note:** `ko mcp test` currently uses the raw `mcp` SDK (already a dep) with a hand-rolled raw-POST error fallback — keep it simple. FastMCP ships a high-level **`Client`** (`from fastmcp import Client`: list_tools/call_tool/transports). Once `fastmcp` is a dep anyway (for this server, or for pydantic-ai's `[mcp]` agent toolset), `ko mcp test` can ride on `fastmcp.Client` and shed code. Don't add `fastmcp` just for the probe.
  - **Decision (2026-06-26): do NOT swap the client to fastmcp now.** You can't "remove" `mcp` — fastmcp is built on it (stays transitively). ko's whole mcp footprint is two tiny clients (`ko tt` + the probe); fastmcp is heavier (uvx weight) and **high release cadence** (chase + pin tax) — both reasons to adopt *late*, not early. Trigger = building `ko mcp serve` or the agent's `[mcp]` toolset; then `uv add fastmcp` and migrate ticktick + probe onto `fastmcp.Client` in one pass. Doc index: `gofastmcp.com/llms.txt`.
  - pydantic-ai's MCP **client** (`pydantic_ai.mcp`, needs `pydantic-ai-slim[mcp]` → fastmcp) is the path for a `ko ai` agent to *consume* external MCP servers as a toolset — a different job from the probe; wire it when the agent layer lands.

## Two front-ends on one core (design note, 2026-06-26)

Once `ko ai` exists, ko grows two surfaces over the same Layer-1 tools + agent — don't build either before `ko ai`:

- [ ] **MCP (FastMCP)** — for *agents* to call ko natively. Local stdio + remote HTTP (home server). The "let my main agent use all my tools" pathway.
- [ ] **Telegram bridge** — for *me* to talk to a ko agent from my phone. Thin: long-poll Telegram → run `ko ai` (all tools) → reply. Composes with the brief: `ko brief --notify telegram` on a cron = ambient morning push. The "ambient agent on the home server" idea (research scan: proactive heartbeat). Front-end, not core.
- [ ] **Agent fleet (far layer)** — a small fleet of bursty/dormant specialist agents on Fly.io Sprites, supervised by a `ko` control agent. Full design + reactions/risks in **`docs/fleet.md`**. Key convergence: a per-agent standing brief IS a `ko prompt` brief. Rides on `ko ai`; furthest-out, do not start before it.
- [ ] **`ko note "..."`** — an append-and-review note ([Karpathy's pattern](https://karpathy.bearblog.dev/the-append-and-review-note/)) backed by a single Google Doc (`[note] doc = <id>`). **Prepend** a timestamped block at the TOP (newest-first — needs a prepend variant of `gdocs.append_text`, inserting at body start not end). Plain text / literal markdown — the Docs API `insertText` is plain-text only (it won't *render* `##`), and that's fine for this. The real answer to "push research somewhere before I forget" — supersedes the Google Keep skip below.
  - **Markdown reality (verified 2026-06-26):** true markdown import/export lives in the **Drive API** (`files.export` `text/markdown` / import), added July 2024, works for personal accounts — but needs a Drive scope. Use the narrow non-restricted **`drive.file`** and have `ko note --init` *create* the doc, so the scope covers it; only worth it if you want the note as real round-tripped markdown. ko's `gdocs get --md` already does a lossy structural export for reads.
  - **Review half = a `ko ai` skill (later):** "compress the older sections" — read the doc → LLM summarizes entries older than N days → write back (replace). The automated *review* in append-and-review; the reason the note lives where ko can both read and rewrite it (a Doc, not Keep).

## Skip (considered, not a fit)

- `ko gh` — `gh` already excellent, agents use it natively
- `ko jira` / `ko linear` — volatile APIs, painful auth, low return
- `ko atuin` / `ko zoxide` — shell-integrated, can't wrap from Python
- Tavily / Brave search / Firecrawl / IPinfo / CoinGecko wrappers — official CLIs now exist (checked 2026-06)
- newspaper3k (dead since 2018), every Python HN wrapper (unmaintained), docling-for-HTML
- Big famous tools generally (rg, fzf, jq…) — wrap only API/SDK-shaped things that *lack* a good CLI (the Exa rule)
- **Google Keep** (considered 2026-06-26) — the official API has **no update/append** (create/get/list/delete only), is **Workspace-only** via a **service account + domain-wide delegation** (a different, heavier auth than ko's user-OAuth, and it can't touch a personal account's notes). `gkeepapi` (unofficial, reverse-engineered) works on personal accounts but is fragile + ToS-risky. The actual want ("append my research to a note") is served far better by `ko gdocs append` / a `ko note` wrapper — a Doc is a real append-target, personal+workspace, readable anywhere.
