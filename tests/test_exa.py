"""Smoke + unit tests for ko.exa. Live tests skip if EXA_API_KEY is unset."""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from ko import exa
from ko.cli import app
from ko.exa import Contents, _client, get_contents, search

runner = CliRunner()


# --- fakes -------------------------------------------------------------------


class FakeClient:
    """Records the kwargs ko passes and returns canned responses."""

    def __init__(self):
        self.search_kwargs = None
        self.contents_kwargs = None

    def search(self, query, **kwargs):
        self.search_kwargs = {"query": query, **kwargs}
        item = SimpleNamespace(
            url="https://example.com/a",
            title="A title",
            score=0.42,
            published_date="2026-01-01",
            text="full text",
            summary="a blurb",
            highlights=["sentence one.", "sentence two."],
        )
        return SimpleNamespace(results=[item])

    def get_contents(self, urls, **kwargs):
        self.contents_kwargs = {"urls": urls, **kwargs}
        results = [
            SimpleNamespace(url="https://ok.com", text="hello"),
            SimpleNamespace(url="https://dead.com", text=""),  # present-but-empty = failed
        ]
        statuses = [
            SimpleNamespace(id="https://ok.com", status="success", source="cache"),
            SimpleNamespace(id="https://dead.com", status="error", source="live"),
        ]
        return SimpleNamespace(results=results, statuses=statuses)


@pytest.fixture
def fake_client(monkeypatch):
    fc = FakeClient()
    monkeypatch.setattr(exa, "_client", lambda: fc)
    return fc


# --- unit: client / library --------------------------------------------------


def test_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="EXA_API_KEY"):
        _client()


def test_search_passes_type_category_maxage(fake_client):
    results = search(
        "q",
        type="fast",
        category="research paper",
        max_age_hours=0,
        with_highlights=True,
        with_summary=False,
    )
    kw = fake_client.search_kwargs
    assert kw["type"] == "fast"
    assert kw["category"] == "research paper"
    assert kw["num_results"] == exa.DEFAULT_MAX_RESULTS
    # highlights + max_age_hours ride inside the contents dict, not top-level
    assert kw["contents"]["highlights"]["query"] == "q"  # bounded + query-biased, not True
    assert kw["contents"]["highlights"]["max_characters"] == exa.DEFAULT_HIGHLIGHT_CHARS
    assert kw["contents"]["max_age_hours"] == 0
    assert "summary" not in kw["contents"]
    assert results[0].highlights == ["sentence one.", "sentence two."]


def test_search_omits_unset_options(fake_client):
    search("q")  # defaults: summary only, no type/category/max_age
    kw = fake_client.search_kwargs
    assert "type" not in kw
    assert "category" not in kw
    assert "summary" in kw["contents"]
    assert "max_age_hours" not in kw["contents"]


def test_get_contents_returns_statuses(fake_client):
    c = get_contents(["https://ok.com", "https://dead.com"])
    assert isinstance(c, Contents)
    assert c.texts["https://ok.com"] == "hello"
    assert c.texts["https://dead.com"] == ""  # failed URL comes back empty
    dead = next(s for s in c.statuses if s.url == "https://dead.com")
    assert dead.status == "error"


# --- CLI: flag validation (exit 2 usage) -------------------------------------


def test_cli_bad_type_exits_2():
    r = runner.invoke(app, ["exa", "search", "q", "--type", "bogus"])
    assert r.exit_code == 2
    assert "invalid --type" in r.output


def test_cli_bad_category_exits_2():
    r = runner.invoke(app, ["exa", "search", "q", "--category", "bogus"])
    assert r.exit_code == 2
    assert "invalid --category" in r.output


def test_cli_get_surfaces_failed_urls(fake_client):
    r = runner.invoke(app, ["exa", "get", "https://ok.com", "https://dead.com"])
    assert r.exit_code == 0
    assert "hello" in r.output  # the good URL's content
    # the dead URL is reported (with its status) on stderr, mixed into output here
    assert "no content for" in r.output
    assert "dead.com" in r.output


# --- live (skipped without a key) --------------------------------------------


@pytest.mark.skipif(
    not os.environ.get("EXA_API_KEY"),
    reason="EXA_API_KEY not set; skipping live Exa test",
)
def test_search_returns_results():
    results = search("claude code anthropic", n=3, with_text=False)
    assert len(results) > 0
    assert all(r.url for r in results)
    assert any(r.title for r in results)  # a title-less result is legal; all-empty means parsing broke
