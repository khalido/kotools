"""One-shot LLM call, stdin-aware. The collate step for CLI pipelines.

`ko hn item 123 | ko llm "summarize the debate"` — no tools, no agent loop,
so cost and output are predictable in pipes. Tool-use judgment belongs to
`ko ai`, the second level.

v2 pattern: one model-less Agent, model passed per run. Default model is
cheap-tier Gemini, override via -m, KO_DEFAULT_MODEL, or `[llm] model` in config.toml.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse

from ko import config

# Model tiers — pick by task stakes, not habit. Money matters for a personal tool:
# basic/medium are the cheap workhorses; smart is capable-but-affordable; ultra is
# the only expensive one, reserved for calls where being wrong costs more than tokens.
# Override per-tier in config.toml `[llm]`. Prices $/M in+out, checked 2026-07-31.
# Re-check them when you touch this — the 2026-07-10 figures had drifted up to 82%.
TIERS = {
    "basic": "openrouter:deepseek/deepseek-v4-flash",  # $0.14/$0.28 (v4-pro $0.43/$0.87 if flash quality disappoints)
    "medium": "openrouter:openai/gpt-5.6-luna",      # $0.10/$0.60 — replaced glm-5.2 ($0.97/$3.04), cheaper on both axes
    "smart": "openrouter:~x-ai/grok-latest",         # $2/$6 — alias, tracks the newest grok
    "ultra": "openrouter:openai/gpt-5.6-sol",        # $5/$30 — high-stakes only
}
# Note: medium's input is cheaper than basic's ($0.10 vs $0.14) while its output is dearer
# ($0.60 vs $0.28) — they cross at input ≈ 8× output. basic stays the high-volume default.
FALLBACK_MODEL = TIERS["basic"]


def model_for(tier: str) -> str:
    """The model for a tier ('basic'/'medium'/'smart'/'ultra'): config.toml `[llm] <tier>` → baked default."""
    return config.get("llm", tier) or TIERS[tier]

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

_agent = Agent(instructions=DEFAULT_SYSTEM)


@dataclass
class RunCost:
    """What one run cost: tokens always; USD when known. `source` says how good the
    number is — 'actual' = OpenRouter's billed cost from provider_details (exact),
    'estimate' = genai-prices lookup (approximate, and misses brand-new models)."""

    model: str
    input_tokens: int
    output_tokens: int
    usd: float | None = None
    source: str = ""

    @property
    def note(self) -> str:
        """One stderr-ready line: `[model · 11→43 tok · $0.0012]`."""
        cost = ""
        if self.usd is not None:
            approx = "" if self.source == "actual" else "~"
            amount = f"{approx}${self.usd:.4f}" if self.usd >= 0.0001 else "<$0.0001"
            cost = f" · {amount}"
        return f"[{self.model} · {self.input_tokens}→{self.output_tokens} tok{cost}]"


# Cost of the most recent llm.run() — CLI layers print .note to stderr.
# Module-level (like config._from_config) so run()'s return stays a plain str.
last_cost: RunCost | None = None


def run_cost(messages: list) -> RunCost:
    """Aggregate cost over a run's messages. OpenRouter puts the ACTUAL billed cost in
    provider_details['cost'] — prefer it; fall back to a genai-prices estimate, which
    can fail for brand-new models (then tokens only)."""
    model, tin, tout, usd, source = "?", 0, 0, None, ""
    for m in messages:
        if not isinstance(m, ModelResponse):
            continue
        model = m.model_name or model
        if m.usage:
            tin += m.usage.input_tokens or 0
            tout += m.usage.output_tokens or 0
        actual = (m.provider_details or {}).get("cost")
        if actual is not None:
            usd, source = (usd or 0.0) + float(actual), "actual"
        elif source != "actual":  # only estimate while no actuals seen
            try:
                usd, source = (usd or 0.0) + float(m.cost().total_price), "estimate"
            except Exception:
                pass  # model not in the genai-prices snapshot yet — tokens only
    return RunCost(model=model, input_tokens=tin, output_tokens=tout, usd=usd, source=source)


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
    model: str | None = None,
    system: str | None = None,
    schema: dict | None = None,
) -> str:
    """One prompt (plus optional piped input) → one answer. No tools.

    With `schema` (a validated JSON-Schema object dict, see load_schema), the model is
    constrained to structured output and the return is compact JSON of the object — no
    prose. The cost note (last_cost) is set on either path."""
    global last_cost
    if stdin:
        prompt = f"{prompt}\n\n<input>\n{stdin}\n</input>"
    if schema is not None:
        from pydantic_ai import StructuredDict

        agent = Agent(instructions=system or SCHEMA_SYSTEM, output_type=StructuredDict(schema))
        result = agent.run_sync(prompt, model=model or default_model())
        last_cost = run_cost(result.all_messages())
        return json.dumps(result.output, separators=(",", ":"), default=str)
    agent = Agent(instructions=system) if system else _agent
    result = agent.run_sync(prompt, model=model or default_model())
    last_cost = run_cost(result.all_messages())
    return str(result.output)


# provider prefix -> env var that makes it usable; drives -m autocomplete.
# Only providers whose SDK we actually install (slim extras: google, openrouter
# -> google-genai + openai). Everything else — anthropic, groq, mistral,
# deepseek, grok, cohere — is reachable via `openrouter:<slug>`, one key.
PROVIDER_KEYS = {
    "google": "GEMINI_API_KEY",
    "openai": "OPENAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}


# OpenRouter's catalog is open-ended, so pydantic-ai deliberately omits it from
# known_model_names() (that's a static type for the type-checker). The provider
# reaches the live list via its OpenAI client; we hit the same public /models
# endpoint (no key) directly and cache it so `-m openrouter:<TAB>` can complete.
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
_OPENROUTER_TTL = 24 * 3600  # re-fetch the catalog at most once a day
OPENROUTER_STALE = 7 * 24 * 3600  # past this, `ko models` hints to run --refresh (public: cli_ai reads it)

# Did the most recent refresh_openrouter_models() reach the network? True = fetched fresh,
# False = network failed and we fell back to the existing cache, None = never attempted (no
# key, or a within-TTL cache served without a fetch). Module-level like last_cost so the
# return stays a plain list. Lets `ko models --refresh` warn when a *forced* refresh
# silently served the old cache instead of the fresh catalog it promised — the failure was
# invisible before, which is how a "refresh didn't help" cache can sit stale.
last_refresh_hit_network: bool | None = None


def _openrouter_cache_file() -> Path:
    from ko.dirs import cache_dir

    return cache_dir() / "openrouter_models.json"


def openrouter_cache_age() -> float | None:
    """Age in seconds of the cached OpenRouter catalog, or None if there's no cache file."""
    try:
        return time.time() - _openrouter_cache_file().stat().st_mtime
    except OSError:
        return None


