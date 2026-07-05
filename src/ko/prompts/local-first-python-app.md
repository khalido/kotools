---
name: local-first-python-app
description: How I build a local-first privacy-first Python pipeline app — FastAPI + vendored htmx + LanceDB
---
# Local-first Python pipeline app — kickoff

How I build a data-processing app that runs entirely on the user's machine: a stage
pipeline ingesting data, storing it in LanceDB, and serving a local FastAPI + htmx UI.
Reference implementations: `~/code/thinker` (cleaner, well-documented) and
`~/code/jabberwocky` (battle-tested, larger scope).

## Why local-first

Data stays on the machine — the privacy story is a documented, enumerable host list. In
thinker that's four at runtime: MS Graph, MS login, Gemini (enrich stage only), and
pypi/astral at setup. Every other potential outbound call is reviewed against that list.
**Don't add a CDN or network dependency without updating the privacy boundary doc.**
This constraint drives every other technical decision: vendored assets, committed calendar
data instead of a yfinance network probe, LanceDB instead of a cloud store.

## No CDN at runtime — vendor everything

htmx 2, Alpine 3, and Tailwind CSS v4 live under `<package>/ui/static/vendor/`, pinned
in a `VERSIONS.txt` (`htmx.org @ 2.0.10`, `alpinejs @ 3.15.12`,
`@tailwindcss/browser @ 4.3.1` in thinker as of v0.x). Served from `/static/vendor/` —
no CDN tag at runtime. Don't re-add CDN script tags; they break the host-allowlist story.
Update by re-downloading the files and incrementing the version in `VERSIONS.txt`.

```html
<!-- in base.html <head> -->
<script src="/static/vendor/tailwindcss-browser.js"></script>
<script src="/static/vendor/htmx.min.js"></script>
<script defer src="/static/vendor/alpine.min.js"></script>  <!-- defer required -->
```

## FastAPI + Jinja2

Server-rendered; no JSON API for the operator UI. Full pages extend `base.html`; fragments
are `_name.html` in the same `templates/` directory. One critical signature:

**`TemplateResponse(request, "name.html", ctx)` — request first.**

Starlette ≥0.29 made `request` the first positional arg. The old `(name, ctx)` form still
appears in most tutorials and fails deep in Jinja's cache — don't cargo-cult it.

Keep endpoint logic thin; business logic stays in the domain module.

## htmx + Alpine interaction gotcha

An htmx swap **destroys all DOM state inside the swapped subtree** — Alpine `x-data`,
open `<details>`, focus, scroll position. Rule: **keep Alpine-stateful components and
`<details>` OUTSIDE any htmx swap target**. The live-progress console in thinker is its
own polled `<div>`; nothing stateful lives inside it. If state preservation across a swap
is truly required, the idiomorph swap extension handles it.

**Use polling, not SSE, for local long-running jobs.** SSE has footguns on local servers
(connection leaks, GZip breaks the stream, disconnect detection). Poll a status endpoint
every 2s; the response fragment self-terminates by omitting `hx-trigger` once the job
is done — no client-side cleanup needed.

## Stage pipeline

```
fetch → parse → enrich → sift → output
```

Each stage is its own file (`fetch.py`, `parse.py`, `enrich.py`, `sift/`). Stages are
**idempotent and resumable**: fetch uses a manifest that skips completed past-day files
(past-day data can't change; today tops up on the next run); parse and enrich use
`db.upsert` which calls `merge_insert("id")` under the hood — re-running is safe. A
crash mid-day means only that day re-runs; completed days keep their output.

`config.py` is the **side-effect-free "where does X live" index**: all paths, run
constants, and config-precedence logic. `import thinker.config` must never create
directories or open files — dirs are created by `config.ensure_dirs()` at CLI/web startup
only. This makes it safe to import in tests and tooling.

## LanceDB

Three things to know:

1. **Naive datetimes.** LanceDB strips `tzinfo` on read. Strip it before any comparison:
   `as_of.replace(tzinfo=None)`. Without this a tz-aware datetime silently miscompares
   against a naive stored datetime — no error, wrong results.

2. **Upsert is `merge_insert("id")`.**
   ```python
   (table.merge_insert("id")
         .when_matched_update_all()
         .when_not_matched_insert_all()
         .execute(rows))
   ```
   Re-running parse or enrich is idempotent — same `id` overwrites, new rows append.

3. **`.to_dicts()` returns Python `datetime` objects.** Use `.strftime("%Y-%m-%d")`, not
   `str(dt)[:10]` — the string repr isn't stable.

Never hardcode the DB path; always use the path constant from `config`.

## Config split + precedence

Four layers (from highest to lowest):

```
env var  >  data/settings.json (UI-entered overrides)  >  deployment.toml (committed defaults)
```

Non-secret identifiers (tenant id, client id, service address) go in the committed
`deployment.toml`. Secrets (client secret, API keys) go in `.env` (gitignored). Operator
self-service via UI form submissions goes to `data/settings.json` — survives `git pull`.
`update_env_secrets` in `config.py` writes `.env` and immediately calls `chmod(0o600)` —
owner-only. A curated model catalog lives in a committed `models.toml`; model *choices*
persist in `data/settings.json`. Never commit a secret.

## Privacy boundary

Document which hosts get called, then enforce it. Enforce the gate at the **query layer**,
not the output layer: `research.get_recent_research` in thinker filters to
`email_type == "broker_research"` before anything enters the sift stage — a personal or
newsletter email can't reach the deliverable even if it slips past enrichment. Content
filters belong on the way IN, not on the way OUT.

## PIT guard (point-in-time)

A signal for day T must derive only from data received by T's run moment.
The canonical boundary: **`received_at <= as_of`**, where `as_of = T 02:30 UTC` (8 AM IST,
`pit.as_of_for_date`). Implement `as_of` as **required and keyword-only** so it can't
be forgotten or positionally defaulted:

```python
def get_recent_research(*, as_of: datetime, ...) -> list[dict]:
    ...
    df = df.filter(pl.col("received_at") <= as_of.replace(tzinfo=None))
```

**Named landmine from both thinker and jabberwocky:** if price scoring ports in later,
`score_cutoff = T-1 23:59:59 UTC` — a different value from `as_of`. Do NOT reuse `as_of`
for it. Conflating them leaks tomorrow's close into today's signal. Bring `score_cutoff_for`
as a separate function; the jabberwocky version lives in `lib/pit.py`.

## CHANGELOG

[Keep a Changelog 2.0](https://keepachangelog.com/en/2.0.0/) + SemVer. Hand-curated from
user-facing changes, not generated from commits. Add notable changes under `[Unreleased]`
as you go; on release, retitle to `[x.y.z] - DATE`, bump `__version__`, tag `vx.y.z`, add
a fresh `[Unreleased]`. Lives on disk and GitHub; read it before re-running after `git pull`.

## Reference repos
- `~/code/thinker` — the cleaner reference. Read `CLAUDE.md`, `config.py`, `docs/ui.md`,
  `db.py`, `research.py`, `pit.py` for every pattern above.
- `~/code/jabberwocky` — the battle-tested source. `CLAUDE.md` → "Agent Learnings" for
  hard-won LanceDB/PIT/agent gotchas; `lib/research.py` + `lib/pit.py` for the full
  timing model including `score_cutoff_for`.
