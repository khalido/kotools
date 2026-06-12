# pydantic-ai — knowledge base

Why we use it, what we verified in its source, what bit us. Official docs are canonical: https://pydantic.dev/docs/ai/ — this file is *our* accumulated knowledge. Design decisions for the agent layer live in `ideas.md` ("`ko ai`").

## Version status

- **On v2.0.0b7** (pinned exact, 2026-06-12). v2 is harness-first: `capabilities=[...]` is the core primitive — matches ko's skills design, which is why we jumped before building anything. Stable v2 "two weeks away" since 2026-05-20, so any day; **un-pin when it lands**.
- `[tool.uv] prerelease = "allow"` required (the `pydantic-ai-slim` sub-package is also beta).
- Source mirrors: `~/code/refs/pydantic-ai` (main = v1 line) and `~/code/refs/pydantic-ai-v2` (v2-main worktree). While on beta, **verify against v2-main source before relying on API details** — preview docs and main drift.
- v1→v2 gotchas: `openai:` model strings now hit the Responses API (`openai-chat:` = old Chat Completions); bare install ships fewer provider extras (anthropic + openrouter still included — verified).

## Why pydantic-ai (decided 2026-04; alternatives considered)

- **Model-agnostic** — one string swaps provider (`anthropic:`, `openrouter:org/model`, …).
- **Typed outputs** — `output_type=SomeDataclass` composes with ko's dataclass returns.
- **Thin core, FastAPI-like DX**; v2's capabilities map 1:1 onto ko's skills plan.
- Skipped: Claude Agent SDK (Anthropic-locked, heavy runtime), smolagents (code-agent-first → sandbox question on day one), LangChain/LangGraph/CrewAI (kitchen sink), OpenAI Agents SDK (wrong provider).

## v2 API facts (audited against v2-main source, 2026-06-12)

