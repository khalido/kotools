"""ko book (Open Library + Exa reviews) and ko tv --reviews: the review recipes per domain."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from ko import book, exa, tmdb
from ko.cli import app


def _hit(url: str) -> exa.ExaResult:
    return exa.ExaResult(url=url, title="A review", score=0.0, published_date=None, text=None,
                         highlights=["the verdict"])


def test_book_reviews_query_keeps_the_typed_title_and_adds_the_author():
    wager = book.Book("Wager", ["David Grann"], 2023, "/works/OL28738217W")
    assert book.reviews_query("The Wager", wager) == "The Wager David Grann book review"
    assert book.reviews_query("The Wager David Grann", wager) == "The Wager David Grann book review"
    assert book.reviews_query("Some Title", None) == "Some Title book review"


def test_ko_book_sends_the_recipe_and_prints_reviews(monkeypatch):
    seen = {}
    monkeypatch.setattr(book, "find", lambda q: book.Book("Wager", ["David Grann"], 2023, "/works/OL1W"))
    monkeypatch.setattr(exa, "search", lambda q, **kw: seen.update(q=q, **kw) or [_hit("https://nyt.com/r")])
    r = CliRunner().invoke(app, ["book", "The Wager", "--json"])
    assert r.exit_code == 0, r.output
    data = json.loads(r.stdout)
    assert data["book"]["authors"] == ["David Grann"] and data["reviews"][0]["url"] == "https://nyt.com/r"
    assert seen["q"] == "The Wager David Grann book review" and seen["objective"] == book.REVIEWS_OBJECTIVE


def test_ko_book_without_an_open_library_match_still_searches(monkeypatch):
    monkeypatch.setattr(book, "find", lambda q: None)
    monkeypatch.setattr(exa, "search", lambda q, **kw: [_hit("https://x.com/r")])
    r = CliRunner().invoke(app, ["book", "Obscure Zine"])
    assert r.exit_code == 0 and "no Open Library match" in r.stderr and "https://x.com/r" in r.stdout


def test_tv_reviews_query_uses_tmdb_title_year_and_kind():
    film = tmdb.Title("movie", 1, "Dune: Part Two", 2024, 8.1, "", 1.0)
    show = tmdb.Title("tv", 2, "Severance", 2022, 8.4, "", 1.0)
    assert tmdb.reviews_query(film) == "Dune: Part Two 2024 film review"
    assert tmdb.reviews_query(show) == "Severance 2022 TV series review"


def test_ko_tv_reviews_adds_critics(monkeypatch):
    seen = {}
    film = tmdb.Title("movie", 1, "Dune: Part Two", 2024, 8.1, "Paul unites", 1.0)
    monkeypatch.setattr(tmdb, "lookup", lambda *a, **k: (film, []))
    monkeypatch.setattr(exa, "search", lambda q, **kw: seen.update(q=q, **kw) or [_hit("https://variety.com/r")])
    r = CliRunner().invoke(app, ["tv", "Dune Part Two", "-r", "--json"])
    assert r.exit_code == 0, r.output
    assert json.loads(r.stdout)["reviews"][0]["url"] == "https://variety.com/r"
    assert seen["q"] == "Dune: Part Two 2024 film review" and seen["objective"] == tmdb.REVIEWS_OBJECTIVE
    seen.clear()
    r = CliRunner().invoke(app, ["tv", "Dune Part Two", "--json"])
    assert "reviews" not in json.loads(r.stdout) and not seen  # no -r, no Exa call


def test_a_wrong_open_library_match_adds_no_author():
    wrong = book.Book("The Wagered Wife", ["Wilma Counts"], 2001)
    assert not book.matches("The Wager", wrong)
    assert book.reviews_query("The Wager", wrong) == "The Wager book review"


def test_ko_book_when_open_library_fails_says_so_once(monkeypatch):
    def down(q):
        raise RuntimeError("503")

    monkeypatch.setattr(book, "find", down)
    monkeypatch.setattr(exa, "search", lambda q, **kw: [_hit("https://x.com/r")])
    r = CliRunner().invoke(app, ["book", "The Wager"])
    assert r.exit_code == 0 and "lookup failed (503)" in r.stderr and "no Open Library match" not in r.stderr


def test_ko_movie_is_tv_with_movie_only(monkeypatch):
    seen = {}
    film = tmdb.Title("movie", 1, "Dune: Part Two", 2024, 8.1, "", 1.0)
    monkeypatch.setattr(tmdb, "lookup", lambda q, **k: seen.update(q=q, **k) or (film, []))
    r = CliRunner().invoke(app, ["movie", "Dune Part Two", "--json"])
    assert r.exit_code == 0, r.output
    assert seen["kind"] == "movie" and json.loads(r.stdout)["top"]["title"] == "Dune: Part Two"


def test_tmdb_reads_chotas_token_name_too(monkeypatch):
    monkeypatch.delenv("TMDB_READ_ACCESS_TOKEN", raising=False)
    monkeypatch.setenv("TMDB_TOKEN", "eyJ-test")
    assert tmdb._headers()["authorization"] == "Bearer eyJ-test"
