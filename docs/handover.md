# Handover — 2026-10-28

Read CLAUDE.md first, then this.

**2026-10-27: LLM calls moved to Vercel AI Gateway; `models.py` owns every model** (a `ModelSpec` per tested model: note, host order, settings; tiers basic/medium/smart; `resolve()`). `ko ai` defaults to basic (deepseek-v4.1-flash). New `ko models info <name>` (and agent tool `model_info`): price, hosts' speed, top HN threads with their best comment. HN comments now come in HN's ranked order. WORKLOG 2026-10-27 has the why and measurements. OpenRouter ($0.21) now only serves `ko judge` and `ko yt -s`.

**2026-10-28:** `ko models info a b c` compares models in one table; new `ko pkg info <name>...` + `pkg_info` agent tool (WORKLOG 2026-10-28). Packages upgraded (pydantic-ai 2.61) and the ko tool env reinstalled.

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

0. **Bugs from the 2026-10-27 dogfood run, checked:** `ko fetch` accepts bot walls ("not a robot") as `live`; a non-existent reddit thread returns another sub's page from the mirror as success (check the post id); `ko hn item` rejects a pasted item URL; `ko hn search` help says "highest points first" but the backfill tier ranks title matches first; `fetch --json` returns an object, not the per-URL array, when every URL fails; arxiv abs front matter mixes affiliations into `authors`. Small adds: exa-py 2.26 `lite` search type (`exa.py:33`), pydantic-ai `Caching` for `ko ai`.
1. KO: `ko google auth kd` and `ko google auth sfl` to add the YouTube scope (`ko google accounts` shows `missing: youtube` until then). Still open: `ko google auth ne`, and whether the OAuth app is in Testing (7-day tokens): https://console.cloud.google.com/auth/audience?project=kotools-500611
2. Stubs in `docs/ideas.md` (2026-10-14): `ko weather` (Open-Meteo + BOM FTP + Google, like KO's home repo) and `ko books` (blocked: Google Books fielded queries return 0 since ~2026-10-05; re-test before building).
3. Later, from pydantic-ai's decision docs: Jev picking the model tier per step in `ko ai` (`SelectModel`), and a Jev tool-call guard.
4. `ko ai` against the one-agent-loop guidance (svelte skill `references/agent-sidepanel.md`, checked 2026-10-25): 24 tools, no subagents, so nothing to fold. Two follow-ups: fold the repo agent's refs map into `ko ai` (as instructions or a tool) instead of telling the user to switch to `ko agent repo`; and add ~10 golden questions with known answers, each run 2–3 times, alongside the trace-reading eval, then use them to judge each toolset change.
5. `lazy import` (PEP 810) can replace `_cli_shared.lazy()` and the in-function SDK imports — a separate cleanup.
6. Carried over: retire `docs/assistant.md` and `docs/yaad.md`; move the live-network test out of the default suite (`tests/test_gsheets.py:494`); `accounts --check`; fetch logins in the `ko-fetch` agent-browser session; trafilatura import cost upstream issues.
7. `ruff format --check` wants to reformat 65 files — pre-existing; decide whether to adopt the formatter repo-wide in one commit.

## Open calls for KO

- ko vs jaldi split stands (2026-09-29): no new gmail/cal features in ko.
- ops/.venv is still Python 3.14 with kotools installed editable by hand; it breaks on its next rebuild against kotools' `>=3.15`.
