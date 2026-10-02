# Handover — 2026-10-02

Read CLAUDE.md first, then this.

## Where things stand

- **Committed 2026-10-02 as one combined commit** (KO's call): the earlier service-account /
  opencode batch plus the 2026-09-29 → 10-02 sessions. WORKLOG has each change's why.
- **Shipped across those sessions:**
  - `ko google` accounts with tags `kd` (ko@khalido.dev, default) and `sfl`; `ne` not signed in yet.
  - Thread-first gmail; cal merged across accounts; brief covers every account.
  - Lazy imports (0.2 s start); one Google error net; `--json` `fix` field.
  - Fetch: parallel rescue (agent-browser, Exa, archives) + a 1 h cache.
  - `ko doc`: OCR only when needed, page markers, `--md`/`--json`, fenced pages, no 1,000-page cap, .docx read directly.
- **sfl-mes switched to `ko doc`** and deleted its own manual-to-text script (2026-10-01).
- **The installed `ko` is a uv tool env, which doesn't follow `uv lock`.** After a dependency change: `uv tool install --reinstall -e .`

## What shapes the next work

- **ko vs jaldi (Fable review, 2026-09-29).** Split by state:
  - jaldi's Bun sidecar gets resident things: mail and calendar sync plus tokens, jobs, feeds, chat.
  - ko keeps stateless one-shots, including Sheets, Docs and Drive.
  - ko gmail/cal serve coding agents until jaldi's sync is trusted, then get deleted. **No new gmail/cal features in ko.**
  - jaldi-61 has the jaldi-side findings.

## Next, in order

1. Open: a paid per-page PDF fallback for hard layouts (two-column `--md`). KO is topping up the direct Gemini key; he prefers it over OpenRouter for Google. Retest Danfoss page 18 on `google:gemini-3.5-flash`. Datalab / Mistral OCR are the alternatives (research in WORKLOG 10-01).
2. KO: `ko google auth ne` in a terminal. The kotools OAuth app is External (sfl signed in fine); whether it's published or still in Testing (7-day token expiry) is unverified — check https://console.cloud.google.com/auth/audience?project=kotools-500611.
3. From the review, not started:
   - Retire `docs/assistant.md` and `docs/yaad.md` (jaldi owns the resident agent).
   - Move the live-network test out of the default suite (`tests/test_gsheets.py:494`).
   - Account banner for drive/gsheets.
   - `accounts --check` (live refresh).
4. Fetch (2026-09-30): parallel rescue + cache landed (WORKLOG). Next: logins in the
   `ko-fetch` agent-browser session (`--restore ko-fetch`, KO signs in headed once) so paywalled
   sites (NYT) come back whole; KO wants to think the login flow through first.
5. Perf, measured 2026-09-29:
   - `ko fetch` warm takes 1–2.5 s: ~0.5 s Python+ko, 0.65 s importing trafilatura, network on top, extraction 0.04 s.
   - The trafilatura cost is upstream, worth filing issues: `trafilatura/deduplication.py:22` scans all Unicode code points (~100 ms), and dateparser's timezone regex (~120–300 ms).
   - `ko doc` now OCRs only pages without a text layer (109 s → 4.6 s on the magazine); done 2026-09-30.
6. Hold (no consumer yet): `ko schema --json`, `KO_JSON=1`, NDJSON progress. Revisit when jaldi calls more of ko.

## Open calls for KO

- The commit split (above).
- Whether jaldi should shell `ko … --json` for Sheets, TickTick, fetch, sum and papers (see the "what jaldi could use ko for" discussion from 2026-09-29).
