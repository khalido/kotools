# WORKLOG — ko

Newest first. Big picture only — git commits have the detail; candidate ideas and decisions live in `docs/ideas.md`.

## 2026-10-15 — `ko hn`: search ranked by points, real front-page order

- **Filters were silently dropped.** `numericFilters` went as repeated params, and Algolia keeps only the first, so `-c` never applied under the date window. Comma-joined now, as Algolia's docs show. The old comment and test claimed the comma form 400s; it doesn't.
- **Search ranking.** Algolia ranks word proximity above points: "uv python" put a 2-pt story first and missed the 2,214-pt uv post. Now: title matches with 20+ points, 30 hits, sorted by points; under n, the rest fill in after (title matches at any points, then body/url matches). One call for common topics, two for niche ones ("sqlite vector search": best stories 2–8 pts). Title-only removes body-text noise (a 657-pt home-inventory app for "local llm").
- **`typoTolerance=false`.** Typos made "trust"/"roasted"/"robust" match "rust". Algolia ranks typo matches last, so they didn't show before, but re-sorting by points or date lifts them to the top. Trade-off: a misspelled query gets weaker results.
- **`--since <months>` → `-y/--years`, default 1** (KO: rarely want older; `-y 0` = all time). A URL query searches every submission of it, all time, no floor: scheme and `www.` stripped (Algolia matches them as words — `https://paulgraham.com/avg.html` found 2 of 24 submissions, the rest are `http://`), then matched against `url` only (else Ask HN posts quoting it come back too).
- **`--now` = HN's real rank.** Algolia's `front_page` tag gets the members right (29/30) but has no order; we used to sort by points, which dropped rising stories (HN #2 and #3 missing). Order now comes from Firebase `topstories`; data from Algolia; stories Algolia hasn't tagged yet are fetched from Firebase. Two calls in parallel, ~0.7s; Firebase for every item took 1.5s for 30. Firebase is otherwise no use: no search, one request per item.
- **Not built:** comment search (`tags=comment`). An sfl-mes research doc went around ko to Algolia for it, so it's the next flag if wanted.

## 2026-10-14 — `ko yt`: write-ups by Gemini watching, batch info, cache

- **`-s` now watches the video.** Gemini Flash through OpenRouter (`video_url`, pydantic-ai `VideoUrl` via `llm.run`), prompt `src/ko/prompts/yt-condense.md` (KO's brief via koevguide: every point with its detail, on-screen text marked, filler cut, length follows content). Works when transcripts are IP-blocked and sees on-screen prices/readouts. Model: `-m`, `KO_YT_MODEL` or `[yt] model`.
- **Flash, not Flash-Lite** (31-min test video, same v2 prompt): Lite 898 words, $0.05, but thin and wrong on the drive events (missed the solid-line crossing and the 70 sign misread, called a no-indicator exit "a disengagement"); Flash 1,364 words, $0.29, every event with its timestamp. Cost ranged 8.5–33c per run on the same input.
- **Cache** `~/.cache/ko/yt/`: transcripts by id; write-ups by id + model + sha of the prompt (editing the prompt re-runs). `--refresh` bypasses. Metadata isn't cached (views move).
- **`info <url>...`**: one videos.list per 50, JSON is always a list; each video carries `transcript` = excerpt (100 words from start, middle, end) / `text` (`--full`) / `{available: false, code}`. Missing ids come back as `{id, code: not_found}` entries.
- **Error codes**: ip_blocked, no_captions, private, not_found, quota, network. A blocked transcript's message names `-s` and its price but sets no `fix` field: agents run `fix` blind, and that would be a paid fallback by another name. No automatic Gemini fallback: free stays free (Gemini's review agreed).
- **Spend guards** (ko-review): `info -s` refuses more than 5 uncached write-ups per call; a failed or cut-short write-up still reports what it cost. Each `info` fetches every video's free transcript — fine for a few, the IP-block pattern for 50.
- **`channel --since`** lifts the default cap of 10; a capped list says there's more on stderr.
- **`channel --since`** stops paging once past the cutoff; every upload gets its duration (+1 unit per 50).
- **Shorts left out by default** (KO via koevguide: 11 of 25 inbox videos were Shorts or clips; `--shorts` brings them back). The Data API has no Shorts field and a ≤3:00 cutoff also catches ordinary clips, so it's exact instead: uploads of 3 minutes or less get a HEAD to `youtube.com/shorts/<id>` — 200 is a Short, a redirect isn't. A failed check keeps the video with `is_short: null` and says so. `-n` counts after the filter.
- **Direct Gemini key** (`GEMINI_API_KEY`, `~/.zshrc.local`) is out of AI Studio prepay credit (402); OpenRouter is the path.

## 2026-10-14 — `ko judge`: decision models (Jev)

- **What.** `ko judge "<question>" [-f x | stdin]` returns a typed answer with probabilities: P(yes) by default, `--choice` (one of many, every option's probability), `--score` (ordered levels, nearest level + exact position), `--tags` (yes/no per tag). Default model Jev; `-m` takes any `ko llm` model, which answers the same type bare — the way to compare. Guide: `ko prompt judge`.
- **Route.** Jev through OpenRouter's `/v1/systemone` on the existing `OPENROUTER_API_KEY` (pydantic-ai `TypeSafeModel` + `TypeSafeProvider(base_url="https://openrouter.ai/api")`) — one key, one bill; `TYPESAFE_API_KEY` goes direct when set. Needs the `typesafe` extra (the SDK is imported either way). Measured: ~350 tokens, <$0.0001, a call.
- **Why its own command, not `ko llm -m jev`** (built that first; ko-review pushed back): a decision needs input, takes no system prompt, schema or `-x`, and needs `--json` for its numbers — `ko llm` was heading to 8 flags with an exclusion matrix. `ko llm -m jev` now errors toward `ko judge`.
- **How each kind maps.** yes/no = a bounded float (the raw probability, no threshold); choice = pydantic-ai `Choices`; score and tags = `StructuredDict` with `anyOf` const+description (pydantic-ai has no run-time rubric helper; `UseEnumMemberDocstrings` reads source docstrings, so a dynamic IntEnum can't carry level meanings). Score rounding is pydantic-ai's, not ours.
- **Later, not built:** `SelectModel` routing in `ko ai` (Jev picks the tier per step) and a Jev tool-call guard — both in pydantic-ai's decision docs.

## 2026-10-13 — Python 3.15

- **Why.** PEP 810 `lazy import` can replace `_cli_shared.lazy()` and the in-function SDK imports
  that keep startup at ~0.2 s. Not done yet; this is the version bump only.
- **Measured.** Same lockfile installs on 3.15.0; 601 tests pass; `ko --help` ~0.23 s, as on 3.14.
- **uvx / tool installs.** `requires-python >=3.15` makes uv fetch a managed 3.15 on its own.
- **Not moved:** ops/.venv is still 3.14 with kotools installed editable by hand.

## 2026-10-05 — doc --deep: docling for layouts liteparse can't read

- **Why.** liteparse `--md` turns two-column prose (the Danfoss parameter descriptions) into a
  fake table mixing both columns, and its layout stats report one column there, so no
  per-page fallback is possible. Measured on the same pages: docling reads page 18 in order
  (each parameter whole), keeps the page-63 register table exact (`16-13 | Frequency | 0 |
  1 set-up | TRUE | -1 | Uint16`), ~3 s a page warm. Local marker kept columns but took 91 s
  a page on CPU; Gemini via OpenRouter aborted mid-page; Anthropic's own pdf skill is
  pypdf/pdfplumber/pdftotext — nothing layout-aware.
- **Shape.** `ko doc --deep file [-o out.md]` runs `_docling_worker.py` in docling's own uv
  environment (`uv run --no-project --with docling`): torch + ~1 GB of models never enter
  ko's deps, and the first run's setup is one-off (later runs: 8 s for 3 pages). The worker
  returns per-page markdown (compact tables); ko adds the source line + `<!-- page N -->`.
  OCR only when ko's own check says a page lacks text.
- **Figures.** With `-o`, saved to `<name>_assets/`, renamed `p018-fig1.png` by first page,
  one file per distinct image (the logo on every page is kept once, linked from each).
  Without `-o`, `<!-- figure -->` placeholders. Agents view the PNG when they need it — the
  same move Anthropic's skills make (render, then look) rather than describing everything.
- **`--describe`:** Gemini (`[doc] describe_model`, default gemini-3.8-flash) via its
  OpenAI-compatible endpoint, from docling's API picture describer. docling reports SUCCESS
  when every call failed (a 402 only reaches its log, descriptions come back empty), so the
  worker counts tried vs got and captures error logs; ko raises on any shortfall. Not yet
  run against a funded key.
- **docling's Office readers:** fine but no reason to switch .docx (ko's direct reader now
  matches content at 0.2 s vs 4–8 s); its .xlsx output was 10 MB of padded tables before
  `compact_tables`. LibreOffice isn't installed here, so liteparse's Office route can't run;
  it's also the weakest path (Office → PDF → re-guess structure).

## 2026-10-02 — doc: .docx read directly (python-docx), no LibreOffice

- **Why.** `ko doc` on a .docx shelled out to LibreOffice (liteparse converts Office → PDF), so it
  failed on any machine without it — for the most common document an agent is handed.
- **Direct read:** paragraphs and tables in document order (`iter_inner_content`), headings and
  lists from Word styles (and Word's own numbering — bullets are often "Normal" + numPr),
  tables as pipe tables. A merged cell is written once: python-docx repeats the same `_tc` for
  every grid column and row it spans, so a repeat is left blank. All-blank rows (Word spacing
  in form-style tables) are dropped. Provenance line: author, created, changed (and by whom,
  when not the author), size, md5.
- **No pages:** a .docx has none until something lays it out. `--pages` / `--json` (both
  page-shaped) still take the LibreOffice → PDF route for page markers. `doc.parse` (used by
  `llm -f`) reads .docx directly too.

## 2026-09-30 — fetch: parallel rescue (real browser, Exa, archives) + a local cache

- **Why.** ko fetch acts as KO, so it gets pages coding agents' built-in fetchers don't — but on
  a wall it walked archive.today → Wayback serially at 30 s each: stackoverflow (a Cloudflare
  "Just a moment…" in 0.1 s) took 60 s+ to fail; carsales/whirlpool 36–44 s to reach Wayback.
- **Rescue in parallel, pick by preference.** When the free live fetch fails, every rescuer
  starts at once; the most-preferred success wins as soon as everything above it has failed
  (25 s deadline, daemon threads so a hung one is abandoned). Walled/empty: browser > Exa >
  archive.today > Wayback. Gone (404/410/no answer): Wayback > Exa. Other statuses count as
  walls, not deaths — Cloudflare challenges come as 403 and 503. Archive timeouts 30 → 10 s.
- **Browser = agent-browser** (no Python SDK — PyPI has none; it's a Rust CLI with a warm daemon,
  ~2.5 s a page). ko's own session `ko-fetch` (never the shared default), one tab behind a
  file lock; waits for `readyState` + a non-challenge title, then extracts the rendered HTML
  with the same trafilatura path as live (its own `read` keeps sign-up modals).
- **Exa** runs only with EXA_API_KEY (paid, ~$1/1k pages) — it rescued carsales,
  realestate and whirlpool in 4–14 s where archives failed or crawled.
- Measured after: stackoverflow 5.6 s (browser), carsales 4.9 s, realestate 3.6 s,
  whirlpool 14 s (Exa). NYT's live page returns the paywall teaser as a "success" — logins in
  the ko-fetch session (later) are the fix, not a detector.
- **Cache:** `~/.cache/ko/fetch/`, keyed on url + archive flags, `[fetch] cache_hours`
  (default 1); `--fresh` skips it; hits say "cached Nm ago".

## 2026-09-30 — doc: OCR only the pages that need it; deps refreshed

- **`ko doc` OCR'd every page by default.** A 132-page magazine PDF took 109 s, and `--no-ocr`
  read the same text layer in 0.9 s for ~6% less text (words baked into photos). Now: read the
  text layer, then OCR only the pages liteparse's `is_complex` pass says are scanned, textless or
  garbled, and splice them back in page order. The magazine takes 4.6 s; an image-only PDF still
  OCRs. `--ocr/--no-ocr` force either way.
- **Page markers (agent feedback, 2026-10-01):** an agent converting a 72-page Danfoss guide
  kept its own script because ko's output couldn't be traced to a page. `ko doc` now puts
  `<!-- page N -->` (plus the PDF's own label when it differs) before every page; `--md` gives
  liteparse's per-page markdown (pipe tables, `image_mode="none"` so no links to images never
  written); `--json` gives `[{page, label, text}]`. `doc.parse()` stays marker-free for `fetch`
  and `llm -f`.
- **sfl-mes in real use (1,612 + 72 pages, 2026-10-01), three fixes:**
  - liteparse caps at 1,000 pages *silently* (a 1,113-page manual → pages 1–1000, exit 0). ko
    passes `max_pages=100_000` and raises if fewer pages come back than the document has.
  - Plain pages now sit in a ```` ```text ```` fence under their marker, so the output is a
    valid markdown file whose column alignment survives rendering (their wrapper script did
    this). The source line counts pages without text.
  - "vector-text" (text drawn as outlines) joins the OCR triggers.
  - Also: the installed `ko` tool env had liteparse 2.11 (uv tool envs don't follow
    `uv lock`), so `page_label` crashed there — floor raised to `liteparse>=2.15`, tool
    reinstalled. After any dep bump: `uv tool install --reinstall -e .`.
- **Known gap: `--md` on two-column prose.** liteparse's markdown renderer turns a two-column
  page into a fake pipe table mixing both columns, and its layout stats report column_count=1
  there, so ko can't route around it. Plain mode reads fine. Measured alternatives on the
  same page: local marker (old CPU build) keeps columns apart but took 91 s for one page;
  Gemini Flash via OpenRouter aborted mid-page (below). A paid per-page fallback (Gemini /
  Mistral OCR / Datalab, ~$4–10 per 1k pages) is the open decision — research in the
  2026-10-01 session.
- **`ko llm` passed partial output as an answer.** OpenRouter → Google returned half a page
  with `finish_reason: "error"` and 0 usage; ko printed it, exit 0. `llm.run` now raises
  `TruncatedOutput` on error/length/content_filter finishes.
- **Not liteparse's own `needs_ocr`:** it also fires on "embedded-images" and "sparse-text",
  i.e. any page with a photo (126 of 132 pages). Those pages already have their text.
- Tesseract's "Detected N diacritics" lines went to fd 2 past liteparse's `quiet`; silenced
  around OCR calls only.
- **`uv lock --upgrade`:** pydantic-ai 2.36→2.51, oauthlib 3→4, openai 3.6→3.22, weasyprint
  69→70, liteparse 2.15; tests and live llm/schema/ai/gmail/pdf/websearch all pass. pydantic-ai
  2.51 added an ASCII-art Logfire banner on the first agent run; ko sets `PYDANTIC_AI_NO_BANNER`.
- **`--json` added where the output is data:** `fetch` (per-URL rows, failures included, so
  jaldi parses one array), `gmail view|thread|draft show` (whole bodies + account), `cal add`
  (the created event), `google accounts`. Left plain: commands whose output *is* a document
  (`doc`, `gdocs get`, `hf get`) or a single path/URL.

## 2026-09-29 — google: one way in (`ko google`), gmail goes thread-first

- **Why.** KO works across 3–5 Google accounts (own + one per client). Sign-in was copied into five
  groups (`gsheets/gdocs/cal/gmail/drive auth`), the default lived only in hand-edited config.toml,
  and gmail output never said which account it read.
- **`ko google auth|use|accounts|rename|logout`.** Per-service `auth`/`accounts` removed outright
  (KO: "one way to login"). Accounts are short tags. `use` writes `state/settings.json` — per machine,
  command-owned — so commands never rewrite the synced, hand-edited config.toml; config's
  `[google] account` stays as the fallback so other machines keep working.
- **Unknown tag = error, not a browser.** `-a sfll` used to open a consent flow and sign up a new
  account by accident; now it lists who is signed in.
- **Thread-first gmail.** Lists are threads (Gmail's own model; a lone email is a one-message thread).
  `draft reply <thread>` answers the latest message, as Gmail's Reply does — if that one is mine, it
  goes to my message's recipients. `--msg` picks another. The positional takes any message id too:
  Gmail's thread id is its first message's id, so a later id needs a threadId lookup. `brief` keeps
  the message-level `recent()`.
- **Calendar merges accounts.** Reads cover every signed-in OAuth account, deduped on (iCalUID,
  start) so a client invite on two accounts shows once. One dead account is a warning, not a failed
  agenda. `cal add`'s target is its own setting (`ko cal use`): changing the mail default must not
  move where events land. `brief` gathers today + unread per account. Network blips (SSL EOF seen
  live, twice in ten minutes) now die clean instead of a traceback — `NETWORK_ERRORS`, not all
  `OSError`, so a full disk mid-download isn't mislabeled.
- **Startup 1.74 s → 0.19 s.** Every group registers at import, so an eager `from . import llm`
  made `ko cal` pay for pydantic-ai, exa and the X SDK. `_cli_shared.lazy()` (stdlib
  LazyLoader) keeps `llm_mod.fn()` call sites unchanged; `llm`/`exa`/`x` import their SDK inside
  the function that uses it, because `--help` text reads their constants at import.
- **One error net, one service getter.** `_GoogleTyper` runs every Google command inside
  `_google_errors` (19 of 51 had none; 31 hand-written `with` blocks went). `google_auth.service(api)`
  replaced five identical getters. The five `_handle`s stay: each is a single `raise_for_status`
  call with its own error class and hint, so they're configuration, not duplication. `--json` errors gained
  `fix` (the command that resolves it); cf's launch post doesn't cover errors, and the field is
  the agent-CLI pattern the research pass found. `ko doctor` probes the network once and skips
  live checks offline (1.4 s, not a hang).

## 2026-09-10 — drive: folder mirror + push, and the Drive scope widens

- **Why now.** The ops session built `ne drive ls|pull` (one-way sync of a client folder into a
  gitignored `data/<slug>/`) and KO wanted the same in ko, plus write-back. ko's documented
  `drive.file` stance made that impossible: per-file scope can't list a folder ko didn't create.
- **Scope: `drive.file` → `drive.readonly` (read token) / `drive` (write token).** The blast-radius
  argument was real but the practical use is client folders; per-folder scoping is a
  service-account profile, which ko already has. Old tokens re-consent once — `_load_cached`
  checks for the Drive scope the way it already checked the write marker. Catch to remember:
  `drive` is a Google *restricted* scope — harmless in Testing/Internal, but publishing the
  External app with it means Google verification.
- **Manifest, not mtime.** ne stamps local mtime from `modifiedTime` and calls "unchanged" size +
  mtime ≥ remote. That can't distinguish "edited here after pull" from "both sides changed", so a
  push would clobber a remote edit. `.ko-drive.json` records per file the id, remote md5/modifiedTime
  and local md5/size as of the last sync: both sides compare to a common base. Binary files use
  Drive's free `md5Checksum`; exported Docs/Sheets/Slides have none (and no `headRevisionId`
  either — confirmed), so they fall back to `modifiedTime`. Local side: size+mtime fast path,
  hash only when mtime moved.
- **Refusals over guesses.** Both-sides-changed is a conflict: pull leaves it, push uploads
  nothing at all (all-or-nothing) — `--force` makes Drive win on pull, local on push. A file
  removed on Drive but still local is a push conflict too (else the next push resurrects it).
  Untracked local files that match Drive byte-for-byte are adopted; differing ones are conflicts.
  Never deletes on either side; exported natives never push (a docx re-import drops comments).
- **Gemini second opinion (design-level, triaged):** took — `/` in Drive names must not become a
  directory, duplicate names in one folder collapse to one path (first wins, twin reported),
  save the manifest in `finally` so a mid-run 403 leaves it consistent, refuse a folder arg that
  disagrees with the manifest, push confirms on a TTY like every outward command. Rejected —
  `version` over `modifiedTime` (it bumps on invisible server-side changes: noisier, not safer).
- Left out of v1: following shortcuts (listed as unsupported), local deletion propagating anywhere
  (a locally deleted file comes back on the next pull), concurrency on the folder walk.
- Offline suite: a fake `files().list()` + stubbed transfer helpers cover walk/pull/push
  classification (18 tests). Live: needs the re-consent first.

## 2026-09-11 — google auth: who am I, and no silent hangs

- **`accounts` like `gh auth status`.** Asked for after the drive work: `ko gmail accounts`
  showed `* work` and nothing else. Google's token JSON carries no address, so ko learns it
  once (Drive `about.get`, Gmail profile as fallback) using only the cached token and stashes
  it in the token file; one derived line describes the grant per API (read-only / read+write /
  missing / stale per-file Drive). Same renderer in every group's `accounts` and `ko doctor`.
  First run surfaced two facts: the only signed-in account is ko@khalido.dev (not the personal
  Gmail), and its grant is read-only across the board.
- **The read-only grant then explained a field bug** (everx session): `gdocs push` and
  `gmail draft new` "hung for minutes" from an agent. A write on a read-only token fell into
  the consent flow, which blocks on a browser callback nobody can click. Fixed at the one
  chokepoint, not per command: an *implicit* flow (a command found no usable token) refuses
  without `/dev/tty` — one line saying why (`read-only grant and this is a write`, `isn't
  signed in`, `pre-2026-09 token`, `expired token that couldn't refresh`) and the exact `ko
  gsheets [-a x] auth` to run — and with a terminal prints that reason before opening the
  browser. The `auth` command is `explicit` and always runs. `/dev/tty` rather than
  `stdin.isatty()`: a human piping markdown into `gdocs push -` still has a terminal; an
  agent's shell has none (ENXIO).
- **A bare `--account` shortcut was tried and dropped.** Typer 0.27 ignores `flag_value`, and
  Click's value-less option would swallow the next subcommand as the account name.
- **Global net in `main()`** for `AuthError`: 27 of 55 Google commands had no error wrapper, so
  the new clean refusal still arrived as a traceback from `gsheets set`. Every command now
  gets the one-liner. (Spotted in passing, not fixed: `gdocs push` on a Sheet id 400s with a
  traceback from `export_markdown` — the same missing wrapper for `HttpError`.)

## 2026-08-31 — deps: ko's own HTTP moved to httpx2 (rode pydantic-ai 2.32 → 2.36.0)

- **The upgrade flipped the board.** A routine `uv lock --upgrade` landed pydantic-ai-slim
  2.36.0, which migrated to **httpx2** (pydantic's kept-API fork of httpx). With the ecosystem
  already moving (OpenAI SDK 3.3 + Anthropic's SDK on httpx2, httpxyz folded in as "the blessed
  fork", upstream httpx dormant since 2024-12 with 1.0 built in a private repo), the wait-for-
  pydantic-ai trigger from that morning's research fired the same day.
- **Our code: `import httpx2 as httpx` everywhere (22 sites, src + tests).** Tests construct
  Response/Request and raise the exception classes our code catches — they must be the same
  package, so the swap covers both. Plain `httpx` stays a dep because exa-py, arxiv2markdown,
  mcp/fastmcp-slim, google-genai and pydantic-ai-harness pin it; both stacks ship, which is
  the fork's design. One behavioral delta accepted knowingly: **httpx2 verifies TLS against
  the OS trust store, not certifi** — live-smoked on a generic fetch, the reddit ladder
  end-to-end, and `ko billing` (Client + auth headers), all fine; a mirror failing OS-store
  verification just becomes a dead rung in the ladder.
- **2.36 break found by the regression guard, fixed in the test only:**
  `agent._instructions` entries are now `SourcedInstruction` wrappers (callables inside
  `.instruction`) — the preamble guard looks through the wrapper and still handles the
  pre-2.36 bare-callable shape, since the floor (`>=2.32`) admits both.
- Full suite 531 passed + ruff clean before and after; `ko llm` smoke through 2.36+httpx2.


## 2026-08-31 — reddit: the mirror became a ladder (safereddit → synth → Wayback)

- **The single mirror died** — reddit.synth.download 502s, which is what "I have trouble
  reading reddit pages" turned out to be. Re-probed every direct method against a live
  thread first, because the BROWSER bundle changed the board: www/api `.json` → 403 with
  AND without the bundle + a Chrome UA (this wall is endpoint/IP-shaped, not header-shaped
  — the drive.com.au lesson does not transfer); old.reddit → 302 login wall (`reason=lor2`,
  a logged-out rate limit); pullpush.io → 200 with zero data; r.jina.ai free → empty shell;
  Wayback → no old.reddit capture. Nothing direct works.
- **redlib is other people having solved it** — an open-source reddit frontend with public
  instances; a redlib URL is just a readable URL, so it's the same straight host swap the
  synth mirror already was. safereddit.com served the thread cleanly through trafilatura
  (post, selftext, comment authors + ages, ~9.3k tokens). Of 9 instances probed it was the
  only one alive — catsarch 403s our UA, perennialte is shut down (410), bloat.cat serves
  a challenge page, privacydev times out. Instance churn is THE failure mode.
- **So: `_REDDIT_MIRROR` (one host) → a try-in-order ladder** `safereddit.com →
  reddit.synth.download` (502 today, fine last month — kept second), first non-empty
  extract wins, Wayback of the ORIGINAL url after the whole ladder, then the loud error.
  Swappable without a release: `[fetch] reddit_mirrors = "h1,h2"` / `KO_REDDIT_MIRRORS`.
  Mirror requests now carry the BROWSER bundle (both live successes had it). Tests pin
  ladder order, the fall-through (200-but-unextractable first mirror), the config
  override, and wayback-of-original when everything's down.
- **Durable path registered, not built:** Reddit OAuth app "kotools"
  (developers.reddit.com/apps/kotools) — the answer when the ladder dies for good or
  search/listings/comment-trees are wanted. For plain reads the host swap is enough.


## 2026-08-19 — fetch: browser navigation headers, and the end of markdown negotiation

- **The 403 was about missing headers, not the User-Agent.** drive.com.au (Cloudflare) blocked
  `ko fetch` outright. Isolated it a header at a time on the live URL: our honest kotools UA
  plus the `Sec-Fetch-*` family and `Upgrade-Insecure-Requests` → 200; the same UA without them
  → 403; a **spoofed Chrome UA without them → still 403**, twice. So the wall keys on the
  absence of what a navigating browser sends, and claiming to be Chrome while sending nothing
  Chrome sends looks worse, not better. We keep the honest UA and just describe the request
  truthfully — it *is* a top-level document navigation. Free, instant, no new dependency, so
  it's the default rather than a fallback rung above Wayback.
- **Dropped `Accept: text/markdown`.** The premise was that agent-optimized servers hand back
  clean markdown. Measured across 6 sites: only 2 answer at all, and answering isn't
  extracting — Cloudflare's own blog is a wash (24k chars vs trafilatura's 19.7k) and
  drive.com.au is a whole-DOM dump, 31k tokens of nav, icons and a JSON-LD author blob against
  trafilatura's 4.6k of just the review. 6.6x the tokens for less of the article. The branch
  bought nothing measurable on the sites that hit it, so it's gone and every non-PDF page goes
  through trafilatura. Worth re-running as the practice matures — more sites will serve
  genuinely useful markdown — but today it's hit and miss, and the miss is expensive.
- **The ladder was misclassifying walls as dead links** — the real finding, surfaced by a
  second-opinion pass. `except httpx.HTTPError` sent *everything* to `_wayback` under a
  comment reading "the page is gone", but a 403 is the opposite of gone: the page is there
  and refusing us. The archive.today rung — JS-rendered capture, better on exactly this case
  — only fired on 200-with-empty-extraction. So 401/403/429 now route to `_paywall_fallback`
  (archive.today, then Wayback); 404/5xx/timeouts still go straight to Wayback. Strictly
  a superset of the old behaviour, and the documented successor for when the header bundle
  stops working.
- **Kept the markdown *response* pass-through.** Dropping the request header and dropping the
  response branch were one edit but two decisions; only the first was measured. A server can
  volunteer `text/markdown` unsolicited (llmstxt.org does), and trafilatura on a markdown body
  is length-dependent — the full 13.5k doc round-trips near-verbatim, but truncate it to 2.5k
  and `_extract` returns None, which would send a fine page down the paywall path.
- **What the bundle actually claims.** The first draft of this entry said we "don't lie about
  who we are" — true of the UA, false of `Sec-Fetch-User: ?1`, which asserts a click nobody
  made, and that turns out to be the one header you can't drop (three paired rounds:
  full set 200, minus that header 403, every time). The honest framing is that the wall asks
  whether the request carries the bundle a click-initiated top-level GET sends, not whether
  you're Chrome. Also noted as rot: `Sec-Fetch-Site: none` is replayed verbatim across
  redirects where a browser would flip it to same-origin/cross-site.
- Net on the URL that started this: 403 → 4,580 clean tokens. Three new tests cover the
  header bundle, the markdown pass-through, and refused-vs-gone; each was mutation-checked.

## 2026-08-10 — `--help` on a budget: one function, one line

- **The old principle caused the verbosity it was meant to prevent.** "Assume humans and
  agents skim `--help` and nothing else" reads as license to put *everything* in help, and
  `ko --help` had grown to 136 lines — past the point anyone skims it. Replaced the adjective
  with a budget, because a number is enforceable and "concise" isn't: a command docstring is
  one sentence plus one example, a flag is one line, and rationale/warts/measurements go to
  WORKLOG or `ko prompt` instead.
- **Visible aliases printed twice.** Stacked `@app.command()` registers one callback under
  two names, so the group listing rendered the whole docstring for each — `pdf`/`md-to-pdf`
  and `fetch`/`webfetch` burned ~14 lines carrying zero information, and `tt`/`todo` another
  five. Now the alias registers `hidden=True` and the primary's docstring names it
  (`Alias: \`ko webfetch\``); the alias still works when typed, it just stops competing for
  listing space. This walks back the 2026-07-28 "visible alias" call for `md-to-pdf` — the
  point was that an agent can *find* the ecosystem spelling, and one line saying so does that
  as well as a duplicate entry does. 136 → 103 lines.
- Same treatment on the `ko pdf` CLAUDE.md bullet (2,771 chars — the file's longest line),
  cutting the rationale that the 2026-07-28 entry below already records. CLAUDE.md loads into
  every session; the journal doesn't have to ride along.

## 2026-08-06 — Google service-account profiles + review-sweep fixes

- **SA profiles** — the deliberate exception to google_auth's "runs as you": `ko gsheets -a
  <name> auth --service-account <key.json>` copies the key to
  `~/.local/state/ko/google_sa_<name>.json` (0600, echoes only the client_email);
  `get_credentials` short-circuits to it, no browser/token. For automation and docs a human
  account can't hold — share the doc with the client_email. **State, not config**, though the
  first cut put it in config: config is the dotfile-synced dir, and unlike `google_client.json`
  (inert without a consent flow) an SA key is a standalone bearer credential — same class as the
  OAuth tokens already living in state. Written via `os.open(..., 0o600)`, since write-then-chmod
  leaves a secret world-readable for the length of the write.
  Drive calls now pass `supportsAllDrives` — but only on `files.*`: the discovery doc shows
  `export`/`comments`/`replies` have no such param, and googleapiclient rejects unknown kwargs.
- **Review sweep** (two fresh-context Opus reviewers over the recent llm/sum/fetch batch +
  pending pdf work): wikipedia routing is now host-parsed — the old `[a-z]{2,3}` regex captured
  the *tail* of long subdomains (`nds-nl` → the Dutch article, silently) and, unanchored,
  hijacked URLs merely containing a wikipedia link; mobile `en.m.` now routes. `ko sum`/`-f`'s
  URL heuristic requires a hostname shape, so a typo'd local path fails instantly instead of
  costing fetch+Wayback. `-f` content now really goes *before* the prompt (the docs said it,
  the code didn't). Smaller: decode errors exit 1 not 2, media URLs answering text/html fail
  loud, `-x` on an unclosed fence drops the opener, sum flushes each summary as computed,
  doctor shows `[llm] sum`, brand typos exit 2 listing `~/.config/ko/brands/`, and the pdf
  font scale is finished (code/TOC/lh-tag at 9pt under the 9.5pt body — output is no longer
  byte-identical with the ops script, which the 2026-07-28 entry claimed).
- **`ko websearch` / `ko webfetch`** — aliases (stacked `@app.command()`, one callback) for
  `ko exa search` / `ko fetch`. The whole ecosystem spells this capability WebSearch/web_search,
  and an agent arrives knowing that name; `exa` is right for the group, wrong as the only
  spelling. Aliases, not renames — the vendor name still tells you which index and whose bill.
- **The review round caught a bug I'd just added**: routing `.svg` off the multimodal path sent
  it to liteparse, which returns `''` for SVG — and an empty part is filtered out, so
  `ko llm -f diagram.svg` became a *paid call with the input silently missing*, the exact thing
  the fatal-on-unreadable rule exists to prevent. SVG now reads as text (it is text).
- **tests/conftest.py, first one in the repo** — chasing a hang found the suite could reach real
  Google OAuth, where `run_local_server()` blocks on a localhost socket forever: no failure, no
  name, just a dead run. Now `_run_flow` is stubbed to raise, which turned the hang into a named
  assertion in one run. It exposed the actual leak: `-a <name>` sets `KO_GOOGLE_ACCOUNT` in
  `os.environ` directly (right for a one-shot CLI), so one CliRunner invoke re-pointed every
  later test — including a live one — at the test account. Both that and the credential
  lru_caches are now isolated per test.

## 2026-07-28 — `ko pdf` (markdown → PDF), ported from ops

- **`ko pdf <file.md>`** — WeasyPrint renderer lifted from `ops/scripts/md_to_pdf.py`,
  CSS reused verbatim so output is byte-identical in text (verified by diffing a real
  4-page uts-tmos status doc rendered both ways). ko is the right home: it composes with
  `ko gmail draft new -a "$(ko pdf status.md)"`, which was the actual task that prompted it.
- **Fixed the pattern's two footguns rather than porting them.** The ops script needed
  `--brand path` on *every* call and printed a `yourbrand.dev` placeholder when you forgot
  — a trap documented in the tmos repo's own lab-doc skill. Here the brand resolves through
  ko's config chain (`--brand` > front-matter > `KO_BRAND` > `[pdf] brand`) and an
  unresolved one warns on stderr. Also dropped its hardcoded
  `h2#hosting-ownership { page-break-before }` rule — a per-document hack that broke
  silently whenever that heading was reworded — for a generic `<div class="page-break">`.
- **Left the resume stylesheet in ops.** ~90 lines of CSS for one document's layout isn't a
  generic renderer's job; `--bare` (typography only) + `--css FILE` (appended, so it wins)
  covers a bespoke look with the sheet living next to the CV. This split also made the CSS
  readable: BASE (typography) / DOC (numbering, letterhead, TOC, footer) / PAGE.
- **The native-deps problem, solved without asking the caller for anything.** WeasyPrint
  needs pango/cairo, which on macOS sit where dyld doesn't look, and dyld reads its search
  path at process *start* — so no in-process fix exists (ctypes preloading doesn't satisfy
  cffi's dlopen-by-leafname; tested). The ops script re-execs itself. Here the HTML is
  built in-process and handed to a short-lived child interpreter with
  `DYLD_FALLBACK_LIBRARY_PATH` set: one code path, no re-exec, ~0.35s, and no
  `export` line an agent could never add. `ko doctor` gained a row that says
  `brew install pango` when the libs are missing.
- Deps: weasyprint, pyyaml, mdit-py-plugins (footnotes), pygments. 26 tests, all offline
  except two end-to-end renders that skip if the native libs are absent — `build_html` is
  separated from the byte-writing precisely so the markup is testable without them.
- **Then reworked against Quarto's PDF docs + the WeasyPrint 69 notes** (both read, not
  skimmed):
  - **Named styles**, because "quickly make a PDF" and "client handover doc" want
    different furniture: `note` (default — typography + page footer), `report` (the ops
    look: letterhead, Contents, numbered sections), `bare`, plus any
    `~/.config/ko/pdf/<name>.css` layered on `note`. **Quarto's `toc` and
    `number-sections` are both opt-in**, which is the right call — a Contents block
    listing two headings is furniture, not navigation — so the default got lighter.
  - **Numbering is tied to the TOC** rather than separately switchable: the TOC prints
    "N." prefixes that must match the body's CSS counter, so splitting them is a way to
    render a document whose two halves disagree.
  - **Syntax highlighting** via pygments (`friendly` — light themes survive printing).
    Pandoc does 140 languages by default and we did none; the ops script even declared a
    pygments dep it never used. An unknown language falls back to a plain block.
  - **Tagged PDFs by default** (`pdf_tags=True`) — the structure a screen reader needs,
    which is most of what Quarto's accessibility section is about, and WeasyPrint 69
    specifically improved its validity. `--pdf-a` adds PDF/A-3b for archival (~8% bigger).
  - Named `ko pdf` with a visible `ko md-to-pdf` alias, following the house split where
    clarifying aliases (`ko todo`) show in help and terse shortcuts (`ko a`, `ko gd`) hide.
- **Then cut it back — seven flags to four.** Building the sample fixture was what exposed
  most of this; the rest was asking "did I add this because it was needed, or because I'd
  just read about it?"
  - `--pdf-a` (archival PDF/A-3b) and `--css` **gone**. The first was Quarto-envy for a
    property no ko document needs. The second was a second answer to "how do I add CSS"
    now that `~/.config/ko/pdf/<name>.css` exists — and a named style is reusable where a
    flag isn't. Tagged PDFs stay on: free, no surface.
  - `--toc/--no-toc` **gone** — wanting a Contents block means wanting the `report` look.
    `--letterhead/--no-letterhead` stays: rendering `report` *without* the letterhead is
    exactly how the tmos lab docs are produced, so it's a proven need rather than a
    symmetry.
  - Front-matter `brand:` and inline brand-key overrides **gone**. One identity, one
    brand.yml; `--brand` covers the rest.
  - `default_out`'s walk up to an enclosing `docs/` **gone**. It could write two levels
    from the source into a directory it created, and it was never exercised — the lab-doc
    skill passes `-o`. Now: a `pdf/` dir beside the input if one exists, else beside it.
  - **`linkify` and `typographer` were dead options** inherited from the ops script —
    both are silently inert on markdown-it's `commonmark` preset (verified, not assumed).
    Removed rather than switched on: typographer rewrites `--` to an en dash, and these
    documents are full of `--flags` in prose.
- **`docs/pdf-sample.md`** — one fixture exercising every element the renderer claims to
  handle (marks, nested lists, known + unknown code fences, tables, footnotes, a forced
  page break), rendered in every style by a parametrized test. A style that breaks on a
  table now fails there rather than in a document about to be sent.

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