def cached_openrouter_models() -> list[str]:
    """Cached `openrouter:<id>` names. Read-only, never hits network — completion-safe."""
    try:
        return json.loads(_openrouter_cache_file().read_text())["models"]
    except (OSError, ValueError, KeyError):
        return []


def refresh_openrouter_models(ttl: int = _OPENROUTER_TTL, *, force: bool = False) -> list[str]:
    """Refresh the cached OpenRouter catalog if stale, and return it.

    No-op unless OPENROUTER_API_KEY is set (we only surface OR models to users
    actually using it). The /models endpoint itself is public; network errors
    fall back to the existing cache, so this is safe to call from any command.
    `force=True` ignores the TTL — for grabbing a just-released model now.
    """
    global last_refresh_hit_network
    last_refresh_hit_network = None
    if not os.environ.get(PROVIDER_KEYS["openrouter"]):
        return cached_openrouter_models()
    path = _openrouter_cache_file()
    if not force:
        try:
            if path.stat().st_mtime > time.time() - ttl:
                return cached_openrouter_models()
        except OSError:
            pass
    try:
        import httpx

        data = httpx.get(OPENROUTER_MODELS_URL, timeout=10).json()["data"]
    except Exception:
        # Network/endpoint failure. We keep the old cache (and DON'T touch its mtime, so the
        # next call retries), but record the miss so a forced --refresh can say so instead of
        # looking like it worked.
        last_refresh_hit_network = False
        return cached_openrouter_models()
    models = sorted(f"openrouter:{m['id']}" for m in data)
    tmp = path.with_suffix(".tmp")  # atomic: a crash mid-write must not corrupt the cache
    tmp.write_text(json.dumps({"models": models}))
    os.replace(tmp, path)
    last_refresh_hit_network = True
    return models


def available_models(prefix: str = "") -> list[str]:
    """Model names filtered to providers whose env key is set. Feeds -m tab-completion.

    pydantic-ai's static list, plus the cached OpenRouter catalog when
    OPENROUTER_API_KEY is set (populated by refresh_openrouter_models). Reads
    cache only — no network, so completion stays instant.
    """
    from pydantic_ai.models import known_model_names

    names = [
        n
        for n in known_model_names()
        if ":" in n and os.environ.get(PROVIDER_KEYS.get(n.split(":")[0], "")) is not None
    ]
    if os.environ.get(PROVIDER_KEYS["openrouter"]) is not None:
        names += cached_openrouter_models()
    return [n for n in names if n.startswith(prefix)]
