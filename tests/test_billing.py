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
