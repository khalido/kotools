"""One-shot LLM call, stdin-aware. The collate step for CLI pipelines.

`ko hn item 123 | ko llm "summarize the debate"` — no tools, no agent loop,
so cost and output are predictable in pipes. Tool-use judgment belongs to
`ko ai`, the second level.

v2 pattern: one model-less Agent, model passed per run. Default model is
the basic tier, override via -m, KO_DEFAULT_MODEL, or `[llm] model` in config.toml.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from ko import config
from ko.models import gateway_price, model_for, resolve

if TYPE_CHECKING:
    from pydantic_ai import Agent
    from pydantic_ai.models import Model

# `ko sum`'s default: fast, cheap, long-context. Gemini Flash-Lite is strong on content and
# transcripts (field-tested in koevguide). Override per run with -m, or `[llm] sum` in config.toml.
SUM_MODEL = "vercel:google/gemini-3.5-flash-lite"


def sum_model() -> str:
    """`ko sum`'s model: KO_SUM_MODEL -> [llm] sum -> baked SUM_MODEL."""
    return config.setting("KO_SUM_MODEL", "llm", "sum", SUM_MODEL)


DEFAULT_SYSTEM = (
    "You are ko, a one-shot CLI helper used by humans and AI agents. "
    "Answer the prompt directly and concisely in markdown. When input text "
    "is provided after the prompt, treat it as the material to work on. "
    "No preamble, no closing questions — just the answer."
)

# Used when --schema constrains the output: the markdown instruction above would fight the
# structured shape, so extraction gets its own neutral frame. Output structure is enforced
# by the output_type; this only steers the content.
SCHEMA_SYSTEM = (
    "You are ko, a one-shot CLI extractor used by humans and AI agents. "
    "Populate the required structure from the prompt and any input text provided after it. "
    "Be accurate; leave a field empty (or omit it) only when the input doesn't supply it."
)

@lru_cache(maxsize=1)
def _default_agent() -> Agent:
    """The model-less default agent — built on first use: importing pydantic-ai costs ~0.5 s,
    and every `ko` command imports this module for its constants."""
    from pydantic_ai import Agent

    return Agent(instructions=DEFAULT_SYSTEM)


@dataclass
class RunCost:
    """What one run cost: tokens always; USD when known. `source` says how good the
    number is — 'actual' = OpenRouter's billed cost from provider_details (exact),
    'estimate' = the gateway's listed price or a genai-prices lookup (approximate), 'free' =
    Google's free tier (`models.free_gemini`)."""

    model: str
    input_tokens: int
    output_tokens: int
    usd: float | None = None
    source: str = ""

    @property
    def note(self) -> str:
        """One stderr-ready line: `[model · 11→43 tok · $0.0012]`."""
        cost = ""
        if self.source == "free":
            cost = " · free tier"
        elif self.usd is not None:
            approx = "" if self.source == "actual" else "~"
            amount = f"{approx}${self.usd:.4f}" if self.usd >= 0.0001 else "<$0.0001"
            cost = f" · {amount}"
        return f"[{self.model} · {self.input_tokens}→{self.output_tokens} tok{cost}]"


# Cost of the most recent llm.run() — CLI layers print .note to stderr.
# Module-level (like config._from_config) so run()'s return stays a plain str.
last_cost: RunCost | None = None


class TruncatedOutput(RuntimeError):
    """The model stopped before finishing — partial text must never pass as an answer."""


def raise_if_cut_short(messages: list, model: str) -> None:
    """A provider can abort mid-generation and still return 200 with partial text and
    finish_reason "error" (seen 2026-10-01: Gemini Flash via OpenRouter on a page image, cut
    mid-sentence, 0 usage). pydantic-ai hands that back as a normal result; ko refuses it."""
    from pydantic_ai.messages import ModelResponse

    last = next((m for m in reversed(messages) if isinstance(m, ModelResponse)), None)
    if last is None or last.finish_reason not in ("error", "length", "content_filter"):
        return
    why = {
        "error": "the provider errored mid-answer",
        "length": "it hit the output-token limit",
        "content_filter": "a content filter stopped it",
    }[last.finish_reason]
    upstream = (last.provider_details or {}).get("downstream_provider")
    via = f" (upstream: {upstream})" if upstream else ""
    raise TruncatedOutput(f"{model} stopped early — {why}{via}; partial output discarded. Retry or try another -m.")


