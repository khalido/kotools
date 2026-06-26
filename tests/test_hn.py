"""Offline tests for ko.hn — pure logic only, no Algolia calls."""

from datetime import datetime, timezone

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
