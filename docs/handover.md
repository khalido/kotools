# Handover — 2026-11-10

Read CLAUDE.md first, then this.

## Next session: big-picture review (KO, 2026-11-10)

Fable 5.2 is out. Start the next session on it as the **orchestrator**: sweep the whole project, judge it, then direct the work. Give the heavy lifting (reading many files, sweeps, drafts, fixes) to Opus and Sonnet subagents, check what they bring back against the code, and keep the judgment calls in the main thread.

Read first: Anthropic's [Building effective agents](https://www.anthropic.com/research/building-effective-agents) (KO's pick). Use it as the yardstick for `ko ai` and `src/ko/agents/`: simplest pattern that works, workflows vs agents, tool design.

Questions for the sweep:
- **Direction.** Does ko still match its vision (CLAUDE.md: bake in the best library once, 2–3 flags, safe for an agent to call blind)? What's drifted, what's dead weight, what's missing?
- **Sensible.** Is anything over-built, hand-rolled where a library exists, or inconsistent between commands (flags, `--json` shapes, error codes, help text)?
- **Auth and keys.** Google OAuth (`google_auth.py`, one token per account, `_GoogleTyper`), API keys (`config.require_key`, `[keys]` in config.toml), and the two LLM providers (Vercel AI Gateway for everything, OpenRouter only for Jev). Is it simple and in one place? Can a new user add one provider key and have everything work (not yet, `docs/ideas.md` "One provider isn't automatic yet")?
- **Easy to add things.** How much does a new command, agent tool or provider take today? Is CLAUDE.md's "Adding a subcommand" enough, or does it need a scaffold or a shared pattern?

Output: a short ranked list of what to change and why, shown to KO as a diagram or HTML page before any big refactor. Then rewrite this handover clean around that plan (it has grown into a log; most dated entries below can go to WORKLOG).

**2026-11-12: `ko gmail draft --md` embeds local images inline** (WORKLOG). Test draft `r-39674769328854445` ("ko inline image test (delete me)", account kd) is waiting for KO to open in Gmail and delete. Still open from everx's request: `draft update` keeping attachments, and `draft delete`; everx left drafts titled "OLD, delete: …" to clear by hand.

**2026-11-10: front matter skips, never stops.** One parser, `frontmatter.split`, for `ko pdf`, `ko publish` and `ko prompt`. A block that doesn't parse is dropped with a stderr note naming the file and line, and the content still comes back (KO: an opinionated tool should get the job done). The short-lived `--skip-bad-front-matter` flag is gone. Candidate: `fetch._text_title` could now use `split(..., warn=False)` too. `main` is ahead of GitHub and not pushed.

**2026-11-09: code-smell pass, on main** (WORKLOG has what was fixed, what was left and why). New shared helpers: `_die_from`, `_emit_json`, `dirs.write_atomic`; CLAUDE.md's "Adding a subcommand" says when to use them. Left for a later pass: ~15 Google `except` blocks made redundant by `_GoogleTyper`.

**2026-11-07: v2026.11.7 is the first PyPI release since July.** `ko judge` is now `ko decide` (alias kept). Chota replaces yaad, and `ko` stays the toolbox. Next: install on the Pi5 (needs `uv`), then Chota swaps `fetchPage` for `ko fetch` on X links. Then `ko decide --each` (`docs/ideas.md`, "More use of Jev"). OpenRouter serves `ko decide` and is nearly empty ($0.03); pydantic-ai can't reach Jev on the gateway, so moving it waits on them. The re-check and the one-provider gaps are in `docs/ideas.md`.

**2026-11-06: `ko publish` fix pass, on main** (WORKLOG). `ko-test`, `kotools` and `robot-arm` are redeployed. Still local: the PIN patch in `ops/proposals/uts-tmos-robot-arm/src/index.ts` is uncommitted in ops, and `labs/claude-code-workshop` is patched but has no live Worker. `docs/ideas.md` keeps `worker_exists` for `wrangler login` users. Not built, KO's call: `--ttl`, encrypted share links, `Accept: text/markdown`, signed PIN cookies.

