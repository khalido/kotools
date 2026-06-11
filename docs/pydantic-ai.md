# pydantic-ai — notes & plan for `ko agent`

Planning doc for adding an agent subcommand to ko. Not a tutorial — the official docs at https://ai.pydantic.dev are canonical. This file captures *our* decisions and the shape we want, so we don't relitigate them later.

## Why pydantic-ai (over the alternatives)

- **Model-agnostic.** One-line swap between `anthropic:...`, `openai:...`, `google:...`, OpenRouter. Matches ko's "I might change my mind" habit.
- **Typed outputs.** Pass `output_type=SomeDataclass` and the agent returns that. Our ko modules already return dataclasses (`ExaResult`, `ArxivPaper`, `SheetInfo`) — these compose directly.
- **Simple core.** The `Agent` + `@tool_plain` decorator pattern is ~10 lines to get a working agent. No DAG, no graph, no orchestrator to learn.
- **FastAPI-like DX** from the Pydantic team — same people who built the stdlib of modern Python typing.

### Considered and skipped

- **Claude Agent SDK** — great, but locks to Anthropic and ships a big runtime (subagents, compaction, MCP). Overkill for ko's "thin wrappers" vibe.
- **smolagents** — tiny, but code-agent-first (every turn writes Python), which forces the sandbox question on day one.
- **LangChain / LangGraph / CrewAI** — kitchen sink. Opposite of ko's philosophy.
- **OpenAI Agents SDK** — fine if already on OpenAI. We aren't.

## Minimal shape

```python
from pydantic_ai import Agent
from ko import exa, arxiv

agent = Agent(
    "anthropic:claude-sonnet-4-6",
    system_prompt="You are ko's research assistant.",
)

@agent.tool_plain
def search_web(query: str) -> list[str]:
    return [r.url for r in exa.search(query).results]

@agent.tool_plain
def fetch_paper(arxiv_id: str) -> str:
    return arxiv.fetch(arxiv_id).markdown

result = agent.run_sync("Find 3 recent papers on grid projection OCR")
print(result.output)
```

## Toolsets — the useful abstraction

`FunctionToolset` groups tools so they can be reused across agents, filtered, prefixed, and attached/detached at runtime. Per-module toolsets fit ko cleanly:

```python
# src/ko/agent_tools.py (sketch)
from pydantic_ai import FunctionToolset
from ko import exa, arxiv, gsheets

exa_toolset = FunctionToolset(
    tools=[exa.search, exa.get],
    instructions="Use exa for open-web semantic search and URL→markdown.",
)

arxiv_toolset = FunctionToolset(
    tools=[arxiv.search, arxiv.fetch],
    instructions="Use arxiv for CS/ML papers. Prefer fetch() for full-text.",
)
```

Then `Agent(..., toolsets=[exa_toolset, arxiv_toolset])`. Swap or filter per-run.

