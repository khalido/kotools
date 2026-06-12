"""Offline tests for ko.tmdb — pure logic, no API calls."""

import pytest

from ko import tmdb


def test_title_from_movie_raw():
    t = tmdb._title(
        {"id": 1, "title": "Dune", "release_date": "2021-10-22", "vote_average": 7.8},
        "movie",
    )
    assert (t.title, t.year, t.rating) == ("Dune", 2021, 7.8)
    assert t.url == "https://www.themoviedb.org/movie/1"


def test_title_from_tv_raw_missing_date():
    t = tmdb._title({"id": 2, "name": "Severance", "first_air_date": ""}, "tv")
    assert t.year is None
    assert t.kind == "tv"


def test_providers_dedupe_keeps_best_offer(monkeypatch):
    region = {
        "link": "https://tmdb/watch",
        "flatrate": [{"provider_name": "Netflix"}],
        "rent": [{"provider_name": "Netflix"}, {"provider_name": "Apple TV"}],
        "buy": [{"provider_name": "Apple TV"}],
    }
    monkeypatch.setattr(
        tmdb, "_get", lambda path, params=None: {"results": {"AU": region}}
    )
    t = tmdb.Title(
        kind="movie", id=1, title="x", year=None, rating=0, overview="", popularity=0
    )
    t = tmdb.providers(t)
    # Netflix stays under stream only; Apple TV under rent (its best offer)
    assert t.providers == {"flatrate": ["Netflix"], "rent": ["Apple TV"]}
    assert t.watch_link == "https://tmdb/watch"


def test_headers_require_token(monkeypatch):
    monkeypatch.delenv("TMDB_READ_ACCESS_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="TMDB_READ_ACCESS_TOKEN"):
        tmdb._headers()
