"""Smoke tests for ko.exa. Live tests skip if EXA_API_KEY is unset."""

from __future__ import annotations

import os

import pytest

from ko.exa import _client, search


def test_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="EXA_API_KEY"):
        _client()


@pytest.mark.skipif(
    not os.environ.get("EXA_API_KEY"),
    reason="EXA_API_KEY not set; skipping live Exa test",
)
def test_search_returns_results():
    results = search("claude code anthropic", n=3, with_text=False)
    assert len(results) > 0
    assert all(r.url for r in results)
    assert all(r.title for r in results)