Key toolset features worth knowing (from https://ai.pydantic.dev/toolsets/):

- `.filtered(fn)` — hide tools at runtime based on context
- `.prefixed("exa")` — avoid name clashes (`exa_search` vs `arxiv_search`)
- `.approval_required(fn)` — human-in-the-loop before calling a tool (useful for writes/spend)
- `.defer_loading()` — don't load tool schemas until discovered; good for large MCP toolsets
- `CombinedToolset([a, b])` — merge

## Sandbox — `mcp-run-python`

The Pydantic team ships [`mcp-run-python`](https://github.com/pydantic/mcp-run-python), an MCP server that runs Python in a sandboxed environment (Deno + Pyodide). Wire it up as:

```python
from pydantic_ai import Agent
from pydantic_ai.mcp import MCPServerStdio

run_python = MCPServerStdio("uv", args=["run", "mcp-run-python", "stdio"])
agent = Agent("anthropic:claude-sonnet-4-6", toolsets=[run_python])
```

**When we'd actually want this:** only when the agent needs to *execute arbitrary code* (data munging, quick analysis). For an agent that just calls `exa.search()` + `arxiv.fetch()` + reads Sheets, there's no untrusted code — no sandbox needed. Add it when/if we build `ko agent "analyze this CSV"`.

**Alternatives if `mcp-run-python` is too limited** (it's Pyodide — no native deps like pandas C extensions in some cases): E2B (Firecracker microVMs, paid, Python SDK), Daytona (Docker-based, faster cold start), Modal (if we already run stuff on Modal).

## Plan — `ko agent` subcommand

Scope for v0:

1. `ko agent "<task>"` — one-shot, prints result to stdout.
2. `--model <id>` flag, default from `KO_AGENT_MODEL` env or `anthropic:claude-sonnet-4-6`.
3. `--toolset exa,arxiv,gsheets` — comma-separated, all enabled by default.
4. `--json` — dump structured output (result + message trace) instead of plain text.
5. No sandbox yet. No code execution tool yet.
6. Tool functions are direct references to `ko.exa.search` etc. — so agents get the same dataclass shapes `ko` users get.

Files:
- `src/ko/agent.py` — assembles the `Agent` + `FunctionToolset`s from existing modules
- `src/ko/cli.py` — add `agent` subapp with the `run` command

Out of scope for v0 (revisit after dogfooding):
- Multi-turn REPL mode (`ko agent repl`)
- Streaming output
- Persistence / message history across invocations
- Code execution / sandboxing
- Exposing the agent itself over MCP (our existing `mcp_server.py` path is for the raw tools, not for an orchestrated agent)

## Open questions

- **Which model default?** Claude for quality, but OpenRouter lets us shop by price. Leaning: default to `anthropic:claude-sonnet-4-6`; `KO_AGENT_MODEL=openrouter:deepseek/...` for cheap runs.
- **Approval gating.** Should `gsheets` writes (once we add them) be `.approval_required()` by default? Probably yes — follow the "no interactive prompts" rule except for explicit human-in-the-loop.
- **Logfire / tracing.** Pydantic-ai integrates with Logfire out of the box. Skip for now; add when debugging gets painful.
- **Structured output.** v0 returns plain text. Once we have a clear shape (e.g. `AgentResult(answer, sources, tools_called)`), promote it.

## Auth & secrets (reconfirmed)

- **Google (gsheets):** human runs `ko gsheets auth` once → token cached at `~/.config/ko/google_token.json`. MCP server reads the cached token. If missing, fail loud (`AuthError("run 'ko gsheets auth' first")`) — never spawn a browser inside the MCP process.
- **Provider keys (exa, anthropic, openrouter…):** read from env. Optional `.env` loading via `python-dotenv` (consider adding) so project-local secrets work without polluting the shell. Load lazily in the relevant module, not at import time.

## Pydantic AI ecosystem — what we use, what we defer

| Package | Use? | Why |
|---|---|---|
| `pydantic-ai` | ✅ installed | Core agent + toolsets + MCP client. Foundation. |
| `pydantic-ai-slim[anthropic,openrouter]` | maybe later | Leaner alt if lockfile bloat from full `pydantic-ai` (temporalio, xai, bedrock…) starts to bite. |
| `pydantic-ai-harness` | ⏸ defer | Capability library: memory, guardrails, file system, **code mode**. Install when we want one. |
| `mcp` (official SDK) | ✅ already in deps | We're the MCP *server*. PydanticAI is the MCP *client*. Don't conflate. |
| `logfire` | ⏸ defer | Observability + LLM gateway (Pydantic killed standalone Gateway, folded it into Logfire). One-line integration; add when debugging hurts. |

## References

- Docs (canonical): https://pydantic.dev/docs/ai/
- llms.txt: https://pydantic.dev/docs/ai/llms.txt
- Toolsets: https://pydantic.dev/docs/ai/toolsets/
- MCP client: https://pydantic.dev/docs/ai/mcp/client/
- Harness: https://pydantic.dev/docs/ai/harness/overview/
- Code Mode (in Harness): https://pydantic.dev/docs/ai/harness/code-mode/
- Logfire (incl. Gateway): https://logfire.pydantic.dev/
- `mcp-run-python`: https://github.com/pydantic/mcp-run-python
