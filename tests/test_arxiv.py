"""Smoke tests for ko.arxiv. Live-API tests are opt-in via KO_LIVE_TESTS=1."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import arxiv
import pytest

from ko import arxiv as arxiv_mod
from ko.arxiv import SearchResult, search


def _fake(entry_id: str, days_old: int):
    return SimpleNamespace(
        entry_id=f"http://arxiv.org/abs/{entry_id}",
        title="Title",
        authors=[SimpleNamespace(name="A. Author")],
        published=datetime.now(timezone.utc) - timedelta(days=days_old),
        summary="Abstract",
        pdf_url=f"http://arxiv.org/pdf/{entry_id}",
    )


def test_search_defaults_to_relevance_no_date_filter(monkeypatch):
    captured = {}

    def fake_results(sq):
        captured["sort"] = sq.sort_by
        return iter([_fake("2501.00001", 900), _fake("2501.00002", 30)])  # one old, one new

    monkeypatch.setattr(arxiv_mod, "client_results", fake_results)
    results = search("language model agents", max_results=5)
    assert captured["sort"] == arxiv.SortCriterion.Relevance  # not date-sorted
    assert len(results) == 2  # no since_months → the 900-day-old paper is kept


def test_search_recent_uses_date_sort_and_stops_at_cutoff(monkeypatch):
    captured = {}

    def fake_results(sq):
        captured["sort"] = sq.sort_by
        return iter([_fake("2501.00002", 30), _fake("2501.00001", 900)])  # newest first

    monkeypatch.setattr(arxiv_mod, "client_results", fake_results)
    results = search("cat:cs.LG", recent=True, max_results=5)  # default 18-month window
    assert captured["sort"] == arxiv.SortCriterion.SubmittedDate
    assert len(results) == 1  # breaks at the 900-day-old one


def test_search_relevance_filters_but_does_not_stop(monkeypatch):
    # relevance-sorted results aren't date-ordered, so an old hit is skipped, not a stop signal
    def fake_results(sq):
        return iter([_fake("a", 900), _fake("b", 10), _fake("c", 900), _fake("d", 20)])

    monkeypatch.setattr(arxiv_mod, "client_results", fake_results)
    results = search("topic", since_months=6, max_results=5)
    assert [r.short_id for r in results] == ["b", "d"]  # both in-window ones, old ones skipped


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


@pytest.mark.skipif(
    not os.environ.get("KO_LIVE_TESTS"),
    reason="hits the live (slow, rate-limited) arxiv API; set KO_LIVE_TESTS=1",
)
def test_search_returns_recent_results():
    results = search("large language model", since_months=6, max_results=3)
    assert len(results) > 0
    assert all(r.title for r in results)
    assert all(r.short_id for r in results)
