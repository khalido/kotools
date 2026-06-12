# pydantic-ai — notes for ko's agent layer

Decision record, not a tutorial — https://pydantic.dev/docs/ai/ is canonical. Captures *our* choices so we don't relitigate them.

**Status (2026-06):** v0 shipped as `ko agent research` (`src/ko/agents/research.py` — one-shot + REPL, Exa search tool). The broader design — `ko ai`, skills as capabilities, SQLite persistence — lives in `docs/ideas.md` under "`ko ai` — the agent layer".

## Why pydantic-ai (over the alternatives)

- **Model-agnostic.** One-line swap between `anthropic:...`, `openai:...`, `google:...`, OpenRouter.
- **Typed outputs.** `output_type=SomeDataclass` — composes directly with ko's dataclass returns (`ExaResult`, `SheetInfo`, …).
- **Simple core.** `Agent` + `@tool_plain` is ~10 lines to a working agent. No DAG/orchestrator to learn.
- **FastAPI-like DX** from the Pydantic team.

Considered and skipped: **Claude Agent SDK** (Anthropic-locked, big runtime), **smolagents** (code-agent-first — forces the sandbox question on day one), **LangChain/LangGraph/CrewAI** (kitchen sink), **OpenAI Agents SDK** (we're not on OpenAI).

## Toolsets — the useful abstraction

One `FunctionToolset` per ko module (`exa`, `arxiv`, `hn`, `hf`…), each with its own `instructions=`; pass `toolsets=[...]` per agent or per run. Worth knowing (https://pydantic.dev/docs/ai/toolsets/):

- `.filtered(fn)` — hide tools at runtime
- `.prefixed("exa")` — avoid name clashes
- `.approval_required(fn)` — human-in-the-loop for writes/spend
- `.defer_loading()` — don't load schemas until discovered (large MCP toolsets)
- `CombinedToolset([a, b])` — merge

## Sandbox — only when we execute code

[`mcp-run-python`](https://github.com/pydantic/mcp-run-python) (Pydantic's Deno+Pyodide MCP server) is the first pick when the agent needs to *run arbitrary code*. An agent that only calls `exa.search()` / `arxiv.fetch()` has no untrusted code — no sandbox needed. Alternatives if Pyodide is too limited: E2B, Daytona, Modal.

## Open questions

- **Default model:** leaning `anthropic:claude-sonnet-4-6`; `KO_AGENT_MODEL` env override for cheap OpenRouter runs.
- **Approval gating:** gsheets writes (once added) should be `.approval_required()`.
- **Logfire:** one-line integration; add when debugging hurts.

## Auth & secrets

- **Google:** human runs `ko gsheets auth` once → cached token. Agent/MCP contexts read the cache; if missing, fail loud — never spawn a browser from a non-interactive process.
- **Provider keys:** env vars, loaded lazily in the relevant module, never at import time.

## Ecosystem — use vs defer

| Package | Use? | Why |
|---|---|---|
| `pydantic-ai` | ✅ installed | Core agent + toolsets + MCP client. |
| `pydantic-ai-slim[...]` | maybe later | If full-package lockfile bloat bites. |
| `pydantic-ai-harness` | ⏸ defer | Capability library (memory, guardrails, code mode). |
| `mcp` (official SDK) | ✅ in deps | We're the MCP *server*; pydantic-ai is the MCP *client*. |
| `logfire` | ⏸ defer | Observability; add when debugging hurts. |

## References

- Docs: https://pydantic.dev/docs/ai/ (llms.txt: https://pydantic.dev/docs/ai/llms.txt)
- Capabilities: https://pydantic.dev/docs/ai/core-concepts/capabilities/
- Toolsets: https://pydantic.dev/docs/ai/toolsets/
- MCP client: https://pydantic.dev/docs/ai/mcp/client/
- Multi-agent / delegation: https://pydantic.dev/docs/ai/guides/multi-agent-applications/
- Harness + code mode: https://pydantic.dev/docs/ai/harness/overview/
