"""Offline tests for ko.x — pure logic, no API calls."""

from types import SimpleNamespace

import pytest

from ko import x


def test_post_from_api_object():
    raw = SimpleNamespace(
        id=123,
        text="hello x",
        author_id=42,
        created_at="2026-06-11T10:00:00.000Z",
        public_metrics={"like_count": 7, "retweet_count": 2, "reply_count": 1},
    )
    p = x._post(raw, {"42": "kodev"})
    assert p.author == "kodev"
    assert p.likes == 7 and p.reposts == 2
    assert p.url == "https://x.com/kodev/status/123"
    assert p.created_at.year == 2026


def test_post_unknown_author_still_links():
    raw = SimpleNamespace(
        id=9, text="t", author_id=1, created_at=None, public_metrics=None
    )
    p = x._post(raw, {})
    # x.com/i/status/<id> resolves regardless of username
    assert p.url == "https://x.com/i/status/9"


def test_client_requires_token(monkeypatch):
    monkeypatch.delenv("X_BEARER_TOKEN", raising=False)
    x._client.cache_clear()
    with pytest.raises(RuntimeError, match="X_BEARER_TOKEN"):
        x._client()