- **`Capability(*, instructions=None, toolsets=None, tools=(), id=None, description=None, defer_loading=False)`** (`capabilities/capability.py`). `defer_loading=True` hides everything behind a one-line catalog entry until the model calls `load_capability(id)`; `id` required when deferred.
- **No `from_file`/markdown helpers on Capability** — it holds live Python objects and can't round-trip YAML. Skills-as-markdown is ~3 lines of our own: `Capability(id=name, description=frontmatter.description, instructions=md_body, defer_loading=True)`.
- **`Agent.from_file(yaml/json)`** exists, but only for spec-constructible capabilities (`NativeTool`, `MCP`, `ReinjectSystemPrompt`, …) — not custom `Capability` with functions.
- **Toolsets survive** under capabilities: `FunctionToolset(tools=[...], instructions=...)`; `Capability(toolsets=[...])` wraps them. `FilteredToolset` takes a *callable* `(ctx, tool_def) -> bool`, not a name list. Per-run `toolsets=[...]` and `capabilities=[...]` both work.
- **`agent.model` is read-only in v2.** Model switching = pass `model="..."` per `run()`/`run_sync()` call (one Agent instance serves all models). The v1 clai pattern (`agent.model = infer_model(...)`) is dead — our `/model` command just keeps the current choice in a variable.
- **Persistence is ours to own**: `ModelMessagesTypeAdapter.dump_json(result.all_messages())` → blob; reload via `.validate_json` into `message_history=`. `conversation_id` is metadata threading only — no built-in storage. `ReinjectSystemPrompt()` for resumed sessions. Record `pydantic_ai_version` with stored transcripts (old can't read new).
- **`UsageLimits`**: `request_limit=50` default; also `tool_calls_limit`, token limits, `count_tokens_before_request`. Cost: `ModelResponse.cost().total_price` (via the `genai-prices` dep).
- **`Hooks` capability** is extensive in-core: before/after run, node, model request, tool validate/execute (+ wrap/error variants), `@hooks.on.before_model_request` decorator form. Named guardrail/memory capabilities live in **pydantic-ai-harness** (separate package, defer).
- **In-core spec-serializable capabilities**: NativeTool, ImageGeneration, Instrumentation, MCP, PrefixTools, PrepareTools, ProcessHistory, ReinjectSystemPrompt, SetToolMetadata, Thinking, Toolset, ToolSearch, WebFetch, WebSearch, XSearch, …
- **`known_model_names()`** (480 qualified names) — feeds `ko llm` `-m` autocomplete; filter by configured env keys; OpenRouter models aren't enumerated (arbitrary strings — pull their live catalog when keyed).

## Agent Skills ↔ Capabilities (researched 2026-06-12)

They're different layers, not rivals: **agentskills.io is the portable file format** (knowledge, shareable, Claude Code loads it natively); **Capability is the runtime object** (typed, composable, enforceable). Author skills as files, load them as capabilities.

- **Officially blessed bridge:** the v2 capabilities doc itself (`docs/capabilities.md` in v2-main, ~line 409) shows a `load_skill(path)` that splits SKILL.md frontmatter → `Capability(id, description, instructions=body, defer_loading=True)`. Pydantic publishes their own skill at github.com/pydantic/skills (vendored at `.agents/skills/building-pydantic-ai-agents/`). Native interop tracked in pydantic-ai issue #3365 — check before building our loader, it may land.
- **Community prior art:** `pydantic-ai-skills` (PyPI; adds `read_skill_resource` + `run_skill_script` tools for bundled files/scripts), `haiku.skills` (sub-agent-per-skill mode — each skill runs isolated with only its tools), `coleam00/custom-agent-with-skills` (clean roll-your-own reference). In refs: hermes-agent carries 150+ SKILL.md files, openclaw 75+ — a borrowable ecosystem, which is the real argument for staying on-format.
- **`allowed-tools` frontmatter is Claude-Code-specific** (Experimental in the spec) — hosts like raw pydantic-ai ignore it. Our opinionated upgrade: map it to a `FilteredToolset` so ko *enforces* what the spec only suggests. That's the Capability robustness win: skill text says "only use X"; a capability can make non-X tools not exist.
- **Bundled resource files:** embed small ones inline after the body at load time; if they grow, add a `read_skill_resource` tool (DougTrajano pattern) for second-stage disclosure.
- **Pure text recipes** ("use A then B, double-check, report") are just `instructions=` — they work with zero special handling.

## Useful v2 features on our radar

- **`pydantic_ai.direct`** — model call with no Agent at all; possible ultra-thin `ko llm` core (we still prefer Agent for the shared mental model).
- **`Thinking()`** — extended thinking for Anthropic models, one line in `capabilities=[]`.
- **`ToolSearch`** — model searches tools by description instead of seeing all schemas; relevant when ko's tool surface grows.
- **Per-run `spec=` overlay** — merge AgentSpec config at call time without rebuilding the agent.
- **`WebFetch`/`WebSearch` capabilities** — provider-native search/fetch with local fallback. Note: does NOT obsolete `ko fetch` (Layer 1 must stay deterministic + LLM-free), but `ko ai` could lean on them.
- **clai** (`pydantic_ai/_cli/__init__.py`, private module — pin-sensitive): compose from its pieces, don't call `run_chat()` whole (it never returns messages). Import `ask_agent()` (streams, returns `all_messages()`), `handle_slash_command()`, `CustomAutoSuggest`; pass `config_dir=~/.config/ko`. Verified v2-current (diff vs main: 10 trivial lines). `clai web` = free browser chat UI for any agent.

## Sandbox — only when we execute code

[`mcp-run-python`](https://github.com/pydantic/mcp-run-python) (Deno+Pyodide MCP server) when the agent must run arbitrary code. Tool-calling-only agents need no sandbox. Fallbacks: E2B, Daytona, Modal.

## Auth & secrets

- Google: `ko gsheets auth` once → cached token; non-interactive contexts fail loud, never spawn a browser.
- Provider keys: env vars, loaded lazily in the using module, never at import time.

## Ecosystem — use vs defer

| Package | Use? | Why |
|---|---|---|
| `pydantic-ai` (v2 beta) | ✅ pinned | Core agent + capabilities + toolsets + MCP client. |
| `pydantic-ai-harness` | ⏸ defer | Memory, guardrails, code mode — install per-capability when wanted. |
| `mcp` (official SDK) | ✅ in deps | We're the MCP *server*; pydantic-ai is the MCP *client*. |
| `logfire` | ⏸ defer | One-line observability; add when debugging hurts. |

## References

- Docs: https://pydantic.dev/docs/ai/ (llms.txt: https://pydantic.dev/docs/ai/llms.txt)
- v2 changelog / upgrade guide: https://pydantic.dev/docs/ai/changelog/
- v2.0.0b1 release notes: https://github.com/pydantic/pydantic-ai/releases/tag/v2.0.0b1
- Capabilities: https://pydantic.dev/docs/ai/core-concepts/capabilities/
- MCP client: https://pydantic.dev/docs/ai/mcp/client/
- Multi-agent / delegation: https://pydantic.dev/docs/ai/guides/multi-agent-applications/
- Local source: `~/code/refs/pydantic-ai-v2` (v2-main), `~/code/refs/pydantic-ai` (main/v1)
