# ko

Ko's personal opinionated CLI. **The vision:** for each thing I do often, research the best library or API once, bake it in with the defaults I'd have picked myself, expose 2–3 flags max — then neither I nor my agents ever think about it again. Every command serves two callers: me at a shell, and AI agents over bash. Layer-1 tools stay deterministic, cheap, and safe for an agent to call blind; intelligence is opt-in and always explicit (`ko llm`, `ko ai` — a typo'd subcommand must never silently spend tokens).

This file is a stub, not a manual — same rule as agent-browser/herdr: **the installed binary is the authority.** What `--help` can say, lives in `--help`. What changes between versions lives nowhere static. This file keeps only principles and the things `--help` cannot tell you.

**Read [docs/handover.md](docs/handover.md) first**: where the last session left off and what to do next. Keep it current; rewrite it clean, never append.

## Where truth lives
- `ko <cmd> --help` — the contract for every command and flag (`-h` everywhere; trailing `help` routes to group help)
- `AGENTS.md` — output/exit-code/`--json` conventions, bare-arg shortcuts; the agent-facing intro
- `WORKLOG.md` — rationale, measurements, rejected alternatives for every decision below
- `docs/` — design docs. Vision-bearing ones: `ideas.md` (roadmap + thesis), `assistant.md` (always-on personal assistant replacing yaad), `memory.md` (cross-agent memory plan), `remote.md` + `fleet.md` (remote boxes / sandboxes for agent-written code), `pydantic-ai.md` (accumulated pydantic-ai knowledge + gotchas)

## Commands (one line each — details in `--help`)
| Command | What |
|---|---|
| `exa search\|get` (`websearch`/`webfetch`) | semantic web search + URL→markdown |
| `fetch <url>...` | URL→markdown, deterministic routing (arxiv/yt/pdf/doi/reddit/wikipedia/walls→archives); alias `webfetch` |
| `sum [url\|file...]` | fetch + summarize in one move; stdin-aware |
| `llm "<prompt>"` | one-shot LLM; `-f` attaches anything, `-x` extracts code, `--schema` constrains JSON |
| `decide "<question>"` (alias `judge`) | typed answer with probabilities (Jev): yes/no, `--choice`, `--score`, `--tags`; guide `ko prompt decide` |
| `models [info <name>...]` | model strings usable with `-m`; `info` = newest matching gateway model: price, hosts' speed, top HN threads + best comment (no LLM); several names = one comparison table |
| `pkg info <name>...` | npm/PyPI package: latest + date (and what the release-age floor picks), version in this repo, advisories, stars, last push, release notes head (deps.dev + registry + GitHub, no key) |
| `doc <file>` | PDF/Office/image → text (liteparse); pages with no text layer OCR'd via `ocr` (DeepSeek on the gateway; stops first over 50 pages or $0.25; `-e vision` free on a Mac) |
| `ocr serve` | liteparse OCR server on 127.0.0.1:8828 for other projects (`--ocr-server-url`) |
| `pdf <file.md>` (`md-to-pdf`) | markdown → print-ready PDF; styles + brand.yml |
| `gsheets ...` | Sheets read+write, OAuth, learned name aliases |
| `gdocs ...` (`gd`) | Markdown ↔ Google Docs, tabs, comments |
| `drive ls\|pull\|push` | mirror a Drive folder locally, push edits back in place; manifest-based conflict detection |
| `cal ...` | agenda merged across all Google accounts; `cal use` sets where `cal add` lands (apart from the mail default) |
| `google auth\|use\|accounts\|rename\|logout` | the one way to sign in / pick Google accounts (short tags, `-a <tag>`) |
| `gmail ...` | thread-first read + attachments + drafts; replies go to a thread (send opt-in, gated) |
| `yt <url>\|info\|channel` | transcript (free, cached); `-s` Gemini watches + writes up (cached); batch `info` with transcript excerpt; `channel --since`, Shorts out unless `--shorts` (`GOOGLE_CLOUD_API_KEY`) |
| `hn` / `hf` / `papers` | HN · HF papers · OpenAlex literature scouting |
| `x search\|list\|user\|lists` | X search/lists/timelines (pay-per-read) |
| `tt ...` (`todo`) | TickTick read+write |
| `tv <title>` | movie/TV + AU watch providers |
| `brief [--raw]` | morning brief: gather + one cheap-model synthesis |
| `publish ...` | folder → Cloudflare Worker (`new`/`preview`/`rm`/`list`) |
| `ai "<prompt>"` | default agent, all toolsets + memory, basic tier (`-m` for hard questions) |
| `agent research\|repo\|tv\|sessions` (`a`) | specialist agents, resumable sessions |
| `mcp inspect\|call\|overview\|servers` | investigate/use MCP servers |
| `refs [pull]\|setup\|add\|list` | manage ~/code reference clones |
| `prompt [name]` | my kickoff briefs, by name |
| `logs` / `billing` / `doctor` | local logs · spend · setup status |

## Principles
- **Opinionated, not generic.** Defaults match my habits. Do what I actually do, the way I do it.
- **Fail loud.** No silent clamping, rounding, or fall-through — a wrong default or a missing capability surfaces as a clean error naming the fix (`ko doctor` lists them all).
- **Thin wrappers.** One self-contained module per domain over one upstream SDK; parameter names match the SDK.
- **Help lines carry their weight — on a budget.** Docstring = one sentence (+one example if it composes). A flag needing a paragraph means the flag is wrong. Rationale goes to WORKLOG, never `--help`. `ko --help` must stay skimmable; visible aliases noted on one line, registered `hidden=True`.
- **stdout = data, stderr = everything else.** Plain text/TSV default, `--json` when structure matters, errors as `{error, code}` under `--json`, exit codes 0/1/2 (empty results are success).
- **One file per domain.** No shared `utils/` until three callers exist.

## Stack
Python 3.15, `uv`, `ruff`, `pytest`, `typer`. Module-local dataclasses; `@lru_cache` singletons. Lint with `ruff check`. Don't run `ruff format`: the repo isn't formatted with it, so one run rewrites 43 files (adopting it is an open call, `docs/handover.md`).

## Adding a subcommand
1. `src/ko/<thing>.py` — thin SDK wrapper + dataclass results
2. `cli.py` — Typer subapp, every flag with `help=`. Use the shared helpers in `_cli_shared.py`: a caught exception exits through `_die_from(e, as_json=...)` (missing key → `config`, Google token → `auth`, our bugs re-raise), `--json` output through `_emit_json`. A gate (send, spend, delete) lives in the module function, not only in the CLI. Cache and state files are written with `dirs.write_atomic`.
3. `tests/test_<thing>.py` — smoke test; skip live calls without keys
4. Deps into `[project].dependencies`

Releasing: `/release` repo skill — CalVer, CHANGELOG canonical, human approves tag/publish.

## Things `--help` can't tell you
- **Google auth:** one shared OAuth token per account across gsheets/gdocs/cal/gmail; accounts are short tags, default set by `ko google use` (state/settings.json, per machine — commands never rewrite config.toml). Personal Gmail + External app in Testing = refresh token dies in 7 days — publish the app or use Workspace-Internal. Service-account profiles (`auth --service-account key.json`), multi-account `-a`, token paths — all in `google_auth.py` + README. Scope is per-API, never per-folder; Drive is the full `drive` scope since 2026-09 (per-folder = a service account).
- **Models** (`models.py`, ko's twin of everx's `ai-models.ts`): a `ModelSpec` per tested model (note, gateway host order, pydantic-ai settings) and the basic/medium/smart price tiers, all via Vercel AI Gateway. Every call goes through `models.resolve()`; nothing else builds a model; override in config.toml `[llm]`. OpenRouter stays only for Jev (`ko decide`). Re-check prices whenever touching the tier table — they drift fast. Every LLM call prints a cost note to stderr (gateway calls: estimated from the cached catalog price).
- **Directories** (`dirs.py`): config (`~/.config/ko`, dotfile-synced, keys fall back to `[keys]` in config.toml) · state (`~/.local/state/ko`, tokens/sessions/memory, never synced) · cache (disposable). Env overrides `KO_*_DIR`.
- **Keys:** modules call `config.require_key(name, hint)` → one `MissingKeyError` type; `ko doctor` reports the lot.

## Agents (`src/ko/agents/`)
- **Tools declared once** as FunctionToolsets in `_toolsets.py` (web, papers, news, tmdb, files…); agents are ~15-50-line compositions. Docstring-as-contract: a tool docstring states every silent filtering default — the agent-side twin of "--help is the contract".
- Resilient tools wrap calls in `_try` (error note, not raise) except programming errors and `MissingKeyError` — an unset key must be learned, never routed around silently.
- Sessions: full trace dumped per turn to state; resume `-r`; SQLite index is a rebuildable projection, the JSON log is truth ("the log is the agent").
- Memory: per-agent md workspace, head-anchored `memory.md`; shared user context at `~/.config/ko/memory.md`. Full plan: `docs/memory.md`.
- **Toolset eval method** (house method, after any tool change): run the agent on 1-2 real questions, have a fresh subagent READ THE SESSION TRACES for flailing/invented params, then an adversarial code review. Traces find ergonomics, review finds crashes. The tools' heaviest user is the model — let it review them.

## Not yet
MCP server scaffolded, unwired (`mcp_server.py`) — build only if a consumer appears. Candidate list + priorities: `docs/ideas.md`.
