# WORKLOG — ko

Newest first. Big picture only — git commits have the detail; candidate ideas and decisions live in `docs/ideas.md`.

## 2026-07-10/11 — money layer, refs, repo agent, memory (post-release arc)

- **First release cut**: v2026.7.9 tagged + GitHub release via the new `/release` skill
  (everx pattern + Keep a Changelog 2.0.0; CHANGELOG.md canonical). PyPI deferred until
  the trusted publisher is registered (user).
- **Money-first LLM layer**: `[llm] basic/medium/smart/ultra` tiers (deepseek-v4-flash /
  glm-5.2 / `~x-ai/grok-latest` / gpt-5.6-sol — priced via OR catalog); every LLM call
  prints a stderr cost note using **OpenRouter's actual billed cost** from
  provider_details (genai-prices estimate crashes on brand-new models — actuals aren't
  just better, they're the only reliable path); doctor live-checks the OR key via
  /credits ($ left + trend). Config: `config.setting()` chain, `[telemetry]` opt-in
  (pydantic-ai OTel → PostHog, off by default, GH #2 awaits the key flip), loud
  malformed-config.toml warning, doctor settings/dirs footer.
- **`ko refs`** (pull/setup/add/list — gitupdateall.py port; refs.txt machine list;
  doctor disk footprint, openclaw flagged at 1.9GB) and **`ko agent repo`** — the
  cheap repo-explorer (read-only files toolset confined to ~/code, refs/CLAUDE.md map
  injected per run, ~$0.001/question). Tools aligned to Claude Code priors (glob rename,
  grep limit/context/files_only — the model *invented* the limit param in traces).
- **The toolset eval loop became house method** (CLAUDE.md "Adding an agent" step 4):
  run the agent → subagent reads session traces → fresh adversarial review →
  prior-alignment check. Three passes on the files tools each caught what the others
  couldn't (10MB single-line read, broken-symlink crash, invented params).
- **Per-agent markdown memory** (docs/memory.md "v1 first", researched vs
  thinker/deepagents/hermes): `~/.local/state/ko/memory/<agent>/` workspaces
  (memory.md anchor + free-form notes; append/uniqueness-edit tools; pin marker) +
  shared `~/.config/ko/memory.md`. Live-verified cross-run recall ($0.0003). Fable
  review caught an APFS case bypass that clobbered the anchor + a subdir crash — both
  fixed with regressions. 309 tests passing (was 180 at the week's start).

## 2026-07-09 — strategic review sweep + `ko brief`/`ko yt`/sessions index

- **Full hands-on review** (4 Sonnet testers over every cluster + a researcher dogfooding ko + a Fable
  feedback pass). The tool held up; the fixes below came out of real invocations, not code reading.
- **Agent toolsets stop starving the library** (external feedback, verified + applied): `hn_top` +
  `hn_front_page` (new `hn.front_page()` via Algolia's `front_page` tag; CLI `ko hn top --now`),
  `hn_search` widened (since_months/by_date/min_comments), `exa_search` widened (domain/date filters).
  **Docstring-as-contract audit**: every toolset docstring now states its silent defaults (hn's 12-month
  window, exa's ~3000-char text cap, hf's coverage gap, papers_refs' 50-ref cap, arxiv's relevance sort) —
  an agent must never mistake a windowed empty result for "doesn't exist". `news` instructions rewritten.
- **Agent-contract fixes** (the 2026-07-03 pass, finished): Google `HttpError` net in `_google_errors`
  (gmail view/thread tracebacked on a bad id; raw `<HttpError …url…>` blobs leaked into --json errors) +
  applied to all gmail/cal/gsheets-write commands; `exa get` exits 1 when ALL urls fail; clean errors for
  `hn item`/`x user` bogus ids (raw httpx/requests leaks); `papers search ""` = usage error; `papers cites`
  validates W-ids (typo'd id looked like zero citations); `x lists --json` biggest-first; `hn search`
  no-results names the 12-month window; `ko logs` error field now captures `_die`'s code label (was always
  null); mcp inspect says "(HTML page, not an MCP endpoint?)" instead of quoting HTML; doctor lists
  gdocs/cal/gmail + mcp + billing (needs column folds, not truncates); cal clamps ongoing multi-day events
  to the queried window + flattens multi-line locations; `cal find --calendar`; gmail strips invisible
  ESP padding chars; Post/Story urls included in --json (@property ≠ asdict).
- **`ko yt <url|id>`** — youtube-transcript-api (free, keyless; manual subs preferred over auto);
  `--json` snippets, `-s` LLM summary; `ko <youtube-url>` now routes to the transcript (fails loud on
  no captions — the description page was a trap). Gemini video fallback deferred until it bites.
- **`ko brief`** — deterministic gather (cal + unread gmail + hn top + hf top, each best-effort) → one
  cheap-model synthesis; `--raw` skips the LLM. A scripted pipeline, NOT an agent loop (per ideas.md).
- **`ko agent sessions summarize`** — SQLite index at `~/.local/state/ko/ko.db` (rebuildable projection;
  JSON files stay source of truth): cheap-model structured output (title/takeaway/tags) over a
  **deterministic digest** — Q&A verbatim, tool calls compressed to one `[tool: name(args…) → return…]`
  line each, trim order never drops the first prompt or final answer. `sessions --tag/--search` filter.
- Research scan: `ko pwc` still wait-for-API-stability; publish already on Workers Static Assets
  (deprecation scare was a false alarm); agent-self-critique idea banked in ideas.md (build when
  summarize-review loop exists — which sessions summarize now starts). 240 tests passing (was 180).
- **Release machinery** (ported from everx-crm's `/release` skill, adapted): `.claude/skills/release/`
  + `CHANGELOG.md` (Keep a Changelog, canonical; GitHub release derived). CalVer **PEP 440-normalized**
  (`2026.7.9`, tag `v2026.7.9`) — diverges from everx's zero-padded form so tag == PyPI version ==
  `ko --version`. WORKLOG stays the dev journal; CHANGELOG is release-grained facts. PyPI publish
  slots in when the trusted-publisher action lands (tag push becomes the trigger, skill unchanged).

## 2026-07-06 — `ko prompt opencode` + skills investigation

- Added **`ko prompt opencode`** — driving OpenCode non-interactively (`opencode run "msg" -m provider/model
  -f file`, message-first) for second opinions, with a cheap/medium/hard model-tier table (grounded in the
  `/opencode` skill's model catalog + the CLI docs). Library now 8 briefs.
- Investigated adding a **`ko skill install`** tool (three Sonnet miners over active repos + a Fable review).
  **Verdict: don't build it.** The `ko prompt` parallel breaks — prompts are portable *opinions* (rightly
  centralized here); the high-value skills mined were *procedures bound to a specific codebase* (thinker's
  PIT invariants, nibbleedge's `ne` loop, ops' WORKLOG format). Centralizing + copying them creates two
  sources of truth that drift. So: **repo-scoped skills live in each repo's own `.claude/skills/` (under its
  git); global ones go straight to `~/.claude/skills/` (dotfile-synced). No distribution tooling.** If `ko
  skill` ever earns its keep it's as `ko skill new` (a frontmatter/description scaffolder — description
  quality was the miners' recurring failure), not an installer. Skill candidates + build order noted in docs/ideas.md.

## 2026-07-06 — four new `ko prompt` briefs

- Assessed the prompt library (Sonnet subagent mined ~25 project CLAUDE.md files) and added the
  four highest-recurrence "how I build X" gaps, each drafted by a subagent grounded in the real
  reference repos (no invented specifics — one hallucinated dep, `pydantic-ai-harness`, caught + fixed):
  - **`pydantic-ai-agent`** — toolsets/output-enums/sessions/sandbox (kotools agents + jabberwocky/sift).
    Fills the `building-pydantic-ai-agents` skill that thinker's CLAUDE.md references but never existed.
  - **`python-cli`** — Typer layout, output contract, config/dirs split, `_route` (kotools + `ne`).
  - **`sveltekit-embedded-ai`** — Vercel AI SDK v6, ToolLoopAgent, skills/scripts, croner (everx + chota-bot).
  - **`local-first-python-app`** — FastAPI + vendored htmx + LanceDB + PIT guards (thinker + jabberwocky).
  - Library now: 7 briefs. `ko prompt <name>` to load one into an agent.

## 2026-07-05 — `ko x` lists fixed (was fully broken) + read any public list

- Audit (Sonnet subagent) found `ko x lists` / `ko x <name>` were **dead on arrival** — three
  stacked bugs, all "the XDK returns untyped `data` as plain dicts, code used attribute access":
  1. `_user_id`: `resp.data.id` on a dict → crash (killed every list/name path).
  2. `my_lists`: `lst.id`/`lst.name` on dict items → crash.
  3. `my_lists`: a `followed_lists` **403** (gated separately by tier) killed the whole call →
     now per-endpoint resilient (degrades to `owned_lists`).
  All three routed through the existing `_field()` dict/object helper.
- **Read any public list by id/URL**: new `_resolve_list` accepts a bare list id or an
  `x.com/i/lists/<id>` URL (no owned/followed lookup) as well as a name — so `ko x list
  204975651`, `ko x <that-url>`, and `ko x ai` all work.
- **New `ko x user <@handle|url>`** — one user's timeline (goes back further than search's ~7-day
  window); `_parse_handle` accepts @name / bare / profile URL. **`ko x lists`** now shows each list's
  **description + member_count, biggest first** (via `list_fields`).
- **Search scoping + full-archive**: `ko x search --list <name|id|url>` scopes to a list (adds the
  `list:<id>` operator), and `--days >7` switches from the 7-day recent index to **full-archive**
  search (`search/all`, years back — both work on the current tier). So `ko x search "claude code"
  --list ai --days 90` works (verified). `from:<handle>` operator also scopes to one account.
- **Likes / bookmarks / home timeline / `get_me`: not available** — those need OAuth *user-context*
  (UserToken), not the app-only bearer (verified: `get_me` 403s, so the token can't self-identify).
  Smart default added: my handle resolves KO_X_HANDLE → **`[x] handle` in config.toml** → 'ko', so
  it's noted once (dotfile-syncable) rather than hardcoded. An OAuth login flow (deferred) would
  unlock all of the above — see docs/ideas.md.
- Post-review nits fixed: `my_lists` now only swallows a *followed_lists* failure (an owned_lists
  error surfaces); full-archive search notes when `--top` is ignored.
- **Cost model documented** (X went **pay-per-use** 2026-02: prepaid credits, ~$0.005/post read,
  $0.001 owned reads, 2M/mo cap) — in the module docstring, `_client` error, and CLAUDE.md, so a
  big `--n`, full-archive search, or tight loop is an informed choice. No tier upgrade needed for
  any of this. +4 offline tests. 180 passing.

## 2026-07-05 — paper-tool audit + research brief

- **Fixed a real `ko arxiv search` bug**: default was `sort=SubmittedDate`, which made the arxiv
  API return newest-overall and effectively *ignore the query* — a topical search came back junk
  (verified: "transformers" returned a probability-theory paper). Now **relevance-ranked by default**;
  `--recent` opts into newest-first for browsing a `cat:cs.LG`. `since_months` is a hard filter in
  relevance mode, an early-stop in recent mode. Added a `client_results` seam + 3 offline tests.
- **`ko papers` robustness**: retry/backoff on OpenAlex 429/5xx (hit live 503s twice while testing —
  they're frequent). Flagged the arxiv-orphan caveat in `cites` help + the agent tool (famous preprints
  merged into a published record lose their arXiv DOI → use the journal DOI or search→W-id).
- **Research agent upgraded**: added `papers_refs` + `papers_get` to the papers toolset (backward
  snowball + read-any-DOI / verify-a-citation), and rewrote the agent instructions to work like a
  researcher — seed → snowball cites/refs → triangulate ≥2 tools → judge by code+replication not
  citation count. Paired with a new **`ko prompt research-papers`** brief (ML/AI lens; built from a
  web scan of 2026 research practice — OpenAlex/S2/arXiv are the free composable APIs ko already wraps;
  Papers with Code is dead since 2024). 176 tests passing.

## 2026-07-03 (later) — deps + Fable-review fixes

- **Dep upgrade**: pydantic-ai-slim/pydantic-graph 2.0.0b7 → **2.4.0 stable** (dropped the beta-pin block + explicit pydantic-graph pin — transitive-only); exa-py 2.14→2.16, liteparse 2.1→2.4, openai/google-genai/mcp/typer bumps. Migrated `exa.py` off the deprecated `search_and_contents()` → `search(contents=…)`.
- **httpx2 decision: WAIT** (not migrating). pydantic-ai main (v2.4.0) still pins `httpx>=0.27`; a full migration lives only on the unmerged `origin/use-httpx2` branch. Migrating now would split httpx/httpx2 exception types across our `except` boundaries. Watch for that branch merging + `httpx2` in `pydantic_ai_slim/pyproject.toml`.
- **Fable multi-agent review → top findings fixed**:
  - **Agent contract honored**: wrapped the bare command bodies (`fetch`, `doc`, `llm`, `agent research/tv`, `tt`, `gsheets info/get`) in catch→`_die` so they emit a clean stderr line + right exit code instead of a raw traceback. `_die` now maps `code="usage"`→exit 2 (AGENTS.md). Shared `_google_errors` net catches GoogleError/AuthError/stray HttpError (400/429/5xx).
  - **Security**: `ko mcp servers --json` now redacts auth/token/key values (it prints env-expanded config → was leaking live secrets to stdout).
  - **Silent-wrong-answer bugs**: `ko cal` event sort now keys on a parsed aware datetime (was lexicographic on mixed-offset RFC3339 → mis-ordered cross-tz calendars); `parse_when("today"/"tomorrow")` uses the configured zone, not the host's.
  - **Silent truncation surfaced**: `hn item` reports the true comment total (note "showing N of M"); `exa get` notes URLs that returned no content.
  - **Smaller**: token files chmod 0600 (hold refresh tokens); `papers.py` DOI regex `[^\s?#]+` (matches fetch, drops utm junk); `x.py` guards a None `data` on unknown handle; `gdrive` escapes Drive-query name literals; `_try` re-raises programming errors instead of masking them as "source unavailable".
  - **Round 2 (more simple wins)**: `gsheets info/tabs/get` now actually accept a URL (applied `sheet_id()` — help claimed URL support, code didn't); `arxiv.fetch` surfaces arxiv2md's own error instead of a bare CalledProcessError; subprocess timeouts on `npm install` (5 min) + `wrangler deploy` (3 min) so an agent can't hang; atomic writes (temp + `os.replace`) for session files (rewritten every REPL turn) + the OpenRouter model cache; extracted the bare-arg dispatch into a pure, tested `_route()`. **+12 tests total → 172 passing** (incl. the previously-untested `_route`/`_cmd_label` — the privacy-label contract now has assertions).
  - **TSV integrity**: shared `_tsv_cell` sanitizer (tab→`\t`, newline→`\n`) applied to `gsheets get`/`find` output — a multi-line sheet cell no longer splits one logical row across two output lines; exa search titles collapse stray newlines too. +1 test → 173 passing.
  - Deferred (noted, not done): publish cluster (SPA-rewrite on `--md` rename, registry/takeover guards, JSONC regex), the duplicated httpx `_get` helpers, `--json` error shape across the remaining cal/gmail/gdocs commands, `gmail view/thread --json`. **lru_cache account key**: intentionally left — see below (a non-bug for the bash-driven CLI; only matters for in-process account switching).

## 2026-07-03

- **`ko papers` shipped** — cross-publisher literature scouting per `docs/papers-cli-design.md`
  (built as designed, same day the design settled):
  - `search|get|cites|refs|similar`; OpenAlex backbone (no key, `mailto` polite pool),
    S2 tldr/similar behind optional `S2_API_KEY` (keyless S2 429s immediately — degrades gracefully).
  - `get` = full text via the OA copy through the existing fetch/liteparse pipeline, else a
    metadata card (authors, journal, tldr, abstract reconstructed from `abstract_inverted_index`).
  - **DOI routing in `ko fetch`**: doi.org URLs resolve OpenAlex's `oa_url` (= Unpaywall data)
    before the publisher landing page — verified live on a Nature DOI that previously returned nothing.
  - Research agent gains `papers_search` + `papers_cites` (citation graph) in the papers toolset.
  - Zero new deps. ⚠️ live finding: some OpenAlex DOI records are bad merges (right abstract,
    wrong title/author — e.g. `10.1002/jemt.20118`); workaround is search-by-title → W-id.
  - **Post-ship (Sonnet-agent review): multi-candidate full-text chain.** `get` now walks
    every OA location (`best_oa_location`/`locations[].pdf_url`, direct PDFs first, landing
    page last) instead of OpenAlex's single `oa_url` pick — so a publisher bot-block (MDPI,
    Nature) falls through to a repository/arXiv/OSTI copy. `Work.full_text_urls` property.
    Considered but deferred per owner: EZproxy deep-link print (no uni login yet), `--scihub`
    opt-in (OA chain covers more than expected first).

## 2026-06-25

- **`ko publish` hardening + scaffold polish** (post-Opus-review on the `--hono` tier):
  - Fixed a real bug — `ko publish <hono-dir> --name foo` rewrote the worker's `wrangler.jsonc`
    with the *static* config, dropping `main`/`./public`/`KO_PIN` (gated site → public, served repo
    root). `deploy()` now routes to the hono config writer when the folder is a worker.
  - `run_worker_first` is now `bool(pin)` — required for a gated site (else `/README.md` bypasses
    the PIN), wasteful for an open one (assets serve from the edge).
  - **PIN rotation** `ko publish --pin new|<digits>`; gate cookie bumped 30→90 days.
  - **Scaffold styling**: static `style.css` now has a base element layer (so plain HTML looks
    good) + `.card`/`.pill` starters; heavy JS split into its own `app.js` module (mount `<div>` +
    `<script type=module>`); grey-text guard in the CLAUDE.md (keep prose ≥ zinc-400). Same
    component-split guidance for `--md`/`--hono`.
  - **Cached data endpoint** in the `--hono` scaffold: `/api/data` caches an upstream API at the
    edge (Cache API, lazy/access-driven, `DATA_TTL`) — no cron, no KV. Keys stay server-side.
  - **`ko publish preview [dir]`** — `wrangler dev` over real http (ES modules + `fetch()` work,
    unlike `file://`); runs the real worker for `--hono`.
  - **`.assetsignore`** for static/md/bare (assets dir is `.`) so the `.wrangler/` preview dir,
    `wrangler.jsonc` and `CLAUDE.md` aren't served at the site root.
  - Docs: Cloudflare setup-for-another-user (token template + DNS perm), preview/`file://` note,
    and `docs/publish-ai.md` capturing the (deferred) "simple `ask(prompt)` on a published site"
    plan — Workers AI (zero key) vs OpenRouter (secret-on-deploy).

## 2026-06-22

- **Renamed `ko-tools` → `kotools`** (folder + PyPI name; command stays `ko`; `kotools` is free on PyPI). Recreated the venv — the folder rename orphaned console-script shebangs.
- **Slimmed to `pydantic-ai-slim[google,openrouter]`** and dropped the global `prerelease = "allow"`: scoped prereleases to just the pydantic-ai v2 beta line (its sibling `pydantic-graph` referenced explicitly), so pydantic/mcp resolve to stable again. Loosened the beta pin to track new betas; upgraded all deps + bumped floors.
- **OpenRouter in `ko llm`** — `openrouter:` models aren't in pydantic-ai's static `known_model_names()`, so we fetch OR's public `/models` (no auth) and cache it (24h TTL) to feed `-m` autocomplete. New `ko models [--refresh]`.
- **`config.toml [keys]`** — API keys as a fallback to env (env wins), injected into the environment at startup; `ko doctor` now shows each key's source (env/config/missing) and lists all tools.
- **Live tests for exa/tv/x** (x behind `KO_LIVE_TESTS`, paid reads). The x live test caught a real bug — the XDK returns dicts not objects; fixed with a dict-or-object accessor. Found `TMDB_READ_ACCESS_TOKEN` isn't exported to subprocesses (config.toml fixes it).
- **Agent layer refactored to toolsets** — tools declared once as `FunctionToolset`s (`agents/_toolsets.py`: web/papers/news/tmdb), agents compose subsets (`research.py`, `tv.py`), shared run/stream/repl in `_shared.py`. v2 research (subagents on the refs): code factories beat YAML agent-spec (specs can't reference custom toolsets); capabilities deferred. Fixed `run_stream_sync` (not a context manager in v2); made tools resilient (one flaky source no longer aborts a run); per-agent models with a working `-m` (passed per-run, not baked in).
- **Sessions** (`sessions.py`) — every agent turn dumped to `~/.local/state/ko/sessions/<id>.json` (full trace via `ModelMessagesTypeAdapter`); `-r <id>` resumes, `ko agent sessions` lists. Flat files, no DB yet.
- Both agents live-verified (research across HF/arxiv/HN; tv on gemini-flash). README + CLAUDE.md updated.

## 2026-06-13

- **Adopted XDG-style dir split** (`src/ko/dirs.py`, researched against httpie/yt-dlp/llm/poetry): `~/.config/ko` = synced config, `~/.local/state/ko` = tokens + id caches (google token + x cache auto-migrate there — avoids llm's everything-in-one-synced-dir anti-pattern), `~/.cache/ko` = disposable. Env overrides on all three.
- **Shipped `ko doctor`** — rich table of every tool: what it does, what it needs (key/binary/auth), current status; plus the count of models `-m` can tab-complete given the keys in env.
- **Shipped `ko fetch`** — the long-designed universal URL → markdown: trafilatura for articles, arxiv links → arxiv2md, PDF links → ~/Downloads + liteparse, Wayback fallback for dead/empty pages. Bare `ko <url>` routes here. All four paths live-verified. (Wayback availability API quirk: rejects percent-encoded url= — query built by hand.) Then added archive.today for the paywall case: dead-link→Wayback, paywall→archive.today-then-Wayback, `--archive-is` to force; best-effort (datacenter IPs hit its CAPTCHA, residential works).
- **Shipped `ko llm`** — stdin-aware one-shot (no tools ever): `ko hn item 123 | ko llm "summarize"`. v2-pattern model-less Agent, default `google:gemini-3.5-flash`, `-m` tab-completes only models whose env key is set. Live-verified. Every Layer-1 command now pipes into analysis.
- **Shipped `ko tv`** — movie/TV quick check (rating, overview, regional watch providers, AU default), ported from my chota-bot TypeScript tool. TMDB v4 bearer token, free tier; providers data is JustWatch via TMDB. Live-verified.

## 2026-06-12

- **Shipped `ko x search|list|lists`** — X via the official XDK (tweepy rejected: unmaintained). `list <name>` reads my lists by name (the daily habit), name→id cached to spare rate limits; `ko x ai` bare shortcut via the main() dispatcher. Home timeline skipped (needs OAuth user-context, not app-only bearer). Live test pending a token in env.
- **Switched to pydantic-ai v2 (2.0.0b7)** before building the agent layer — v2 is harness-first with capabilities as the core primitive, which is exactly the ko ai design. Zero code changes needed (research agent + tests pass as-is); pinned exact beta, un-pin at stable v2.
- Pre-publish cleanup: GPL-3.0-or-later license, private references redacted, live arxiv test now opt-in (`KO_LIVE_TESTS=1`), PyPI urls + classifiers.
- Research day, findings in `docs/ideas.md`: HF papers coverage measured (complement to arxiv, not a mirror); paperswithcode.co turns out to have a full undocumented API (`/openapi.json`) → `ko pwc` candidate; Exa Monitors assessed (runs are pollable — no webhook infra needed for v1). Decided on two per-tool artifacts: `docs/<tool>.md` knowledge base (why this library, alternatives, pricing, experiments) and `skills/<name>/SKILL.md` agent-facing usage skills (agentskills.io format, wrappable as pydantic-ai Capabilities).

## 2026-06-11

- **Shipped `ko hf`** — Hugging Face paper pages: `top` (Daily Papers by upvotes), `search` (semantic), `info` (metadata + linked code/models/datasets), `get` (markdown). No auth. Same ids as arxiv, so it composes with `ko arxiv fetch`.
- **Shipped `ko hn`** — Hacker News via Algolia: `top` (hckrnews-style top-N by points), `search` (12-month default), `item` (comment tree as text). No auth.
- **Shipped `ko doc`** — PDF/Office/image → plain text via liteparse (local, no models), plus the bare-arg shortcut: `ko paper.pdf` routes to `doc`.
- First commits. Package renamed `ko-tools` (PyPI `ko` is squatted), command stays `ko`. Installed editable as a uv tool; fixed `arxiv2md` resolution (uv tool installs don't put dependency scripts on PATH — resolve from our own venv).
- Started `docs/ideas.md` as the single candidate list (backlog moved there from this file).

## 2026-04-24

- Drafted `docs/pydantic-ai.md` — agent plan. Picked pydantic-ai over Claude Agent SDK / smolagents / LangChain: model-agnostic, typed outputs, thin runtime. Sandbox deferred until we add a code-execution tool.

## 2026-04-22

- Scaffolded repo: Python 3.14, `uv`, `typer`, hatchling. Ported `exa` + `arxiv` from a private predecessor CLI; added `gsheets` + `google_auth` (OAuth installed-app flow, readonly scopes, token cached at `~/.config/ko/google_token.json`).
- Domain modules kept transport-agnostic (no printing, no file I/O) so a future MCP server imports them alongside `cli.py`; wiring stub in `src/ko/mcp_server.py`.
- MCP structured-output finding: FastMCP auto-serializes dataclasses to a full JSON schema, but per-field descriptions require pydantic `BaseModel` + `Field(description=...)` — migration deferred until we feel the pain.

## Open

- [ ] **kotools cloud backend (Cloudflare Worker + D1 + R2 on khalido.dev)** — store useful state in the cloud, not just locally: the publish registry (today `~/.local/state/ko/publish.json` → `ko publish list`), session summaries, agent memory. Local stays source-of-truth/cache; cloud is the sync + shared layer agents can read/write. Natural home for the `ko sessions summarize` index (D1 instead of local SQLite). This is also the worker that the publish tool's `--hono`/D1/R2 path would build on.
- [ ] **Cloudflare Sandbox for agent code execution** — <https://developers.cloudflare.com/sandbox/> — a sandboxed place for agents to run throwaway code without local blast radius (the "agent writes + runs a script" need). Pairs with the cloud backend.
- [x] **`ko sessions summarize` → SQLite** — SHIPPED 2026-07-09 as `ko agent sessions summarize` (see that entry). Next iteration: the banked agent-self-critique loop in ideas.md rides on this.
- [ ] **PyPI trusted publisher** — tag-push GitHub Action (`publish.yml`), OIDC, no long-lived tokens: https://docs.pypi.org/trusted-publishers/
- [ ] **MCP server (`ko mcp`)** — shared-core-functions approach: CLI and MCP both wrap the same module functions. stdio for local clients; HTTP on Railway later. Stub + notes in `src/ko/mcp_server.py`.
