# Handover — 2026-10-14

Read CLAUDE.md first, then this.

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
- **Transcripts are IP-blocked from this Mac** (youtube-transcript-api `IpBlocked`), so the free path fails today; `-s` is the working route. The excerpt code path is unit-tested only.
- **The direct `GEMINI_API_KEY`** (`~/.zshrc.local`) is out of AI Studio prepay credit (402). OpenRouter carries all Gemini use in ko; `ko doc --deep` figure descriptions still need the direct key.

## Next, in order

1. KO: `ko google auth kd` and `ko google auth sfl` to add the YouTube scope (`ko google accounts` shows `missing: youtube` until then). Still open: `ko google auth ne`, and whether the OAuth app is in Testing (7-day tokens): https://console.cloud.google.com/auth/audience?project=kotools-500611
2. Stubs in `docs/ideas.md` (2026-10-14): `ko weather` (Open-Meteo + BOM FTP + Google, like KO's home repo) and `ko books` (blocked: Google Books fielded queries return 0 since ~2026-10-05; re-test before building).
3. Later, from pydantic-ai's decision docs: Jev picking the model tier per step in `ko ai` (`SelectModel`), and a Jev tool-call guard.
4. `lazy import` (PEP 810) can replace `_cli_shared.lazy()` and the in-function SDK imports — a separate cleanup.
5. Carried over: retire `docs/assistant.md` and `docs/yaad.md`; move the live-network test out of the default suite (`tests/test_gsheets.py:494`); `accounts --check`; fetch logins in the `ko-fetch` agent-browser session; trafilatura import cost upstream issues.
6. `ruff format --check` wants to reformat 65 files — pre-existing; decide whether to adopt the formatter repo-wide in one commit.

## Open calls for KO

- ko vs jaldi split stands (2026-09-29): no new gmail/cal features in ko.
- ops/.venv is still Python 3.14 with kotools installed editable by hand; it breaks on its next rebuild against kotools' `>=3.15`.