def run_cost(messages: list) -> RunCost:
    """Aggregate cost over a run's messages. OpenRouter puts the ACTUAL billed cost in
    provider_details['cost'] — prefer it. The gateway reports cost only ~20 s later
    (/v1/generation), so its calls are estimated from the cached catalog price (all input at
    full price — cache-read discounts ignored, so it errs high); anything
    else falls back to genai-prices, which can miss brand-new models (then tokens only).

    Also adds the run onto the command's log event (tokens, cost, requests, tool calls) — so
    call it once per run, over that run's new messages only."""
    from pydantic_ai.messages import ModelResponse, ToolCallPart

    model, tin, tout, usd, source, requests, tools = "?", 0, 0, None, "", 0, 0
    for m in messages:
        if not isinstance(m, ModelResponse):
            continue
        requests += 1
        tools += sum(isinstance(p, ToolCallPart) for p in m.parts)
        model = m.model_name or model
        if m.usage:
            tin += m.usage.input_tokens or 0
            tout += m.usage.output_tokens or 0
        if m.provider_name == "google-free":  # Google's free tier bills nothing
            usd, source = usd or 0.0, source or "free"
            continue
        actual = (m.provider_details or {}).get("cost")
        if actual is not None:
            usd, source = (usd or 0.0) + float(actual), "actual"
        elif source != "actual":  # only estimate while no actuals seen
            price = gateway_price(m.model_name or "") if m.provider_name == "vercel" else None
            if price and m.usage:
                usd = (usd or 0.0) + (m.usage.input_tokens or 0) * price[0] + (m.usage.output_tokens or 0) * price[1]
                source = "estimate"
                continue
            try:
                usd, source = (usd or 0.0) + float(m.cost().total_price), "estimate"
            except Exception:
                pass  # model not in the genai-prices snapshot yet — tokens only
    from ko import logs

    prior = logs.get("cost_source")
    logs.add(model=model, cost_source=source if prior in (None, source) else "mixed")
    logs.inc(llm_requests=requests, tool_calls=tools, input_tokens=tin, output_tokens=tout, cost_usd=usd)
    return RunCost(model=model, input_tokens=tin, output_tokens=tout, usd=usd, source=source)


# --- decision models (Jev): typed answers with probabilities, not text ---------------------

# `-m jev` is TypeSafe's Jev through OpenRouter, on the key ko already has; TYPESAFE_API_KEY, when
# set, goes direct instead. `system-one:` and `openai-decisions:` strings pass to pydantic-ai as is.
DECISION_PREFIXES = ("typesafe:", "system-one:", "openai-decisions:")


def is_decision_model(model: str | Model | None) -> bool:
    return isinstance(model, str) and (model == "jev" or model.startswith(DECISION_PREFIXES))


@dataclass
class Decision:
    """One judgement. A decision model fills `probabilities` (every option, or every score level
    by index) and `confidence`; a language model asked the same question leaves them empty."""

    kind: str  # "yes_no" | "choice" | "score" | "tags"
    answer: float | str | list[str]  # P(yes); the pick; the position on the scale (0 = first level); or the tags that apply
    probabilities: dict[str, float]
    confidence: float | None
    level: str | None  # score only: the nearest level's name
    model: str


def parse_options(spec: str, *, kind: str) -> list[tuple[str, str]]:
    """`"reply=needs an answer,read,archive"` → [(name, meaning)], meaning "" when not given.
    Fails before any call: too few/many options, a blank or repeated name."""
    opts = [(n.strip(), d.strip()) for n, _, d in (part.partition("=") for part in spec.split(","))]
    names = [n for n, _ in opts]
    lo, hi = {"choice": (2, 255), "score": (2, 10), "tags": (1, 255)}[kind]
    if not lo <= len(opts) <= hi:
        raise ValueError(f"--{kind} needs {lo} to {hi} comma-separated options, got {len(opts)}")
    if any(not n or " " in n for n in names) or len(set(names)) != len(names):
        raise ValueError(
            f"--{kind}: names must be single words and differ, and a meaning can't contain a comma: {spec!r}"
        )
    return opts


