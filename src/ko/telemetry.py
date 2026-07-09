"""Opt-in LLM telemetry — pydantic-ai OTel spans → PostHog AI observability.

Instrumented at the pydantic-ai layer so it's provider-agnostic: Gemini-direct,
OpenRouter, and any future provider all emit the same `gen_ai.*` spans, landing
in PostHog as `$ai_generation` events (model, tokens, cost, latency; traces
reconstruct via `$ai_trace_id`). Covers everything that runs an Agent — `ko llm`,
`ko agent`, `ko brief`, `ko yt -s`, `sessions summarize`.

OFF by default. Enabling is an explicit two-step:

    # ~/.config/ko/config.toml
    [telemetry]
    enabled = true            # opt in
    include_content = false   # keep false: metadata only. true mirrors prompts +
                              # responses to PostHog — ko brief pipes gmail/calendar
                              # content through the LLM, so leave this off unless
                              # you really want that in a third-party dashboard.

plus `POSTHOG_API_KEY` (env or [keys]; the PostHog project API key). Optional
`[telemetry] posthog_host` / `POSTHOG_HOST` (default https://us.i.posthog.com).

When disabled, `setup()` is one dict lookup — no OTel imports, nothing emitted.
"""

from __future__ import annotations

import os

from ko import config

_active = False


def enabled() -> bool:
    """The [telemetry] enabled switch (default off)."""
    return config.get("telemetry", "enabled", False) is True


def setup() -> bool:
    """Instrument all pydantic-ai agents if telemetry is enabled and a key is set.

    Idempotent; returns whether instrumentation is active. Called at CLI startup —
    the disabled path must stay effectively free.
    """
    global _active
    if _active:
        return True
    if not enabled():
        return False
    key = os.environ.get("POSTHOG_API_KEY")
    if not key:
        return False  # ko doctor surfaces "enabled but no key"
    host = config.setting("POSTHOG_HOST", "telemetry", "posthog_host", "https://us.i.posthog.com")
    include_content = config.get("telemetry", "include_content", False) is True
    _instrument(key, host.rstrip("/"), include_content)
    _active = True
    return True


def _instrument(key: str, host: str, include_content: bool) -> None:
    """The heavy half — OTel imports live here so the disabled path never pays them."""
    import atexit

    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from pydantic_ai.agent import Agent
    from pydantic_ai.models.instrumented import InstrumentationSettings

    exporter = OTLPSpanExporter(
        endpoint=f"{host}/i/v0/ai/otel",  # PostHog's AI-observability OTLP endpoint
        headers={"Authorization": f"Bearer {key}"},
    )
    provider = TracerProvider()
    provider.add_span_processor(BatchSpanProcessor(exporter))
    # CLI runs are short — flush the batch processor at exit or spans are lost
    atexit.register(provider.shutdown)
    Agent.instrument_all(
        InstrumentationSettings(tracer_provider=provider, include_content=include_content)
    )
