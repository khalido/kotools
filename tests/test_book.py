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
    monkeypatch.setattr(book, "details", lambda b: b)
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


def _fixture(name):
    from pathlib import Path

    return json.loads((Path(__file__).parent / "fixtures" / "tmdb" / name).read_text())


def test_details_fill_the_movie_card_from_a_recorded_response(monkeypatch):
    monkeypatch.setattr(tmdb, "_get", lambda path, params=None: _fixture("movie_wicked_for_good.json"))
    t = tmdb.details(tmdb.Title("movie", 967941, "Wicked: For Good", 2025, 6.6, "", 1.0))
    assert (t.certification, t.runtime, t.people, t.cinema_date) == ("PG", 137, ["Jon M. Chu"], "2025-11-20")
    assert t.cast[:2] == ["Cynthia Erivo", "Ariana Grande"] and t.imdb_id == "tt19847976"
    assert t.trailer.startswith("https://www.youtube.com/watch?v=")  # a Trailer, not one of the featurettes
    assert "Netflix" in t.providers["flatrate"]


def test_details_fill_the_series_card_from_a_recorded_response(monkeypatch):
    monkeypatch.setattr(tmdb, "_get", lambda path, params=None: _fixture("tv_severance.json"))
    t = tmdb.details(tmdb.Title("tv", 95396, "Severance", 2022, 8.4, "", 1.0))
    assert (t.certification, t.status, t.seasons, t.people) == ("MA 15+", "Returning Series", 3, ["Dan Erickson"])
    assert t.next_episode == "S3E1 2027-01-15" and t.cinema_date == ""


def test_my_providers_go_first_and_match_loosely(monkeypatch):
    from ko import config

    monkeypatch.setattr(config, "get", lambda s, k, d=None: ["Stan", "Disney+"] if (s, k) == ("tmdb", "my_providers") else d)
    assert tmdb._mine_first(["Netflix", "Disney Plus", "Stan"]) == ["Disney Plus", "Stan", "Netflix"]
    assert tmdb.is_mine("Stan Premium") and not tmdb.is_mine("Netflix")


def test_movie_now_lists_and_refuses_a_title(monkeypatch):
    films = [tmdb.Title("movie", 1, "Street Fighter", 2026, 6.3, "", 9.0)]
    monkeypatch.setattr(tmdb, "now_playing", lambda country="AU", n=20: films)
    r = CliRunner().invoke(app, ["movie", "--now", "--json"])
    assert r.exit_code == 0 and json.loads(r.stdout)[0]["title"] == "Street Fighter"
    r = CliRunner().invoke(app, ["movie", "Dune", "--now"])
    assert r.exit_code == 2


def test_book_details_reads_both_description_shapes_and_drops_machine_tags(monkeypatch):
    import httpx2 as httpx

    records = {
        "/works/OL1W": {"description": {"type": "/type/text", "value": "A shipwreck."},
                        "subjects": ["nyt:combined-print-and-e-book-nonfiction=2023-05-07", "Shipwrecks", "Mutiny"]},
        "/works/OL2W": {"description": "A plain string.", "subjects": []},
    }

    def fake_get(url, **kw):
        key = url.removeprefix(book.OPEN_LIBRARY).removesuffix(".json")
        return httpx.Response(200, json=records[key], request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", fake_get)
    b = book.details(book.Book("Wager", key="/works/OL1W"))
    assert b.description == "A shipwreck." and b.subjects == ["Shipwrecks", "Mutiny"]
    assert book.details(book.Book("X", key="/works/OL2W")).description == "A plain string."


def test_a_failed_description_keeps_the_book(monkeypatch):
    def down(b):
        raise RuntimeError("503")

    monkeypatch.setattr(book, "find", lambda q: book.Book("Wager", ["David Grann"], 2023, "/works/OL1W"))
    monkeypatch.setattr(book, "details", down)
    seen = {}
    monkeypatch.setattr(exa, "search", lambda q, **kw: seen.update(q=q) or [_hit("https://x.com/r")])
    r = CliRunner().invoke(app, ["book", "The Wager", "--json"])
    assert r.exit_code == 0 and "description unavailable" in r.stderr
    assert json.loads(r.stdout)["book"]["url"].endswith("/works/OL1W")
    assert seen["q"] == "The Wager David Grann book review"  # the author survived


def test_only_subscription_offers_get_the_tick(monkeypatch):
    from ko import config

    monkeypatch.setattr(config, "get", lambda s, k, d=None: ["Apple TV"] if (s, k) == ("tmdb", "my_providers") else d)
    t = tmdb.Title("movie", 1, "X", 2026, 7.0, "", 1.0, providers={"flatrate": ["Netflix"], "rent": ["Apple TV"]})
    assert tmdb.mine(t) == []  # renting on Apple TV isn't covered by the subscription