def decide(
    question: str,
    text: str,
    *,
    model: str | None = None,
    choice: list[tuple[str, str]] | None = None,
    score: list[tuple[str, str]] | None = None,
    tags: list[tuple[str, str]] | None = None,
) -> Decision:
    """Ask one typed question about `text`: yes/no by default; else pick one of `choice`, place it
    on the ordered `score` levels (lowest first), or ask yes/no of each of `tags`. On a decision
    model every option comes back with its probability; any other model answers the same type
    without them."""
    global last_cost
    from typing import Annotated

    from pydantic import Field
    from pydantic_ai import Agent, StructuredDict
    from pydantic_ai.output import Choices

    if choice:
        kind, instructions = "choice", None
        output = Choices({n: d or n for n, d in choice}, description=question)
    elif score:
        kind, instructions = "score", None
        levels = [{"const": i, "description": d or n} for i, (n, d) in enumerate(score)]
        output = StructuredDict(
            {"type": "object", "properties": {"level": {"description": question, "anyOf": levels}}, "required": ["level"]},
            name="Score",
        )
    elif tags:
        kind, instructions = "tags", None
        items = [{"const": n, "description": d or n} for n, d in tags]
        output = StructuredDict(
            {"type": "object", "properties": {"tags": {"type": "array", "description": question, "items": {"anyOf": items}}}, "required": ["tags"]},
            name="Tags",
        )
    else:
        kind, instructions = "yes_no", question
        output = Annotated[float, Field(ge=0, le=1, description="probability that the answer is yes")]
    result = Agent(resolve(model or default_model()), output_type=output, instructions=instructions).run_sync(text)
    last_cost = run_cost(result.all_messages())
    details = result.response.provider_details or {}
    field = {"score": "level", "tags": "tags"}.get(kind, "response")
    probs = (details.get("probabilities") or {}).get(field, {})
    confidence = (details.get("confidence") or {}).get(field)
    name = result.response.model_name or str(model)
    if kind == "score":
        position = (details.get("scores") or {}).get("level", result.output["level"])
        nearest = score[result.output["level"]][0]  # pydantic-ai's rounding of the position, not ours
        return Decision(kind, round(float(position), 2), probs, confidence, nearest, name)
    if kind == "tags":
        return Decision(kind, result.output["tags"], probs, confidence, None, name)
    return Decision(kind, result.output, probs, confidence, None, name)


def default_model() -> str:
    """One-shot default: KO_DEFAULT_MODEL → `[llm] model` → the basic tier."""
    return config.setting("KO_DEFAULT_MODEL", "llm", "model", model_for("basic"))


def load_schema(path: Path) -> dict:
    """Read a JSON Schema file for `ko llm --schema` and confirm it's usable.

    Raises ValueError (→ the CLI reports it as a usage error) when the file isn't valid
    JSON, isn't a JSON object, or isn't an *object* schema (pydantic-ai's StructuredDict
    only constrains `type: object` schemas). Returns the parsed schema dict on success.
    """
    try:
        schema = json.loads(Path(path).read_text())
    except (OSError, ValueError) as e:
        raise ValueError(f"--schema {path}: not valid JSON ({e})") from None
    if not isinstance(schema, dict):
        # ValueError, not TypeError: the CLI maps ValueError from here to a usage error (exit 2).
        raise ValueError(  # noqa: TRY004
            f"--schema {path}: must be a JSON object (a JSON Schema), not {type(schema).__name__}"
        )
    from pydantic_ai import StructuredDict
    from pydantic_ai.exceptions import UserError

    try:
        StructuredDict(schema)  # validates it's an object schema; raises UserError otherwise
    except UserError as e:
        raise ValueError(f"--schema {path}: {e}") from None
    return schema


def run(
    prompt: str,
    stdin: str | None = None,
    model: str | Model | None = None,  # a Model for routes resolve() doesn't build (models.free_gemini)
    system: str | None = None,
    schema: dict | None = None,
    attachments: list | None = None,
) -> str:
    """One prompt (plus optional piped input) → one answer. No tools.

    With `schema` (a validated JSON-Schema object dict, see load_schema), the model is
    constrained to structured output and the return is compact JSON of the object — no
    prose. `attachments` are pydantic-ai multimodal parts (BinaryContent — images, audio,
    video) sent alongside the prompt; the model must support the media type, which is its
    error to raise, not ours to predict. The cost note (last_cost) is set on every path."""
    global last_cost
    if is_decision_model(model) and schema is None:
        raise ValueError(f"{model} answers typed questions, not prose: use `ko judge`")
    if stdin:
        # Input BEFORE the prompt — simonw-llm's fragment convention (and what the -f help
        # promises): the stable prefix (documents) ahead of the variable suffix (the question)
        # is also the shape provider prompt-caching rewards.
        prompt = f"<input>\n{stdin}\n</input>\n\n{prompt}"
    user_prompt = [prompt, *attachments] if attachments else prompt
    from pydantic_ai import Agent

    if schema is not None:
        from pydantic_ai import StructuredDict

        agent = Agent(instructions=system or SCHEMA_SYSTEM, output_type=StructuredDict(schema))
        result = agent.run_sync(user_prompt, model=resolve(model or default_model()))
        last_cost = run_cost(result.all_messages())
        raise_if_cut_short(result.all_messages(), model or default_model())
        return json.dumps(result.output, separators=(",", ":"), default=str)
    agent = Agent(instructions=system) if system else _default_agent()
    result = agent.run_sync(user_prompt, model=resolve(model or default_model()))
    last_cost = run_cost(result.all_messages())
    raise_if_cut_short(result.all_messages(), model or default_model())
    return str(result.output)
