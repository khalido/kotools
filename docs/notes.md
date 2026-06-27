# ko notes — a private, agent-managed notes repo

> **Status: planning (2026-06-27).** The notes repo is a **separate git repo** (not this one); this doc
> plans the concept + how it relates to the memory store. Pair: `docs/memory.md`.

## Overview

A private GitHub repo for my notes — the **agentic successor to my old Google-Drive notes**, used in
conjunction with AI (Claude Code, `ko ai`). Structured like `~/code/labs`: a **mixture** of
- **plain markdown notes** at the root (`python-logging.md`, `favourite-python-libs.md`, …), and
- **full artifacts / mini-projects** in folders (a robot-arm build, a published site, a notebook).

Agent-managed: agents read, write, and maintain it; an `AGENTS.md` at the root sets the conventions
(how to file a note, when to append to `log.jsonl`, commit etiquette). **This is the full content — the
depth.** Durable, inspectable, `git diff`-able, mine.

## Notes vs memory — two things, one crossover

- **Notes = full content** (rich markdown + artifacts). The authoritative source.
- **Memory = tight 1–2 line summaries, vector-indexed** (the libSQL store in `memory.md`). The *search surface*.
- **Crossover = the memory DB.** Memory rows are summaries sourced from **two** places:
  - **Notes** — each useful note → one or more memory rows: a 1–2 line summary + a **pointer back to the
    full note** (`source = path/in/notes/repo.md`).
  - **Sessions** — the overnight distiller turns session learnings into memory rows that *aren't* notes (yet).

**Retrieval = progressive disclosure** (same pattern as `ko prompt` / skills): an agent searches memory →
gets tight summaries → that's *often enough*; if not, the pointer says **"open the full version"** → load
the full note. Memory is the catalog; the note is the document.

> Example — search `python`: memory returns
> `python logging — use loguru; structured JSONL, sinks make syncs trivial  [→ python-logging.md]` and
> `favourite python libs — httpx, typer, loguru, …  [→ favourite-python-libs.md]` (1–2 lines each).
> Open the full note only if the summary isn't enough.

## How ko relates (don't over-build a notes CLI)

The notes repo is mostly **just git + agents working in it** — ko's value isn't managing the notes, it's
the **memory index over them**:
- ko's **memory extractor** (`memory.md`) reads **both** the notes repo and ko sessions → builds the
  vector index. A memory row's `source` points to a note path *or* a session id.
- A thin `ko notes` (open / grep / new) *might* be worth it later, but resist building a notes manager —
  the repo + `AGENTS.md` + the memory surface is the system. The notes themselves are just files.

## Old → new

Old: static personal notes in Google Drive (aging, passive). New: a git repo agents actively maintain,
with a vector memory surface over it. Migrate the Drive notes in as markdown where useful — no rush.