**2026-10-27: LLM calls moved to Vercel AI Gateway; `models.py` owns every model** (a `ModelSpec` per tested model: note, host order, settings; tiers basic/medium/smart; `resolve()`). `ko ai` defaults to basic (deepseek-v4.1-flash). New `ko models info <name>` (and agent tool `model_info`): price, hosts' speed, top HN threads with their best comment. HN comments now come in HN's ranked order. WORKLOG 2026-10-27 has the why and measurements. `ko yt -s` moved to the gateway 2026-11-04, default model 3.8 Flash, via the paid gateway. The free-tier Google key path is built but switched off (key commented out in config.toml: the free tier was overloaded on day one); uncomment to retry (WORKLOG).

**2026-11-06: `ko doc` OCR is liteparse + DeepSeek with a spend stop** (over 50 OCR pages or $0.25: exit 2, `--yes`; WORKLOG has the engine test). `-e vision` is the free option; its bad-cache line loss has no known trigger or check. Indra is the free off-Mac candidate, not wired in: fast and strong on paragraph numbers, but it swaps real words more often than Vision. liteparse PR #521 would replace `vision-ocr.swift`.

**2026-11-04: OCR server.** `ko ocr serve` (liteparse's OCR API on 127.0.0.1:8828) and `ko doc -e`: DeepSeek on the gateway by default (thinking off, ~$0.001/page), `vision` (Apple, free), `tesseract`. Built for sfl-mes's book pipeline and pk_legal (WORKLOG).

**2026-10-28:** the six dogfood bugs are fixed (WORKLOG). `ko models info a b c` compares models in one table; new `ko pkg info <name>...` + `pkg_info` agent tool (WORKLOG 2026-10-28). Packages upgraded (pydantic-ai 2.61) and the ko tool env reinstalled.

Next on this thread, in order: (1) `model_info` tool returns clipped comments + links; (2) `ko ai` instructions: Reddit via `exa_search(include_domains=['reddit.com'])`, reviews default to the last year; X search as a tool with a small limit; (3) code mode for `ko ai`/research, measured before and after; (4) per-model settings one at a time (terra reasoning effort, deepseek thinking off). Speed: `hn.top_comment` pulls the whole Algolia tree for one comment + reply; a Firebase chain (kids[0] → item → kids[0]) would be a few small calls, and is the slow part of `ko models info`.

## Where things stand

- **Python 3.15**, deps upgraded (pydantic-ai 2.56 with the `typesafe` extra). All committed; WORKLOG 2026-10-13/14 has the why.
- **The installed `ko` is a uv tool env, which doesn't follow `uv lock`.** It also ignores the 3-day guard and pulls fresh releases, so reinstall against the lock: `uv export --frozen --no-hashes --no-dev --no-emit-project -o /tmp/c.txt && uv tool install --reinstall -e . -c /tmp/c.txt`
- **New this session:**
  - `ko decide` (alias `ko judge`, a decision model): typed answers with probabilities from Jev (yes/no, `--choice`, `--score`, `--tags`) through OpenRouter on the existing key. Guide: `ko prompt decide`.
  - `ko yt`: `info` (batch, transcript excerpt or reason code), `channel` (`--since`, durations), `-s` = Gemini Flash watching the video through OpenRouter with `src/ko/prompts/yt-condense.md`. Transcripts and write-ups cached in `~/.cache/ko/yt/`.
  - Google: one Cloud API key `GOOGLE_CLOUD_API_KEY` (YouTube, Books, Weather enabled; `YOUTUBE_API_KEY` still read) for public data; `youtube.readonly` added to the OAuth sign-in.
- **2026-10-16:** `ko tt` is REST-only (search moved off the MCP); `ko mcp` runs on pydantic-ai's `MCPToolset` and `mcp` is no longer a direct dep (2.3 via pydantic-ai); weasyprint ≥70. Page-watch design for chota-bot: `~/code/chota-bot/docs/page-watch.md`. `docs/ticktick.md` is the TickTick reference. Candidates: `ko tt note` (REST `kind: NOTE`), Reddit route check in `ko fetch` (Reddit ending free API/RSS, per HN 49912499).
- **`ko hn` (2026-10-15):** search ranks title matches by points, `-y/--years` (default 1) replaced `--since` months, filters comma-joined (`-c` was silently ignored), `--now` uses HN's real rank via Firebase. WORKLOG has the measurements; comment search is the next candidate.
- **koevguide is ko yt's first consumer** (`koev feeds` calls `ko yt channel --json`; triage uses `info` and `-s`). Shape changes there break it — message the koevguide session.
- **YouTube transcripts work from this Mac again** (2026-10-27); the earlier `IpBlocked` was intermittent and is still mapped to `ip_blocked`, with `-s` as the paid route.
- **The direct `GEMINI_API_KEY`** (`~/.zshrc.local`) is out of AI Studio prepay credit (402). OpenRouter carries all Gemini use in ko; `ko doc --deep` figure descriptions still need the direct key.

## Next, in order

0. **Reddit in `ko fetch` is down** (2026-10-28): safereddit.com serves a browser challenge, reddit.synth.download 502s. Mirror churn keeps recurring; the durable fix is Reddit's OAuth API (app "kotools" already registered, see the comment in `fetch.py`). Small adds: exa-py `lite` search type (`exa.py:33`), pydantic-ai `Caching` for `ko ai`.
1. KO: `ko google auth kd` and `ko google auth sfl` to add the YouTube scope (`ko google accounts` shows `missing: youtube` until then). Still open: `ko google auth ne`, and whether the OAuth app is in Testing (7-day tokens): https://console.cloud.google.com/auth/audience?project=kotools-500611
2. Ideas from Spawn's fleet post (https://x.com/jsnnsa/status/2115623382721126826, 2026-10-30), not started. KO wants (a) first; sketch the design before building:
   - (a) `ko decided "<question>"` + agent tool: a Jev judge over WORKLOG, the CLAUDE.md files, memory feedback files and `docs/`, answering "decided (file:line)", "wrong question" or "hold for KO", so agents check before asking. Spawn's judges: 11,000 verdicts in 8 days, 27 needed the founder.
   - (b) Close the loop on ko's own failures: `ko logs --errors` is recorded but unread. Since 2026-11-04 each event carries argv, error message, crash site, cost and caller (WORKLOG), so a group can name what failed and why; level `error` = broke, `warning` = user-fixable. Start with a grouped list in `ko brief`; later a scheduled agent turns each group into a handover item or fix branch.
   - (c) Spend spike check in `ko brief` across gateway, Exa, X and OpenRouter (they caught a leaked key in 15 minutes).
3. Stubs in `docs/ideas.md` (2026-10-14): `ko weather` (Open-Meteo + BOM FTP + Google, like KO's home repo) and `ko books` (blocked: Google Books fielded queries return 0 since ~2026-10-05; re-test before building).
4. Later, from pydantic-ai's decision docs: Jev picking the model tier per step in `ko ai` (`SelectModel`), and a Jev tool-call guard.
5. `ko ai` against the one-agent-loop guidance (svelte skill `references/agent-sidepanel.md`, checked 2026-10-25): 24 tools, no subagents, so nothing to fold. Two follow-ups: fold the repo agent's refs map into `ko ai` (as instructions or a tool) instead of telling the user to switch to `ko agent repo`; and add ~10 golden questions with known answers, each run 2–3 times, alongside the trace-reading eval, then use them to judge each toolset change.
6. `lazy import` (PEP 810) can replace `_cli_shared.lazy()` and the in-function SDK imports — a separate cleanup.
7. Carried over: retire `docs/assistant.md` and `docs/yaad.md`; move the live-network test out of the default suite (`tests/test_gsheets.py:494`); `accounts --check`; fetch logins in the `ko-fetch` agent-browser session; trafilatura import cost upstream issues.
8. KO to decide: adopt `ruff format` repo-wide in one commit, or not at all. Today it would rewrite 43 files in `src/`, so don't run it as part of other work (CLAUDE.md).

## Open calls for KO

- ko vs jaldi split stands (2026-09-29): no new gmail/cal features in ko.
- ops/.venv is still Python 3.14 with kotools installed editable by hand; it breaks on its next rebuild against kotools' `>=3.15`.
