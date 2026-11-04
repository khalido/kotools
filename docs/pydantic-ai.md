# pydantic-ai — knowledge base

Why ko uses it, what I verified in its source, what bit me. Official docs are canonical: https://pydantic.dev/docs/ai/. This file is accumulated knowledge only. Design decisions for the agent layer live in `ideas.md` ("`ko ai`"). Status: in use. Agents, `ko llm`, sessions and the MCP client are built on it. Capabilities and skills are not used in ko code yet.

## Version status

- **Stable v2.** `pydantic-ai-slim[google,openrouter,mcp,typesafe]>=2.56,<3` in pyproject; 2.61 installed as of 2026-11-04. v2 is harness-first: `capabilities=[...]` is the core primitive, which matches ko's skills design. That is why I jumped before building anything. (I ran the v2 beta, pinned exact, from 2026-06-12 until stable landed.)
- Source mirrors: `~/code/refs/pydantic-ai` (main, now the v2 line) and `~/code/refs/pydantic-ai-harness`.
- v1→v2 gotchas: `openai:` model strings now hit the Responses API (`openai-chat:` is the old Chat Completions). A bare install ships fewer provider extras, so pyproject declares `google`, `openrouter`, `mcp` and `typesafe` explicitly.
- ko never builds a model string itself: every call goes through `models.resolve()` (`src/ko/models.py`) and the Vercel AI Gateway.

## Why pydantic-ai (decided 2026-04)

- **Model-agnostic.** One string swaps provider.
- **Typed outputs.** `output_type=SomeDataclass` composes with ko's dataclass returns.
- **Thin core, FastAPI-like DX.** v2's capabilities map 1:1 onto ko's skills plan.
- Skipped: Claude Agent SDK (Anthropic-locked, heavy runtime), smolagents (code-agent-first, so a sandbox question on day one), LangChain/LangGraph/CrewAI (kitchen sink), OpenAI Agents SDK (wrong provider).

## v2 API facts

Audited against v2-main source on 2026-06-12. Rechecked against the installed 2.61 on 2026-11-04; `agent.model` and the skills loader changed.

