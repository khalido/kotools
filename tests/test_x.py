"""Tests for ko.x — pure logic; live test needs a token AND KO_LIVE_TESTS=1 (reads are paid)."""

import os
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


def test_list_id_cache_hit(monkeypatch, tmp_path):
    cache = tmp_path / "x_cache.json"
    cache.write_text('{"lists": {"ko": {"ai": "777"}}}')
    monkeypatch.setattr(x, "CACHE_FILE", cache)
    # cache hit must not touch the network (no client, no token needed)
    assert x._list_id("AI", "ko") == "777"


def test_resolve_list_id_url_and_name(monkeypatch, tmp_path):
    cache = tmp_path / "x_cache.json"
    cache.write_text('{"lists": {"ko": {"ai": "777"}}}')
    monkeypatch.setattr(x, "CACHE_FILE", cache)
    # bare id and URL resolve without any network / name lookup
    assert x._resolve_list("204975651", "ko") == "204975651"
    assert x._resolve_list("https://x.com/i/lists/204975651", "ko") == "204975651"
    assert x._resolve_list("x.com/i/lists/999", "ko") == "999"
    # a name still routes through the cached name→id map
    assert x._resolve_list("AI", "ko") == "777"


def test_search_routes_recent_vs_archive(monkeypatch):
    calls = {}
    empty = SimpleNamespace(includes=None, data=[])

    def rec(**kw):
        calls["endpoint"] = "recent"
        return [empty]

    def allt(**kw):
        calls["endpoint"] = "all"
        return [empty]

    monkeypatch.setattr(
        x, "_client", lambda: SimpleNamespace(
            posts=SimpleNamespace(search_recent=rec, search_all=allt)
        )
    )
    x.search("q", days=7)
    assert calls["endpoint"] == "recent"  # ≤7 days → recent index
    x.search("q", days=30)
    assert calls["endpoint"] == "all"  # >7 days → full-archive


def test_parse_handle():
    assert x._parse_handle("bcherny") == "bcherny"
    assert x._parse_handle("@bcherny") == "bcherny"
    assert x._parse_handle("https://x.com/bcherny") == "bcherny"
    assert x._parse_handle("https://x.com/bcherny/status/123") == "bcherny"
    assert x._parse_handle("twitter.com/bcherny") == "bcherny"


def test_my_lists_handles_dict_items(monkeypatch, tmp_path):
    # regression: the XDK returns owned/followed list items as plain dicts, not objects
    cache = tmp_path / "x_cache.json"
    monkeypatch.setattr(x, "CACHE_FILE", cache)
    monkeypatch.setattr(x, "_user_id", lambda h: "42")
    page = SimpleNamespace(
        data=[{"id": "204975651", "name": "AI", "description": "ml", "member_count": 120}]
    )  # dict items
    fake = SimpleNamespace(
        users=SimpleNamespace(
            get_owned_lists=lambda **kw: [page],
            get_followed_lists=lambda **kw: [],
        )
    )
    monkeypatch.setattr(x, "_client", lambda: fake)
    lists = x.my_lists("ko")
    assert [(lst.id, lst.name, lst.member_count) for lst in lists] == [("204975651", "AI", 120)]


def test_collect_caps_at_n():
    page = SimpleNamespace(
        includes=SimpleNamespace(users=[SimpleNamespace(id=1, username="a")]),
        data=[
            SimpleNamespace(
                id=i, text=f"t{i}", author_id=1, created_at=None, public_metrics=None
            )
            for i in range(5)
        ],
    )
    out = x._collect([page, page], n=3)
    assert len(out) == 3
    assert out[0].author == "a"


@pytest.mark.skipif(
    not (os.environ.get("X_BEARER_TOKEN") and os.environ.get("KO_LIVE_TESTS")),
    reason="set X_BEARER_TOKEN and KO_LIVE_TESTS=1 for the live (paid) X read",
)
def test_search_returns_posts_live():
    x._client.cache_clear()
    posts = x.search("ai", n=3)
    assert posts
    assert all(p.url.startswith("https://x.com/") for p in posts)
