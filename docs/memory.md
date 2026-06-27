# ko memory — a personal, owned, cross-agent memory store

> **Status: planning (2026-06-27), not built.** A *separable component* — designed so it could later
> graduate to its own GitHub repo. Companion research at the bottom.
>
> **Principle: own the memory layer.** The learning is the point, and it's a tool I reach for when I
> need it. Outsourcing to an all-in-one (mem0 / Zep / Letta) puts it out of my hands. So: build a
> *fraction* on a solid DB backend, steal the patterns, not the dependency.

## Overview

One cheap, **cross-agent** memory store. Any agent — Claude Code, `ko ai`, a fleet box, on my PC or
wherever — reads/writes the *same* space. It holds **distilled useful things** (facts, decisions,
preferences, learnings), not raw sessions. **Retrieval is deterministic** — vector search, no LLM in
the read path. It's **populated overnight** by an agent that distills my sessions into facts.

Three pieces that click together (each reuses something ko already has):

- **Store** — libSQL (Turso) single file + native vector. Local-first; an *embedded replica*
  auto-syncs to a central hosted DB → local-cheap **and** accessible-everywhere, same SQL both modes.
- **Access** — `ko memory search/add` (deterministic, cheap), *also wrapped as an MCP server* → one
  `mcp.json` line and every agent shares it. (Reuses this repo's MCP registry/client work.)
- **Population** — an overnight `ko ai` skill distills sessions → typed facts. A *curated projection*
  of the session log (the "log is the agent" projection rule), not a dump.

## Plan

### Source of truth: markdown-in-git; libSQL is a projection (refinement 2026-06-27, from a Gemini sketch)

**The inversion that improves the plan:** the **human-readable markdown in a private git repo is the
source of truth; the libSQL vector DB is a *rebuildable index* over it** (not the store). Why it's the
right call here:
- It *is* the projection rule (markdown = truth, vector DB = disposable; rebuild from markdown anytime).
- **Inspectable + hand-editable** — read / `git diff` / edit memory as plain markdown. Maximum "own it".
- **Git is the durable, multi-machine sync of the source** — free, versioned, survives DB loss.
- **Unifies with Claude Code's `MEMORY.md`** (already markdown-in-a-folder) → ko's memory and Claude's
  can be the *same* git repo; ko just adds the vector index both read.

**Sync — simpler than syncing both:** **git syncs the source**; each machine **rebuilds its own local
libSQL** from the markdown (re-embedding a few hundred curated facts with a small local model is cheap).
A **central Turso (or the edge-mcp MCP server) is *optional* — only for *remote* agents** (fleet/edge)
that query without running an embedder. So Turso is a remote-query convenience, not the sync backbone.

**Workspace anchor files** (ko already uses the `AGENTS.md` pattern — same idea, pointed at the notes repo):
- `AGENTS.md` in the notes root — the protocol: read/append markdown in-domain, and on a novel
  fact/decision append one line to `log.jsonl`, then commit.
- `log.jsonl` — append-only `{ts, agent, action, file, summary, vector_dirty}` per change. The real-time
  loop writes markdown + this line (zero latency, **no embedding mid-flight**); the overnight loop scans
  for `vector_dirty:true` and **re-embeds just those** (incremental re-index). (Overlaps git history a
  little; the `vector_dirty` flag + cheap-scan summary are the value-add.)

Embeddings stay **small + local**: 384-dim int8 (MiniLM), *not* 1536-dim float32 (~16× storage + a paid
API call per fact) — bump only if retrieval visibly misses.

### Backend decision: Turso / libSQL  (over Cloudflare, over the managed layers)

- **Turso / libSQL ✓ (chosen).** One file does storage *and* vector; **embedded replica** = local DB
  that syncs overnight to a central hosted DB (exactly the model I want); SQLite-native (familiar SQL,
  `ko q` can analyze it). Turso's own AI-memory guide ships the exact schema + decay SQL, and
  **`memelord`** is a reference impl: an MCP server + hooks with reinforcement-style weight decay.
  Free tier to start; the **$5/mo plan** covers sync/scale when it goes multi-machine.
- **Cloudflare (alternative, not chosen).** D1 + Vectorize + Workers AI behind a Worker on
  `edge-mcp.khalido.dev` — works (several `*-memory-mcp` projects do it), edge-native, already in my
  stack, Workers AI gives edge embeddings for free. **But:** two *strictly separate* stores to keep in
  sync (D1 for rows, Vectorize for vectors), and **cloud-only — no local-first / embedded replica.**
  That loses the "local DB synced overnight" property. Use only if I later want pure-edge / no Turso.
- **mem0 / Zep / Letta (no).** Managed all-in-one memory layers — out of my hands. *Steal the patterns
  (below), not the dependency.*

### Schema (from Turso's guide, adapted — deliberately minimal)

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

Retrieval — no vector index needed at personal scale; a full scan + `ORDER BY distance LIMIT k` is fast:

```sql
SELECT id, content, type, vector_distance_cos(embedding, vector8(?)) AS dist
FROM memories WHERE embedding IS NOT NULL
ORDER BY dist ASC LIMIT ?;
```

### Read — deterministic + cheap
`ko memory search "<q>"` = one embedding call (cheap model / Workers AI / local MiniLM) → vector top-k
→ facts. No agent loop, no LLM reasoning. `ko memory add "<fact>" [--type --tags]` for manual writes.
Both exposed as an **MCP server** (`memory_search` / `memory_add`) so every agent gets it via `mcp.json`.

### Write / population — overnight, ADD-only
A nightly `ko ai` skill: read new sessions → distill **atomic typed facts** using mem0's **ADD-only,
one-LLM-call-per-add** pattern (accumulate, skip complex UPDATE/DELETE logic — let decay do the pruning)
with a **small/cheap model** (cf. LightMem) → embed → insert. **Only from my own sessions/work —
never raw external content as a "fact"** (poisoning, see MemoryGraft).

### Decay / forgetting — the curation that keeps it useful
Forgetting is a *feature*, not an afterthought (SuperLocalMemory). On retrieval, bump `last_used` /
`use_count`. Periodic GC: weight ≈ `use_count * POWER(0.95, days_since_last_used)`; prune low-weight +
never-retrieved rows. This keeps the store a small curated set, not a junk drawer — **curation beats
vector tech** (the "memory fallacy" critique).

### Thin slice (build order)
1. **Markdown notes repo + `ko memory add` / `search`.** `add` appends a markdown fact (+ a `log.jsonl`
   line); `search` queries a **local libSQL index rebuilt from the markdown**. Prove retrieval is useful.
2. **Incremental re-index** — scan `log.jsonl` for `vector_dirty`, re-embed just those + decay/GC.
3. **Expose as an MCP server** → Claude Code + `ko ai` share one memory (and the same markdown repo).
4. **Overnight session-distiller skill** (ADD-only, small model) → writes markdown facts, not DB rows.
5. **Central Turso / edge-mcp** only when a *remote* agent (fleet/edge) must query without an embedder.

Skip for v1: knowledge graph (Zep / MAGMA), temporal fact-validity, UPDATE/merge logic. Add only if the
simple version visibly underdelivers.

---

## Research (2026-06-27)

Gathered by dogfooding `ko exa` / `ko hn` / `ko hf`.

### Reference implementation — Turso AI memory + `memelord`
- Turso's [AI memory guide](https://docs.turso.tech/guides/ai-memory): two tables (`memories`, `tasks`);
  `F8_BLOB(384)` int8-quantized vectors; search via `vector_distance_cos(embedding, vector8(?))`;
  **full table scan is fast enough at per-project scale** (no ANN index); int8 = 75% storage cut.
  Embeddings from an external model (`all-MiniLM-L6-v2`). Decay is left as experiments: EMA weights,
  `POWER(0.95, days_since_last_retrieved)` time-decay, GC of low-weight/never-retrieved.
- **`memelord`** — an MCP server + hooks implementing the pattern with reinforcement-style weight decay
  for coding agents. This is essentially the shape we want; read it before building.

### Cloudflare option (edge-native, two-store)
- Native CF memory-MCP servers exist: `MrSnowGlobe/cf-memory-mcp` (D1 + Vectorize + KV + Workers AI),
  `Avik-creator/mcp-memory-cloudflare`, `jmbish04/mcp-memory-v3`. [Vectorize + Workers AI embeddings
  tutorial](https://developers.cloudflare.com/vectorize/get-started/embeddings/).
- The honest write-up: ["challenges of a semantic memory layer on Workers + D1 + Vectorize"](https://dev.to/rahil_pirani_c48446facc8c/the-challenges-of-creating-a-semantic-memory-layer-on-cloudflare-workers-d1-and-vectorize-3c7a)
  — **two strictly separate stores** (D1 rows + Vectorize vectors) you must keep consistent; works on
  the free tier. Edge-native but **no local-first replica**.

### Managed layers — steal patterns, not the dependency
- **mem0** ([arxiv 2504.19413](https://arxiv.org/html/2504.19413)): two-phase (extraction + update),
  three-step loop (retrieve → enrich → store). **OSS v3 is ADD-only**: one LLM call per add, accumulate,
  no UPDATE/DELETE + multi-signal hybrid retrieval. ← the write pattern to copy.
- **Zep** (Graphiti): temporal knowledge graph, fact-validity over time. **Letta** (MemGPT): tiered,
  self-editing memory. Both heavier than v1 needs.

### HF papers (decay / extraction / typing / security)
- **SuperLocalMemory V3.3** (2604.04514) — local-first, **zero-LLM retrieval** (validates the
  deterministic read) + **biologically-inspired forgetting**.
- **LightMem** (2604.07798) — memory extraction with **small language models** (cheap write path).
- **Memanto** (2604.22085) — **typed** semantic memory + information-theoretic retrieval (store
  high-information items; the `type` field earns its place).
- **MemoryGraft** (2512.16962) — **poisoned-experience-retrieval attack**: malicious "memories" cause
  persistent behavioral drift. ⇒ only ingest distilled-from-my-own-work; treat memory like skills
  (read-before-trust).
- **Zep: A Temporal Knowledge Graph Architecture** (2501.13956, the paper behind the product).

### Skeptic
- ["The private agent memory fallacy"](https://blog.getzep.com/the-ai-memory-wallet-fallacy/) — naive
  vector-memory underdelivers (missed retrieval, stale junk). Lesson baked into the plan: **small,
  curated, decaying** > a big vector dump.