- **`Capability(*, instructions=None, toolsets=None, tools=(), id=None, description=None, defer_loading=False)`** (`capabilities/capability.py`). `defer_loading=True` hides everything behind a one-line catalog entry until the model calls `load_capability(id)`. `id` is required when deferred.
- **No `from_file`/markdown helper on Capability.** It holds live Python objects and can't round-trip YAML. Skills-as-markdown is a few lines: `Capability(id=name, description=frontmatter.description, instructions=md_body, defer_loading=True)`. `pydantic-ai-harness` now ships a `Skills` capability that does this (see below).
- **`Agent.from_file(yaml/json)`** exists, but only for spec-constructible capabilities (`NativeTool`, `MCP`, `ReinjectSystemPrompt`, …). It cannot load a custom `Capability` with functions.
- **Toolsets survive** under capabilities: `FunctionToolset(tools=[...], instructions=...)`, and `Capability(toolsets=[...])` wraps them. `FilteredToolset` takes a *callable* `(ctx, tool_def) -> bool`, not a name list. Per-run `toolsets=[...]` and `capabilities=[...]` both work.
- **`agent.model` is settable in 2.61** (it was read-only in the 2026-06 audit). ko still passes `model=` per `run_sync()`/`run_stream_sync()` call, so one Agent instance serves all models (`agents/_shared.py:68`). The v1 clai pattern (`agent.model = infer_model(...)`) is not needed.
- **Persistence is ours.** `ModelMessagesTypeAdapter.dump_json(result.all_messages())` gives a blob; reload it with `.validate_json` into `message_history=` (`src/ko/sessions.py`). `conversation_id` only threads metadata; there is no built-in storage. `ReinjectSystemPrompt()` is for resumed sessions (ko doesn't use it yet). Old pydantic-ai can't read new transcripts, so record `pydantic_ai_version` with each stored session. `sessions.py` doesn't record it yet.
- **`UsageLimits`.** `request_limit=50` by default; also `tool_calls_limit`, token limits, `count_tokens_before_request`. Cost: `ModelResponse.cost().total_price` (via the `genai-prices` dep).
- **`Hooks` capability** is extensive in core: before/after run, node, model request, tool validate/execute (plus wrap/error variants), and a `@hooks.on.before_model_request` decorator form. Named guardrail and memory capabilities live in **pydantic-ai-harness**.
- **In-core capabilities** include `NativeTool`, `ImageGeneration`, `Instrumentation`, `MCP`, `PrefixTools`, `PrepareTools`, `ProcessHistory`, `ReinjectSystemPrompt`, `SetToolMetadata`, `Thinking`, `Toolset`, `ToolSearch`, `WebFetch`, `WebSearch`, `XSearch`, and more (`capabilities/__init__.py`).
- **`known_model_names()`** returns 776 qualified names in 2.61. It feeds `-m` autocomplete in `models.available_models()`, filtered by configured env keys. Gateway and OpenRouter catalogs aren't in it (open-ended); ko fetches and caches their public `/models` lists instead (`models.py:180`).

## Agent Skills and Capabilities

They are different layers, not rivals. **agentskills.io is the portable file format** (knowledge, shareable, Claude Code loads it natively). **Capability is the runtime object** (typed, composable, enforceable). Author skills as files, load them as capabilities.

- **A loader now ships, in harness.** `pydantic-ai-harness` 0.61 has `Skills('path/to/library')` (`pydantic_ai_harness/skills/`, needs the `skills` extra). The model sees each skill's name and description and loads the body with `load_capability`. It reads from a workspace capability (`LocalWorkspace`), doesn't search `.agents`/`.claude`/home on its own, and doesn't load bundled resources or run scripts. The 2026-06-12 sweep found no loader in core, and core still has none. Harness is in ko's dev group only (`codemode`). To use skills, add `pydantic-ai-harness[skills]` to dependencies, or write the loader below. The old watch item, harness PR #183, has landed. Issue #3365 is about Anthropic's *API-level* container-skills beta, not the SKILL.md format.
- **Community prior art:** `pydantic-ai-skills` (PyPI; adds `read_skill_resource` and `run_skill_script` tools for bundled files and scripts), `haiku.skills` (one sub-agent per skill, isolated with only its tools), `coleam00/custom-agent-with-skills` (clean roll-your-own reference). In refs, hermes-agent carries 150+ SKILL.md files and openclaw 75+. That borrowable ecosystem is the real argument for staying on-format. Pydantic publishes its own skill at github.com/pydantic/skills.
- **`allowed-tools` frontmatter is Claude-Code-specific** (Experimental in the spec). Hosts like raw pydantic-ai ignore it. The upgrade I want: map it to a `FilteredToolset`, so ko *enforces* what the spec only suggests. The skill text says "only use X"; a capability can make non-X tools not exist.
- **Bundled resource files:** inline small ones after the body at load time. If they grow, add a `read_skill_resource` tool (DougTrajano pattern) for second-stage disclosure.
- **Pure text recipes** ("use A then B, double-check, report") are just `instructions=`. They need no special handling.
- **Own-loader sketch** (`src/ko/skills.py`, not written), for when `Skills` doesn't fit, for example to enforce `allowed-tools`:
  ```python
  def load_skill(skill_dir: Path) -> Capability:
      front, body = parse_frontmatter((skill_dir / "SKILL.md").read_text())
      extras = "".join(                       # small bundled refs inlined;
          f"\n\n## {f.name}\n\n{f.read_text()}"   # add a read_skill_resource
          for f in sorted((skill_dir / "references").glob("*.md"))  # tool if they grow
      ) if (skill_dir / "references").is_dir() else ""
      return Capability(
          id=front["name"], description=front["description"],
          instructions=body + extras, defer_loading=True,
      )

  def load_skills(names: list[str] | None = None) -> list[Capability]:
      # repo skills/ first, ~/.config/ko/skills/ overlay wins on name clash;
      # names=None → all, else filter (the agents/<name>.toml `skills = [...]` hook)
  ```
  Packaging: repo `skills/` must ship in the wheel for uv-tool installs. Use hatch `force-include` into `ko/_skills/` plus `importlib.resources`, or keep skills under `src/ko/skills_data/`. Decide at build time.

## Features on the radar

- **`pydantic_ai.direct`** — a model call with no Agent. A possible ultra-thin `ko llm` core; I still prefer Agent for the shared mental model.
- **`Thinking()`** — extended thinking, one line in `capabilities=[]`.
- **`ToolSearch`** — the model searches tools by description instead of seeing all schemas. Relevant when ko's tool surface grows.
- **Per-run `spec=` overlay** — merge AgentSpec config at call time without rebuilding the agent.
- **`WebFetch`/`WebSearch` capabilities** — provider-native search and fetch with local fallback. They don't replace `ko fetch`, which must stay deterministic and LLM-free, but `ko ai` could lean on them.
- **clai** (`pydantic_ai/_cli/__init__.py`, private module, so pin-sensitive): compose from its pieces; don't call `run_chat()` whole, because it never returns messages. Import `ask_agent()` (streams, returns `all_messages()`), `handle_slash_command()` and `CustomAutoSuggest`, and pass `config_dir=~/.config/ko`. All three exist in 2.61. `clai web` is a free browser chat UI for any agent.

## Sandbox — only when we execute code

[`mcp-run-python`](https://github.com/pydantic/mcp-run-python) (Deno+Pyodide MCP server) when the agent must run arbitrary code. Tool-calling-only agents need no sandbox. Fallbacks: E2B, Daytona, Modal.

## Auth and secrets

- Google: `ko google auth <tag>` once, then a cached token. Non-interactive contexts fail loud and never spawn a browser.
- Provider keys: env vars, loaded lazily in the using module, never at import time.

## Ecosystem — use vs defer

| Package | Use? | Why |
|---|---|---|
| `pydantic-ai-slim` (v2) | ✅ range `>=2.56,<3` | Core agent, capabilities, toolsets, MCP client. |
| `pydantic-ai-harness` | ⏸ dev group only | Memory, guardrails, skills, code mode. Install per capability when wanted. |
| `mcp` (official SDK) | ✅ in deps | pydantic-ai is the MCP *client* (`mcp_client.py`, `MCPToolset`). The MCP *server* (`mcp_server.py`) is scaffolded and unwired. |
| `logfire` | ⏸ defer | One-line observability. Add when debugging hurts. Opt-in PostHog telemetry exists instead (`telemetry.py`). |

## References

- Docs: https://pydantic.dev/docs/ai/ (llms.txt: https://pydantic.dev/docs/ai/llms.txt)
- v2 changelog / upgrade guide: https://pydantic.dev/docs/ai/changelog/
- v2.0.0b1 release notes: https://github.com/pydantic/pydantic-ai/releases/tag/v2.0.0b1
- Capabilities: https://pydantic.dev/docs/ai/core-concepts/capabilities/
- MCP client: https://pydantic.dev/docs/ai/mcp/client/
- Multi-agent / delegation: https://pydantic.dev/docs/ai/guides/multi-agent-applications/
- Local source: `~/code/refs/pydantic-ai`, `~/code/refs/pydantic-ai-harness`
