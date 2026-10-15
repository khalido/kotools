"""Offline tests for ko.hn — pure logic only, no Algolia calls."""

from datetime import UTC, datetime

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


def _hit(id_: str, points: int, title_match: bool = True) -> dict:
    level = "full" if title_match else "none"
    return {
        "objectID": id_,
        "title": id_,
        "points": points,
        "created_at_i": 1750000000,
        "_highlightResult": {"title": {"matchLevel": level}},
    }


def test_search_comma_joins_numeric_filters(monkeypatch):
    """Regression: repeated numericFilters params make Algolia keep only the first filter
    (min_comments silently ignored). Algolia documents a comma-joined string."""
    calls = []
    monkeypatch.setattr(hn, "_get", lambda path, params: calls.append(params) or {"hits": []})
    hn.search("rust", n=0, years=1, min_comments=30)
    nf = calls[0]["numericFilters"]
    assert isinstance(nf, str)
    assert nf.startswith("created_at_i>") and nf.endswith(",num_comments>=30,points>=20")


def test_search_ranks_by_points_and_backfills_under_floor(monkeypatch):
    """Algolia ranks word proximity above points; we re-rank by points. Title matches over the
    floor come first; when they're fewer than n, the rest fill in — title matches before
    body-only matches, whatever their points (niche topics)."""
    calls = []

    def fake_get(path, params):
        calls.append(params)
        first = "points>=" in params.get("numericFilters", "")
        assert first == (params.get("restrictSearchableAttributes") == "title")
        if first:
            return {"hits": [_hit("a", 30), _hit("b", 900)]}
        return {"hits": [_hit("b", 900), _hit("body", 50, title_match=False), _hit("c", 5)]}

    monkeypatch.setattr(hn, "_get", fake_get)
    assert [s.id for s in hn.search("x", n=4)] == ["b", "a", "c", "body"]
    assert len(calls) == 2
    calls.clear()
    assert [s.id for s in hn.search("x", n=2)] == ["b", "a"]
    assert len(calls) == 1  # floor already filled n — no second call


def test_search_url_is_all_time_lookup(monkeypatch):
    calls = []
    monkeypatch.setattr(hn, "_get", lambda path, params: calls.append(params) or {"hits": []})
    hn.search("https://www.typeof.net/Iosevka/", years=1)
    assert len(calls) == 1 and "numericFilters" not in calls[0]
    # scheme and www stripped: Algolia matches them as words, so https:// misses http:// posts
    assert calls[0]["query"] == "typeof.net/Iosevka"
    assert calls[0]["restrictSearchableAttributes"] == "url"


def test_search_url_keeps_only_that_url_and_below(monkeypatch):
    """Algolia matches url words, so a bare domain also hits other domains containing them."""
    urls = ["http://www.example.com/", "https://example.com/a", "https://systemd-by-example.com/",
            "https://example.community/x", None]
    hits = [{**_hit(str(i), i), "url": u} for i, u in enumerate(urls)]
    monkeypatch.setattr(hn, "_get", lambda path, params: {"hits": hits})
    assert sorted(s.id for s in hn.search("https://example.com")) == ["0", "1"]


def test_firebase_null_item_is_clean_not_found(monkeypatch):
    monkeypatch.setattr(hn, "_get", lambda path, params=None, base=hn.ALGOLIA: None)
    with pytest.raises(RuntimeError, match=r"HN item 42 not found"):
        hn._firebase_story("42")


def test_front_page_keeps_hn_rank(monkeypatch):
    """Order comes from Firebase topstories; Algolia supplies the data; untagged stories
    (Algolia lag) are fetched from Firebase."""

    def fake_get(path, params=None, base=hn.ALGOLIA):
        if path == "topstories.json":
            return [3, 1, 2, 9]
        if path == "item/2.json":
            return {"id": 2, "title": "new", "score": 7, "descendants": 1, "time": 1750000000}
        assert params["tags"] == "front_page"
        return {"hits": [_hit("1", 500), _hit("3", 40)]}

    monkeypatch.setattr(hn, "_get", fake_get)
    stories = hn.front_page(n=3)
    assert [s.id for s in stories] == ["3", "1", "2"]
    assert stories[2].points == 7


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
    assert s.created_at == datetime.fromtimestamp(1750000000, tz=UTC)


def test_get_404_raises_clean_not_found(monkeypatch):
    """_get on items/<id> with a 404 should raise a clean RuntimeError, not leak httpx internals."""
    import httpx2 as httpx

    def fake_get(url, params, timeout):
        resp = httpx.Response(404, request=httpx.Request("GET", url))
        return resp

    monkeypatch.setattr(httpx, "get", fake_get)
    with pytest.raises(RuntimeError, match=r"HN item 9999999 not found"):
        hn._get("items/9999999")


def test_get_non_404_raises_generic_error(monkeypatch):
    """_get on a non-404 HTTP error should raise a clean 'HN API error <status>' RuntimeError."""
    import httpx2 as httpx

    def fake_get(url, params, timeout):
        resp = httpx.Response(503, request=httpx.Request("GET", url))
        return resp

    monkeypatch.setattr(httpx, "get", fake_get)
    with pytest.raises(RuntimeError, match=r"HN API error 503"):
        hn._get("search")


def test_search_by_date_keeps_algolia_order(monkeypatch):
    """Newest first, no points floor; title matches still lead the body-only fill."""

    def fake_get(path, params):
        assert path == "search_by_date" and "numericFilters" in params
        assert "points>=" not in params["numericFilters"]
        if params.get("restrictSearchableAttributes") == "title":
            return {"hits": [_hit("new", 1), _hit("old", 900)]}
        return {"hits": [_hit("newer-body", 3, title_match=False), _hit("new", 1)]}

    monkeypatch.setattr(hn, "_get", fake_get)
    assert [s.id for s in hn.search("x", by_date=True, n=3)] == ["new", "old", "newer-body"]
