# Handover — 2026-11-17

Read CLAUDE.md first, then this. History lives in WORKLOG; this is only what's open.

## Just done

- **One agent (2026-11-17, branch `one-agent`).** `ko ai` absorbed `agent research|repo|tv` as on-demand capabilities; the old names are hidden aliases. Terminal `ko ai` no longer stops at the first narration, piped output keeps the whole answer. Guardrails: $1 budget per run (`--budget`) and big tool results spilled to disk; smart research $12.58 (old agent) → $0.71. Trial and numbers: WORKLOG 2026-11-17.
- **Main is 17 commits ahead of origin, unpushed**, waiting on KO: overnight sanity, cleanup and telemetry merges plus the pre-push fixes (gmail inline embeds, fetch shared failure code). Advisor notes: `/private/tmp/claude-501/-Users-ko-code-kotools/7b6a589f-7e3c-448f-8633-ecc271d3bb5a/scratchpad/prepush/advice.md` (scratch, may be gone).

## Next, in order

1. **Closed stdout exits 1 and logs an error for every command** (`ko --help | head`, the commonest agent call). One handler in `cli.py` `main()`: exit 0, skip the error log. Typer/click's EPIPE path is `typer/core.py:359`.
2. **One agent follow-ups** (`docs/ideas.md` "One agent: follow-ups"): KO's call on tool overlap; prompt caching.
3. **Small pre-push leftovers:**
   - `ko fetch ::1` gives `InvalidURL` (code `error`) and `ko fetch https://` (no host) goes to the paid fallbacks; both should be `usage`.
   - Tighten `test_decide_each_raises_a_missing_key_once` to count calls.
   - AGENTS.md's destructive list is missing `gmail draft delete`.
   - `ko prompt decide`: "use `--json` to threshold" (a text `error` cell passes `awk` thresholds).
   - List the real error codes in AGENTS.md.
4. **Key registry:** one table of API keys that `ko doctor` rows are generated from (advisor's one medium-sized build).
5. Decide `ko brief`'s future (keep, fold into `ko ai`, or drop).
6. Reddit in `ko fetch` is down (mirrors churn); the durable fix is Reddit's OAuth API (app "kotools" registered, see `fetch.py`).
7. `ko decided "<question>"`: a Jev judge over WORKLOG, CLAUDE.md files and `docs/` so agents check before asking (KO wants it; sketch first).
8. Later: `lazy import` (PEP 810) for `_cli_shared.lazy()`; move the live-network test out of the default suite (`tests/test_gsheets.py:494`); retire `docs/assistant.md` and `docs/yaad.md`.

## Waiting on KO

- Push main; merge `one-agent`.
- Telemetry check: set `POSTHOG_API_KEY` and `[telemetry] enabled = true`, run `ko llm "hi"` and `ko ai`, check `$ai_generation` events land in PostHog, close issue #2.
- Keys: `OMDB_API_KEY` (OMDb scores), AeroDataBox (`ko flight` fallback, designed in `docs/flight.md`); set `[tmdb] my_providers`.
- Gmail: delete test draft `r-39674769328854445` and everx's "OLD, delete" drafts (`ko gmail draft delete --yes`).
- `ko google auth kd` / `sfl` to add the YouTube scope; whether the OAuth app is still in Testing (7-day tokens).
- Unpushed release-age commits in thinker and koevguide (main) and everx (branch `deps/release-age-1-day`).
- `ruff format` repo-wide in one commit, or never (rewrites 43 files; don't run it inside other work).
- Issue #1 (opencode app plus secrets).
- The `ko publish` PIN patch in `ops/proposals/uts-tmos-robot-arm/src/index.ts` is uncommitted in ops.

## Where things stand

- Python 3.15, pydantic-ai 2.67. The installed `ko` is a uv tool env that ignores `uv lock`; reinstall against the lock: `uv export --frozen --no-hashes --no-dev --no-emit-project -o /tmp/c.txt && uv tool install --reinstall --compile-bytecode -e . -c /tmp/c.txt` (keep `--compile-bytecode`: agent shells set `PYTHONDONTWRITEBYTECODE=1`).
- koevguide calls `ko yt channel --json`; shape changes there break it.
- ko vs jaldi split stands: no new gmail/cal features in ko.
- ops/.venv is Python 3.14 with kotools editable by hand; it breaks on its next rebuild.
