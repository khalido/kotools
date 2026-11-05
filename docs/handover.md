# Handover — 2026-10-28

Read CLAUDE.md first, then this.

**2026-11-06: `ko publish` fix pass, on branch `ocr-and-publish-fixes`, not yet on main** (WORKLOG). Existing sites need a hand patch or redeploy: three gated sites still have the old PIN redirect in `src/index.ts` (`labs/robot-arm/html`, `labs/claude-code-workshop`, `ops/proposals/uts-tmos-robot-arm`); ko-test has no `.assetsignore` and serves `wrangler.jsonc` until it's redeployed; kotools has the old ignore list.

**2026-10-27: LLM calls moved to Vercel AI Gateway; `models.py` owns every model** (a `ModelSpec` per tested model: note, host order, settings; tiers basic/medium/smart; `resolve()`). `ko ai` defaults to basic (deepseek-v4.1-flash). New `ko models info <name>` (and agent tool `model_info`): price, hosts' speed, top HN threads with their best comment. HN comments now come in HN's ranked order. WORKLOG 2026-10-27 has the why and measurements. OpenRouter ($0.21, KO won't top it up: everything moves to the gateway) still serves only `ko judge` (Jev, `/v1/systemone`), which stops when it runs out; moving it is open (does the gateway serve Jev?). `ko yt -s` moved to the gateway 2026-11-04, default model 3.8 Flash, via the paid gateway. The free-tier Google key path is built but switched off (key commented out in config.toml: the free tier was overloaded on day one); uncomment to retry (WORKLOG).

**2026-11-06: `ko doc` OCR defaults to Vision on a Mac, on branch `ocr-vision-default`** (WORKLOG has the engine table). Off Mac: DeepSeek for now; Indra was the best off-Mac engine in the test and isn't wired in. `[ocr] fallback` is off by default. Open: Vision's bad-cache line loss has no known trigger or check. liteparse PR #521 would replace `vision-ocr.swift`.

**2026-11-04: OCR server.** `ko ocr serve` (liteparse's OCR API on 127.0.0.1:8828) and `ko doc -e`: DeepSeek on the gateway by default (thinking off, ~$0.0006/page), `vision` (Apple, free), `tesseract`. Built for sfl-mes's book pipeline and pk_legal (WORKLOG).

**2026-10-28:** the six dogfood bugs are fixed (WORKLOG). `ko models info a b c` compares models in one table; new `ko pkg info <name>...` + `pkg_info` agent tool (WORKLOG 2026-10-28). Packages upgraded (pydantic-ai 2.61) and the ko tool env reinstalled.

Next on this thread, in order: (1) `model_info` tool returns clipped comments + links; (2) `ko ai` instructions: Reddit via `exa_search(include_domains=['reddit.com'])`, reviews default to the last year; X search as a tool with a small limit; (3) code mode for `ko ai`/research, measured before and after; (4) per-model settings one at a time (terra reasoning effort, deepseek thinking off). Speed: `hn.top_comment` pulls the whole Algolia tree for one comment + reply; a Firebase chain (kids[0] → item → kids[0]) would be a few small calls, and is the slow part of `ko models info`.

## Where things stand

- **Python 3.15**, deps upgraded (pydantic-ai 2.56 with the `typesafe` extra). All committed; WORKLOG 2026-10-13/14 has the why.
- **The installed `ko` is a uv tool env, which doesn't follow `uv lock`.** It also ignores the 3-day guard and pulls fresh releases, so reinstall against the lock: `uv export --frozen --no-hashes --no-dev --no-emit-project -o /tmp/c.txt && uv tool install --reinstall -e . -c /tmp/c.txt`
- **New this session:**
  - `ko judge`: typed answers with probabilities from Jev (yes/no, `--choice`, `--score`, `--tags`) through OpenRouter on the existing key. Guide: `ko prompt judge`.
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
8. `ruff format --check` wants to reformat 65 files — pre-existing; decide whether to adopt the formatter repo-wide in one commit.

## Open calls for KO

- ko vs jaldi split stands (2026-09-29): no new gmail/cal features in ko.
- ops/.venv is still Python 3.14 with kotools installed editable by hand; it breaks on its next rebuild against kotools' `>=3.15`.
