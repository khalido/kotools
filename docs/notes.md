# ko notes — a private, agent-managed notes repo

> **Status: planning (2026-06-27), not built.** The notes repo is a **separate git repo**, not this one. This doc plans the concept and how it relates to the memory store. Pair: `docs/memory.md`. There is no `ko notes` command.

## Overview

A private GitHub repo for my notes: the **agentic successor to my old Google-Drive notes**, used with AI (Claude Code, `ko ai`). It's structured like `~/code/labs`, a mix of:
- **plain markdown notes** at the root (`python-logging.md`, `favourite-python-libs.md`, …), and
- **full artifacts / mini-projects** in folders (a robot-arm build, a published site, a notebook).

Agents read, write and maintain it. An `AGENTS.md` at the root sets the conventions: how to file a note, when to append to `log.jsonl`, commit etiquette. The repo holds the full content. It's durable, inspectable, `git diff`-able, and mine.

## Notes vs memory — two things, one crossover

- **Notes = full content** (rich markdown + artifacts). The authoritative source.
- **Memory = tight 1–2 line summaries, vector-indexed** (the libSQL store in `memory.md`). The *search surface*.
- **Crossover = the memory DB.** Memory rows are summaries from **two** sources:
  - **Notes.** Each useful note becomes one or more rows: a 1–2 line summary plus a **pointer back to the full note** (`source = path/in/notes/repo.md`).
  - **Sessions.** The overnight distiller turns session learnings into rows that *aren't* notes (yet).

**Retrieval is progressive disclosure** (same pattern as `ko prompt` and skills). An agent searches memory and gets tight summaries, which are often enough. If not, the pointer says to open the full note. Memory is the catalog; the note is the document.

> Example — search `python`: memory returns
> `python logging — use loguru; structured JSONL, sinks make syncs trivial  [→ python-logging.md]` and
> `favourite python libs — httpx, typer, loguru, …  [→ favourite-python-libs.md]` (1–2 lines each).
> Open the full note only if the summary isn't enough.

## How ko relates (don't over-build a notes CLI)

The notes repo is mostly git plus agents working in it. ko's value is the **memory index over the notes**, not managing them:
- ko's **memory extractor** (`memory.md`) reads **both** the notes repo and ko sessions and builds the vector index. A row's `source` points to a note path *or* a session id.
- A thin `ko notes` (open / grep / new) might be worth it later. Resist building a notes manager: the repo, `AGENTS.md` and the memory surface are the system.

## Old → new

Old: static personal notes in Google Drive (aging, passive). New: a git repo agents actively maintain, with a vector memory surface over it. Migrate the Drive notes in as markdown where useful. No rush.
