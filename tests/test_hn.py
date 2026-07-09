"""Offline tests for ko.hn — pure logic only, no Algolia calls."""

from datetime import datetime, timezone

import pytest

from ko import hn


def test_strip_html():
    raw = '<p>First &amp; foremost.</p><p>See <a href="https://x.io/full" rel="nofollow">https://x.io/fu...</a> &gt; here</p>'
    assert hn._strip_html(raw) == "First & foremost.\n\nSee https://x.io/full > here"


def test_walk_depth_first_with_cap():
    tree = [
        {
            "author": "a",
            "text": "top",
            "children": [
                {"author": "b", "text": "reply", "children": []},
                {"author": None, "text": None, "children": []},  # deleted — skipped
            ],
        },
        {"author": "c", "text": "second top", "children": []},
    ]
    out: list[hn.Comment] = []
    hn._walk(tree, 0, out, limit=0)
    assert [(c.author, c.depth) for c in out] == [("a", 0), ("b", 1), ("c", 0)]

    capped: list[hn.Comment] = []
    hn._walk(tree, 0, capped, limit=2)
    assert len(capped) == 2


def test_count_comments_is_full_total():
    tree = [
        {
            "text": "top",
            "children": [
                {"text": "reply", "children": [{"text": "deep", "children": []}]},
                {"text": None, "children": []},  # deleted — not counted
            ],
        },
        {"text": "sibling", "children": []},
    ]
    # every text-bearing node anywhere in the tree, independent of any display cap
    assert hn._count_comments(tree) == 4


def test_search_passes_numeric_filters_as_list(monkeypatch):
    """Regression: Algolia 400s on a comma-joined numericFilters string once there's >1 filter;
    httpx must send a list (repeated params). Guards the `created_at_i + num_comments` combo."""
    captured = {}

    def fake_get(path, params):
        captured["path"] = path
        captured["params"] = params
        return {"hits": []}

    monkeypatch.setattr(hn, "_get", fake_get)
    hn.search("rust", since_months=12, min_comments=30)
    nf = captured["params"]["numericFilters"]
    assert isinstance(nf, list) and len(nf) == 2  # NOT a comma-joined string
    assert any(f.startswith("created_at_i>") for f in nf)
    assert "num_comments>=30" in nf


def test_front_page_uses_live_tag(monkeypatch):
    """front_page hits Algolia's `front_page` tag with no date filter — it's the live page,
    not a points-per-window query (that's `top`)."""
    captured = {}

    def fake_get(path, params):
        captured["path"] = path
        captured["params"] = params
        return {"hits": []}

    monkeypatch.setattr(hn, "_get", fake_get)
    hn.front_page(n=15)
    assert captured["params"]["tags"] == "front_page"
    assert captured["params"]["hitsPerPage"] == 15
    assert "numericFilters" not in captured["params"]


def test_story_from_hit():
    s = hn._story(
        {
            "objectID": "123",
            "title": "T",
            "url": None,
            "points": 5,
            "num_comments": None,
            "created_at_i": 1750000000,
        }
    )
    assert s.url is None
    assert s.num_comments == 0
    assert s.hn_url.endswith("id=123")
    assert s.created_at == datetime.fromtimestamp(1750000000, tz=timezone.utc)


def test_get_404_raises_clean_not_found(monkeypatch):
    """_get on items/<id> with a 404 should raise a clean RuntimeError, not leak httpx internals."""
    import httpx

    def fake_get(url, params, timeout):
        resp = httpx.Response(404, request=httpx.Request("GET", url))
        return resp

    monkeypatch.setattr(httpx, "get", fake_get)
    with pytest.raises(RuntimeError, match=r"HN item 9999999 not found"):
        hn._get("items/9999999")


def test_get_non_404_raises_generic_error(monkeypatch):
    """_get on a non-404 HTTP error should raise a clean 'HN API error <status>' RuntimeError."""
    import httpx

    def fake_get(url, params, timeout):
        resp = httpx.Response(503, request=httpx.Request("GET", url))
        return resp

    monkeypatch.setattr(httpx, "get", fake_get)
    with pytest.raises(RuntimeError, match=r"HN API error 503"):
        hn._get("search")
