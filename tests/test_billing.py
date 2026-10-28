"""Offline tests for ko.billing (no network — the live path is covered by `ko billing`)."""

from __future__ import annotations

from ko import billing


def test_openrouter_missing_key_is_graceful(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    b = billing.openrouter()
    assert b.provider == "openrouter"
    assert "OPENROUTER_API_KEY" in b.error  # a clean note, not an exception
    assert b.remaining is None


def test_providers_registry():
    assert billing.openrouter in billing.PROVIDERS


def test_key_capped_only_when_the_key_itself_is_spent():
    # The case that cost a debugging session: account credit left, key cap reached.
    assert billing.Balance("openrouter", remaining=44.75, key_limit=100, key_left=0).key_capped
    assert not billing.Balance("openrouter", key_limit=100, key_left=12.5).key_capped
    assert not billing.Balance("openrouter", remaining=0).key_capped  # uncapped key


def test_openrouter_parses_key_cap(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    payloads = {
        "https://openrouter.ai/api/v1/credits": {
            "data": {"total_credits": 150.0, "total_usage": 105.25}
        },
        "https://openrouter.ai/api/v1/key": {
            "data": {
                "label": "sk-or-v1-107...cd0",
                "limit": 100,
                "usage": 101.36,
                "limit_remaining": 0,
                "usage_daily": 19.67,
            }
        },
    }

    class FakeResponse:
        def __init__(self, url):
            self._url = url

        def raise_for_status(self):
            return self

        def json(self):
            return payloads[self._url]

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get(self, url):
            return FakeResponse(url)

    monkeypatch.setattr(billing.httpx, "Client", FakeClient)
    b = billing.openrouter()
    assert b.remaining == 44.75  # account still has credit...
    assert b.key_capped and b.key_limit == 100 and b.key_used == 101.36  # ...but the key is spent
    assert "today $19.67" in b.trend


def test_key_left_derived_when_limit_remaining_absent():
    # Older /key responses omit limit_remaining; we still want a cap verdict.
    b = billing.Balance("openrouter", key_limit=100, key_left=100 - 101.36)
    assert b.key_capped


def test_gateway_without_key_is_a_note(monkeypatch):
    monkeypatch.delenv("VERCEL_AI_GATEWAY_API_KEY", raising=False)
    b = billing.gateway()
    assert b.provider == "ai-gateway"
    assert "VERCEL_AI_GATEWAY_API_KEY" in b.error
