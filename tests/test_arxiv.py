"""Smoke tests for ko.arxiv. Some tests hit the live arxiv API."""

from __future__ import annotations

from datetime import datetime, timezone

from ko.arxiv import SearchResult, search


def test_short_id_extracts_from_entry_url():
    r = SearchResult(
        id="http://arxiv.org/abs/2604.02460v1",
        title="x",
        authors=[],
        published=datetime.now(timezone.utc),
        summary="",
        pdf_url="",
    )
    assert r.short_id == "2604.02460v1"


def test_search_returns_recent_results():
    results = search("large language model", since_months=6, max_results=3)
    assert len(results) > 0
    assert all(r.title for r in results)
    assert all(r.short_id for r in results)
