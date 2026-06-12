"""One-shot LLM call, stdin-aware. The collate step for CLI pipelines.

`ko hn item 123 | ko llm "summarize the debate"` — no tools, no agent loop,
so cost and output are predictable in pipes. Tool-use judgment belongs to
`ko ai`, the second level.

v2 pattern: one model-less Agent, model passed per run. Default model is
cheap-tier Gemini, override via -m or KO_DEFAULT_MODEL.
"""

from __future__ import annotations

import os

from pydantic_ai import Agent


FALLBACK_MODEL = "google:gemini-3.5-flash"

DEFAULT_SYSTEM = (
    "You are ko, a one-shot CLI helper used by humans and AI agents. "
    "Answer the prompt directly and concisely in markdown. When input text "
    "is provided after the prompt, treat it as the material to work on. "
    "No preamble, no closing questions — just the answer."
)

_agent = Agent(instructions=DEFAULT_SYSTEM)


def default_model() -> str:
    return os.environ.get("KO_DEFAULT_MODEL", FALLBACK_MODEL)


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


# provider prefix -> env var that makes it usable; drives -m autocomplete
PROVIDER_KEYS = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "openai-chat": "OPENAI_API_KEY",
    "google": "GEMINI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "groq": "GROQ_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "grok": "XAI_API_KEY",
    "cohere": "CO_API_KEY",
}


def available_models(prefix: str = "") -> list[str]:
    """Known model names filtered to providers whose env key is set. Feeds tab-completion."""
    from pydantic_ai.models import known_model_names

    return [
        n
        for n in known_model_names()
        if ":" in n
        and os.environ.get(PROVIDER_KEYS.get(n.split(":")[0], "")) is not None
        and n.startswith(prefix)
    ]
