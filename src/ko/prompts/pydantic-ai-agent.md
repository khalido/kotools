---
name: pydantic-ai-agent
description: How I build pydantic-ai v2 agents — toolsets, structured output, sessions, sandbox
---
# pydantic-ai v2 agent — kickoff

How I wire up agents with pydantic-ai v2. Two reference implementations:
- **`~/code/kotools/src/ko/agents/`** — simplest form: FunctionToolsets + shared run/repl loop, sessions.
- **`~/code/jabberwocky/sift/`** — production form: `Agent.from_file` YAML specs, `AbstractCapability` bundles,
  structured output, sandbox path guard, `TestModel` contract tests.

## Stack

- **pydantic-ai v2** — `pydantic-ai-slim[google,openrouter,mcp]>=2.4,<3` (kotools pin). Use `slim` and add
  only the provider extras you actually need. `TestModel`/`FunctionModel` ship inside pydantic-ai itself
  (`pydantic_ai.models.test` / `.function`) — no extra package (see the TestModel section).
- **Providers**: Google Gemini is the default (cheap + fast + generous context);
  OpenRouter for routing experiments. `KO_AGENT_MODEL` / `KO_DEFAULT_MODEL` env vars via `config.toml`.
- **`uv`** — never pip. `uv run pytest`, `uv lock --upgrade` for dep bumps.

## Model selection — don't bake it in

Set a per-agent default at module import so the agent has a fallback; pass the per-run override
via `model=` at call time — NOT into the `Agent()` constructor — so the `-m` flag actually takes effect:

```python
# research.py
_MODEL = os.environ.get("KO_AGENT_MODEL", "openrouter:z-ai/glm-5.2")   # capable model
agent = Agent(_MODEL, instructions=..., toolsets=[web, papers, news])

def run(prompt, model=None, resume=None):
    return _shared.run(agent, prompt, name="research", model=model, resume=resume)
```

`KO_AGENT_MODEL` is global to all agents (session-level override); the `-m` flag feeds `model=` per-call.
Cheap tasks get a cheap default (`"google:gemini-3.5-flash"` for the TV agent).

## Toolsets — declare once, compose by reference

`FunctionToolset` is stateless and shareable. Declare it once in `_toolsets.py`; agents pick the subset
they need. Each toolset carries its own `instructions=` so usage guidance travels with the tools and only
appears when the toolset is attached.

```python
web = FunctionToolset(instructions="Web: exa_search to find sources, exa_get/fetch_url to read in full.")

@web.tool_plain          # no RunContext needed — pure function
def exa_search(query: str, n: int = 5) -> list[ExaResult]:
    """Semantic web search. Returns title, URL, date, excerpt."""
    return _try("exa_search", exa_mod.search, query, n=n, with_text=True)

# then compose:
agent = Agent(_MODEL, instructions=..., toolsets=[web, papers, news])   # ~15 lines total
```

Use `@toolset.tool` (not `tool_plain`) when the tool needs `ctx: RunContext[DepsType]` — for
dependency injection or to raise `ModelRetry` (see sandbox section).

## Structured output — enums, not floats

For extraction tasks, set `output_type=<PydanticModel>` on the agent. The model returns validated
structured data, not raw text. **Do not ask LLMs for numeric probabilities or scores** — they
produce run-to-run drift. Use categorical `Literal` enums instead and map to floats in Python:

```python
crowding: Literal["contrarian", "mixed", "consensus", "unknown"]
```

**Every judgment enum needs an honest abstention value** (`unknown`/`none`/`not_applicable`) — forced
choice without basis produces random-looking flips. Jabberwocky measured: crowding agreement went from
45 % to 86 % raw agreement after adding `unknown` to the enum. (Source: `sift/schema.py:TickerSignal`.)

For `Agent.from_file` (YAML specs): `Agent.from_file(spec.yaml, output_type=FeatureOutput, capabilities=[...])`.
Cap model calls with `UsageLimits(request_limit=N)` to prevent runaway costs on a bad prompt.

## Resilient tools — `_try`, not bare calls

Wrap every tool call so one flaky source (rate limit, timeout, empty result) returns an error note
instead of crashing the run — the model routes to another source. Re-raise programming errors loudly:

```python
def _try(label: str, fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except (TypeError, AttributeError, NameError, ImportError):
        raise           # our bug — surface it, don't disguise it
    except Exception as e:
        return f"{label} unavailable ({type(e).__name__}: {e}). Note this and try another source."
```

(Verbatim from `kotools/src/ko/agents/_toolsets.py`.)

For sandbox path-guarding, raise `ModelRetry` instead — the error message is fed back into the model
loop as a tool result, so the model corrects itself:

```python
from pydantic_ai import ModelRetry
if not full.is_relative_to(root):
    raise ModelRetry("Path outside working folder. Use relative paths only.")
```

## Sessions — persist every turn

pydantic-ai has no built-in session store; `all_messages()` is the mechanism. Dump after every turn
so runs are resumable and listable:

```python
from pydantic_ai.messages import ModelMessagesTypeAdapter

# save
msgs = json.loads(ModelMessagesTypeAdapter.dump_json(messages))
path.write_text(json.dumps({"id": session_id, "messages": msgs, ...}))

# resume
messages = ModelMessagesTypeAdapter.validate_python(data["messages"])
agent.run_sync(prompt, message_history=messages, model=model)
```

Sessions live in `~/.local/state/<app>/sessions/<id>.json` — state dir, not config (generated, not
hand-edited). IDs are time-sortable: `YYYYmmddTHHMMSS-hex6`. Write atomically (tmp + `os.replace`).

For long multi-tool runs (jabberwocky sift), write `result.all_messages_json()` to a `transcript.json`
alongside the output — this is the full revivable session. Note: transcript format is version-bound;
stamp `pydantic_ai.__version__` in run metadata since old pydantic-ai can't read messages serialized
by a newer one.

## Capabilities — bundle tools + instructions as one unit

`AbstractCapability[DepsType]` is the composable unit for anything more complex than a plain toolset:
a capability bundles a `FunctionToolset` and dynamic instructions (callable = re-evaluated each turn):

```python
class SiftWorkspace(AbstractCapability[SiftDeps]):
    def get_toolset(self):
        return toolset          # FunctionToolset with read/write/edit/list/lookup_ticker

    def get_instructions(self):
        return _workspace_instructions   # callable — injects memory.md head each turn
```

Attach with `capabilities=[SiftWorkspace()]`. The thinker port upgraded this to a `before_tool_execute`
hook on the capability for cleaner path-guarding (validates any arg named `path` before tool runs).

Per-agent memory that survives across runs: a markdown file at `runs/{date}/{sector}/memory.md`.
Prep copies the strictly-prior day's memory (PIT-safe — never the current day). The agent reads it via
its toolset; the runner (not the agent) writes the new signals/parquet.

## `_shared.py` — one run/repl loop for all agents

Stream pretty markdown to a TTY; plain text when piped; save the session after every turn.
`run_stream_sync` returns the result directly (not a context manager) in pydantic-ai v2;
`stream_text()` (no args, or `delta=False`) yields the full text so far each tick:

```python
result = agent.run_stream_sync(prompt, message_history=history, model=model)
with Live(console=_console, refresh_per_second=15) as live:
    for text in result.stream_text():
        live.update(Markdown(text))
```

TTY detection: `Console().is_terminal`. Plain-text path uses `agent.run_sync()` + `result.output`.

## TestModel — CI without API calls

`from pydantic_ai.models.test import TestModel`. Pass `TestModel(call_tools=[])` to skip straight to
the output tool — validates YAML/capability wiring, tool registration, and `output_type` schema
roundtrip without a live API call. Use `FunctionModel(script)` when you need to script the exact
turn sequence (e.g., sandbox escape tests). Both accept the same `Agent` constructor as a real model.

## Adding an agent

1. Add/extend a `FunctionToolset` in `agents/_toolsets.py` if new tools are needed.
2. `agents/<name>.py` — `Agent(model, instructions=..., toolsets=[...])` + thin `run`/`repl` binding `_shared`.
3. Export in `agents/__init__.py`; add a `@agent_app.command` in `cli.py`.
4. `tests/test_<name>.py` — smoke test with `TestModel`; skip live calls if token missing.
5. Deps in `pyproject.toml` under `[project].dependencies`.

## Gotchas

- **Model baking**: if you pass the model string to `Agent()` AND override it at run time, the per-run
  `model=` wins — but the agent's system prompt is cached at import time (fine). The issue is when
  `KO_AGENT_MODEL` is read *before* the CLI arg is parsed — it is, by design, so `-m` always wins.
- **pydantic-ai API stability**: `run_stream_sync` returns the result (not a CM) as of v2. Check the
  release notes before upgrading — serialization format changes break transcript revival. Pin with `<3`.
- **`tool_plain` vs `tool`**: `tool_plain` = no `RunContext`, can't access deps or raise `ModelRetry`.
  Use `tool` when you need either. The decorator lives on the toolset, not the agent.
- **Enums break on unknown values**: pydantic raises `ValidationError` on an unlisted literal — this is
  the point. Add `unknown`/`none` explicitly rather than making a field optional when the model genuinely
  might not know.
- **Docs**: https://ai.pydantic.dev/llms.txt (load this into context for any pydantic-ai work).
