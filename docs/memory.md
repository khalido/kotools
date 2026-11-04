# ko memory — a personal, owned, cross-agent memory store

> **Status.** Part 1, per-agent `memory.md`, is **built** (shipped 2026-07-11, `src/ko/agents/_memory.py`). Part 2, the cross-agent store (libSQL index, `ko memory`, MCP server, overnight distiller), is **planned, not built** (2026-06-27). It is a separable component, designed so it could graduate to its own repo. Research is at the bottom.

**Principle: own the memory layer.** The learning is the point, and I reach for this tool when I need it. Outsourcing to an all-in-one (mem0, Zep, Letta) puts it out of my hands. So I build a fraction of it on a solid DB backend and steal the patterns, not the dependency.

## Part 1: per-agent memory.md (built)

Live-verified on 2026-07-11: the repo agent appended to its memory, then a fresh run answered from the injection alone (zero tool calls, $0.0003). I researched it against thinker's SiftWorkspace, pydantic-deepagents and hermes-agent (all in refs). `memory.md` is what an agent holds in its head: working context plus accumulated judgment. The vector store in part 2 is searchable external memory. They complement each other; I built the cheap one first.

- **Two tiers.** Per-agent `~/.local/state/ko/memory/<agent>/memory.md` holds machine-local learned knowledge (state dir; sessions remain the source of truth). One shared `~/.config/ko/memory.md` holds user preferences and standing context (dotfile-synced; it is ideas.md's `context.md`, made agent-readable and hand-edited). Shared memory injects into every agent first, then the agent's own. The `ai`, `repo`, `research` and `tv` agents carry memory.
- **Injection.** An `@agent.instructions` function, the same mechanism `repo.py` uses for refs/CLAUDE.md. It caps the head at 200 lines and adds a truncation pointer (thinker). It also supports deepagents' **pin marker** (`<!-- ko:pin-end -->`): notes above the pin always survive, and the tail below is recency-truncated. The string is the same on every step, so it is prefix-cache-friendly.
- **Write tools** (`agents/_memory.py`, four): `read_note`; `append_memory` (append-only, safer than overwrite); `write_note` (the anchor `memory.md` is protected from overwrite); `edit_note` (thinker's find-and-replace: `old_string` must occur exactly once, else `ModelRetry`). They are scoped to the agent's own memory dir and `.md` files. The general files toolset stays read-only by construction.
- **Curation lives in instructions, not code.** Thinker ran this for 8 months of real memories with no bloat (22-52 lines per agent). The rules: update slowly (only items likely to recur), bump dates and counts in place, delete stale entries, stay under ~100 lines. Shared memory gets different rules: durable user-style facts only, no topic findings.
- **Relation to part 2.** Skip libSQL, vectors, MCP, the distiller and decay for now. When the distiller exists, it reads `memory.md` files as one source. refs/CLAUDE.md is already the repo agent's domain memory. The dogfood run measured ~2x token cost exploring a repo missing from that map, which is the strongest evidence that injection pays for itself.

## Part 2: cross-agent store (planned)

### Overview

One cheap memory store shared by every agent: Claude Code, `ko ai`, a fleet box, on my PC or anywhere. It holds **tight 1-2 line summaries** (facts, decisions, preferences, learnings). It is the search *surface*, not the full content. **Retrieval is deterministic**: vector search, no LLM in the read path.

A memory row is a summary derived from one of two sources, and the depth lives elsewhere (see `docs/notes.md`). Either it comes from my **notes repo**, pointing back to the full note, or from a **session**, via the overnight distiller. So memory is the catalog and the notes repo is the depth. A row's `source` is a note path or a session id. This is progressive disclosure, the same as `ko prompt` and skills.

Three pieces, each reusing something ko already has:

- **Store.** A libSQL (Turso) single file with native vectors. Local-first; an *embedded replica* auto-syncs to a central hosted DB. Local-cheap and reachable everywhere, with the same SQL in both modes.
- **Access.** `ko memory search/add` (deterministic, cheap), also wrapped as an MCP server, so one `mcp.json` line gives every agent the same memory. It reuses ko's MCP registry and client work.
- **Population.** An overnight `ko ai` skill distills sessions into typed facts. It is a curated projection of the session log (the "log is the agent" projection rule), not a dump.

### Source of truth: markdown in git; libSQL is a projection

Refined 2026-06-27, from a Gemini sketch. The human-readable markdown in a private git repo is the source of truth. The libSQL vector DB is a *rebuildable index* over it, not the store. Why:

- It is the projection rule: markdown is truth, the vector DB is disposable, and a rebuild from markdown works anytime.
- It is inspectable and hand-editable. I can read, `git diff` and edit memory as plain markdown.
- Git is free, versioned, multi-machine sync of the source, and it survives DB loss.
- The markdown source *is* the **notes repo** (`docs/notes.md`): full notes and artifacts, `labs`-style. Memory indexes summaries of it plus sessions. (Claude Code's `MEMORY.md` is a lightweight cousin and could live in the same repo.)

**Sync is simpler than syncing both.** Git syncs the source. Each machine rebuilds its own local libSQL from the markdown; re-embedding a few hundred curated facts with a small local model is cheap. A central Turso (or the edge-mcp MCP server) is *optional*, only for *remote* agents (fleet, edge) that query without running an embedder. Turso is a remote-query convenience, not the sync backbone.

**Workspace anchor files.** ko already uses the `AGENTS.md` pattern; this points it at the notes repo.
- `AGENTS.md` in the notes root holds the protocol: read and append markdown in-domain; on a novel fact or decision, append one line to `log.jsonl`, then commit.
- `log.jsonl` is append-only, one `{ts, agent, action, file, summary, vector_dirty}` per change. The real-time loop writes markdown plus this line (zero latency, **no embedding mid-flight**). The overnight loop scans for `vector_dirty:true` and re-embeds just those (incremental re-index). It overlaps git history a little; the `vector_dirty` flag and the cheap-scan summary are the added value.

Embeddings stay **small and local**: 384-dim int8 (MiniLM), not 1536-dim float32 (~16x the storage, plus a paid API call per fact). Bump only if retrieval visibly misses.

### Backend: Turso / libSQL

- **Turso / libSQL (chosen).** One file does storage and vectors. The **embedded replica** is a local DB that syncs overnight to a central hosted DB, exactly the model I want. It is SQLite-native (familiar SQL; the planned `ko q` could analyze it). Turso's own AI-memory guide ships the exact schema and decay SQL. **`memelord`** is a reference implementation: an MCP server plus hooks with reinforcement-style weight decay. Free tier to start; the $5/mo plan covers sync and scale when it goes multi-machine.
- **Cloudflare (not chosen).** D1 + Vectorize + Workers AI behind a Worker on `edge-mcp.khalido.dev`. It works (several `*-memory-mcp` projects do it), it is edge-native, it is already in my stack, and Workers AI gives free edge embeddings. But it needs two *strictly separate* stores kept in sync (D1 for rows, Vectorize for vectors), and it is **cloud-only, with no local-first replica**. That loses the "local DB synced overnight" property. Use it only if I later want pure edge with no Turso.
- **mem0 / Zep / Letta (no).** Managed all-in-one layers, out of my hands. Steal the patterns (below).

### Schema

From Turso's guide, adapted and deliberately minimal.

```sql
CREATE TABLE memories (
  id          TEXT PRIMARY KEY,
  content     TEXT NOT NULL,        -- one atomic fact per row
  embedding   F8_BLOB(384),         -- int8-quantized 384-dim (e.g. all-MiniLM-L6-v2); ~75% smaller than f32
  type        TEXT,                 -- fact | decision | preference | correction | insight
  tags        TEXT,                 -- JSON array
  source      TEXT,                 -- session/run it came from
  created_at  INTEGER,
  last_used   INTEGER,              -- drives decay
  use_count   INTEGER DEFAULT 0
);
```

Retrieval needs no vector index at personal scale. A full scan with `ORDER BY distance LIMIT k` is fast:

```sql
SELECT id, content, type, vector_distance_cos(embedding, vector8(?)) AS dist
FROM memories WHERE embedding IS NOT NULL
ORDER BY dist ASC LIMIT ?;
```

### Read: deterministic and cheap

`ko memory search "<q>"` makes one embedding call (cheap model, Workers AI or local MiniLM), takes the vector top-k, and returns facts. No agent loop, no LLM reasoning. `ko memory add "<fact>" [--type --tags]` writes manually. Both are exposed as an **MCP server** (`memory_search`, `memory_add`), so every agent gets them via `mcp.json`.

### Write: overnight, ADD-only

A nightly `ko ai` skill reads new sessions and distills **atomic typed facts**. It uses mem0's **ADD-only, one-LLM-call-per-add** pattern: accumulate, skip UPDATE/DELETE logic, let decay do the pruning. It runs a small, cheap model (cf. LightMem), then embeds and inserts. It ingests **only my own sessions and work**, never raw external content as a "fact" (poisoning; see MemoryGraft).

### Decay

Forgetting is a feature (SuperLocalMemory). On retrieval, bump `last_used` and `use_count`. A periodic GC computes weight ≈ `use_count * POWER(0.95, days_since_last_used)` and prunes low-weight and never-retrieved rows. That keeps the store a small curated set, not a junk drawer. **Curation beats vector tech** (the "memory fallacy" critique).

### Build order

1. **Markdown notes repo plus `ko memory add` / `search`.** `add` appends a markdown fact and a `log.jsonl` line. `search` queries a local libSQL index rebuilt from the markdown. Prove that retrieval is useful.
2. **Incremental re-index.** Scan `log.jsonl` for `vector_dirty`, re-embed just those, add decay and GC.
3. **Expose as an MCP server.** Claude Code and `ko ai` then share one memory and the same markdown repo.
4. **Overnight session-distiller skill** (ADD-only, small model). It writes markdown facts, not DB rows.
5. **Central Turso / edge-mcp**, only when a *remote* agent (fleet, edge) must query without an embedder.

Skip for v1: knowledge graph (Zep, MAGMA), temporal fact-validity, UPDATE/merge logic. Add them only if the simple version visibly underdelivers.

---

## Research (2026-06-27)

Gathered by dogfooding `ko exa`, `ko hn` and `ko hf`.

### Reference implementation: Turso AI memory and `memelord`
- Turso's [AI memory guide](https://docs.turso.tech/guides/ai-memory) uses two tables (`memories`, `tasks`) and `F8_BLOB(384)` int8-quantized vectors. Search is `vector_distance_cos(embedding, vector8(?))`. A **full table scan is fast enough at per-project scale** (no ANN index), and int8 cuts storage 75%. Embeddings come from an external model (`all-MiniLM-L6-v2`). Decay is left as experiments: EMA weights, `POWER(0.95, days_since_last_retrieved)` time decay, GC of low-weight and never-retrieved rows.
- **`memelord`** is an MCP server plus hooks that implement the pattern with reinforcement-style weight decay for coding agents. It is essentially the shape I want. Read it before building.

### Cloudflare option (edge-native, two stores)
- Native CF memory-MCP servers exist: `MrSnowGlobe/cf-memory-mcp` (D1 + Vectorize + KV + Workers AI), `Avik-creator/mcp-memory-cloudflare`, `jmbish04/mcp-memory-v3`. See the [Vectorize + Workers AI embeddings tutorial](https://developers.cloudflare.com/vectorize/get-started/embeddings/).
- The honest write-up: ["challenges of a semantic memory layer on Workers + D1 + Vectorize"](https://dev.to/rahil_pirani_c48446facc8c/the-challenges-of-creating-a-semantic-memory-layer-on-cloudflare-workers-d1-and-vectorize-3c7a). It means **two strictly separate stores** (D1 rows, Vectorize vectors) that you must keep consistent. It works on the free tier. Edge-native, but **no local-first replica**.

### Managed layers: patterns, not the dependency
- **mem0** ([arxiv 2504.19413](https://arxiv.org/html/2504.19413)): two phases (extraction, update) and a three-step loop (retrieve, enrich, store). **OSS v3 is ADD-only**: one LLM call per add, accumulate, no UPDATE/DELETE, multi-signal hybrid retrieval. This is the write pattern to copy.
- **Zep** (Graphiti): temporal knowledge graph with fact-validity over time. **Letta** (MemGPT): tiered, self-editing memory. Both are heavier than v1 needs.

### HF papers (decay, extraction, typing, security)
- **SuperLocalMemory V3.3** (2604.04514): local-first, **zero-LLM retrieval** (validates the deterministic read) and **biologically-inspired forgetting**.
- **LightMem** (2604.07798): memory extraction with **small language models** (cheap write path).
- **Memanto** (2604.22085): **typed** semantic memory with information-theoretic retrieval (store high-information items; the `type` field earns its place).
- **MemoryGraft** (2512.16962): a **poisoned-experience-retrieval attack**. Malicious "memories" cause persistent behavioral drift. So ingest only what is distilled from my own work, and treat memory like skills (read before trust).
- **Zep: A Temporal Knowledge Graph Architecture** (2501.13956), the paper behind the product.

### Skeptic
- ["The private agent memory fallacy"](https://blog.getzep.com/the-ai-memory-wallet-fallacy/): naive vector memory underdelivers (missed retrieval, stale junk). The lesson baked into the plan: **small, curated, decaying** beats a big vector dump.
