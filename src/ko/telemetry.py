"""Opt-in LLM telemetry — pydantic-ai OTel spans → PostHog AI observability.

Instrumented at the pydantic-ai layer, so every model ko runs (Vercel AI Gateway, Jev on
OpenRouter, the free Gemini key) emits the same `gen_ai.*` spans, landing in PostHog as
`$ai_generation` events (model, tokens, cost, latency; traces reconstruct via
`$ai_trace_id`). The hook is `models.resolve()`, which every LLM call passes through, so
commands that never call a model never load OTel.

OFF by default. Enabling is an explicit two-step:

    # ~/.config/ko/config.toml
    [telemetry]
    enabled = true            # opt in
    include_content = false   # keep false: metadata only. true mirrors prompts +
                              # responses to PostHog — ko brief pipes gmail/calendar
                              # content through the LLM, so leave this off unless
                              # you really want that in a third-party dashboard.

plus `POSTHOG_API_KEY` (env or [keys]; the PostHog project token). Enabled without it is a
MissingKeyError on the first LLM call. Optional `[telemetry] posthog_host` / `POSTHOG_HOST`
(default https://us.i.posthog.com; EU: https://eu.i.posthog.com).

When disabled, `setup()` is one dict lookup — no OTel SDK imports, nothing emitted.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ko import config

if TYPE_CHECKING:
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SpanExporter

KEY = "POSTHOG_API_KEY"
_HINT = (
    "[telemetry] enabled = true needs a PostHog project token: set POSTHOG_API_KEY "
    "(env or [keys] in config.toml), or set enabled = false."
)
_EXPORT_TIMEOUT_S = 5  # per export; caps how long an offline exit can stall on flush

_provider: TracerProvider | None = None


def enabled() -> bool:
    """The [telemetry] enabled switch (default off)."""
    return config.get("telemetry", "enabled", False) is True


def include_content() -> bool:
    """The [telemetry] include_content switch (default off: metadata only)."""
    return config.get("telemetry", "include_content", False) is True


def host() -> str:
    return config.setting("POSTHOG_HOST", "telemetry", "posthog_host", "https://us.i.posthog.com").rstrip("/")


def setup() -> bool:
    """Instrument all pydantic-ai agents if telemetry is enabled; True when active.

    Idempotent. Called by `models.resolve()` before every model call, so the disabled
    path must stay effectively free. Enabled without a key raises MissingKeyError."""
    global _provider
    if _provider is not None:
        return True
    if not enabled():
        return False
    key = config.require_key(KEY, _HINT)
    _provider = _instrument(_exporter(key, host()), include_content())
    return True


def _exporter(key: str, host: str) -> SpanExporter:
    """OTLP/HTTP to PostHog's AI endpoint. The URL is used verbatim (the signal-specific
    form, no /v1/traces appended, which PostHog 404s)."""
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    return OTLPSpanExporter(
        endpoint=f"{host}/i/v0/ai/otel",
        headers={"Authorization": f"Bearer {key}"},
        timeout=_EXPORT_TIMEOUT_S,
    )


def _instrument(exporter: SpanExporter, include_content: bool) -> TracerProvider:
    """The heavy half — OTel SDK imports live here so the disabled path never pays them."""
    import atexit

    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from pydantic_ai.agent import Agent
    from pydantic_ai.models.instrumented import InstrumentationSettings

    provider = TracerProvider()
    provider.add_span_processor(BatchSpanProcessor(exporter))
    # CLI runs are short — flush the batch processor at exit or spans are lost
    atexit.register(provider.shutdown)
    # Binary parts (PDF pages for OCR, yt videos) never go: PostHog caps an export at 4 MB.
    Agent.instrument_all(
        InstrumentationSettings(
            tracer_provider=provider, include_content=include_content, include_binary_content=False
        )
    )
    return provider
