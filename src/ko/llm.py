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
from pathlib import Path

from pydantic_ai import Agent

from ko import config

FALLBACK_MODEL = "google:gemini-3.5-flash"

DEFAULT_SYSTEM = (
    "You are ko, a one-shot CLI helper used by humans and AI agents. "
    "Answer the prompt directly and concisely in markdown. When input text "
    "is provided after the prompt, treat it as the material to work on. "
    "No preamble, no closing questions — just the answer."
)

_agent = Agent(instructions=DEFAULT_SYSTEM)


def default_model() -> str:
    return config.setting("KO_DEFAULT_MODEL", "llm", "model", FALLBACK_MODEL)


def run(
    prompt: str,
    stdin: str | None = None,
    model: str | None = None,
    system: str | None = None,
) -> str:
    """One prompt (plus optional piped input) → one answer. No tools."""
    if stdin:
        prompt = f"{prompt}\n\n<input>\n{stdin}\n</input>"
    agent = Agent(instructions=system) if system else _agent
    result = agent.run_sync(prompt, model=model or default_model())
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


def _openrouter_cache_file() -> Path:
    from ko.dirs import cache_dir

    return cache_dir() / "openrouter_models.json"


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
        return cached_openrouter_models()
    models = sorted(f"openrouter:{m['id']}" for m in data)
    tmp = path.with_suffix(".tmp")  # atomic: a crash mid-write must not corrupt the cache
    tmp.write_text(json.dumps({"models": models}))
    os.replace(tmp, path)
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
