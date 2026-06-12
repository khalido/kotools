"""TMDB — movie/TV lookup + where-to-watch. Needs TMDB_READ_ACCESS_TOKEN.

The quick "is it any good and where can I stream it" check. Watch-provider
data is supplied to TMDB by JustWatch, so regional availability (default AU)
comes free with the same API. Uses the v4 Read Access Token (Bearer) — the
legacy v3 key isn't needed. Ported from my chota-bot TypeScript tool.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import httpx


BASE = "https://api.themoviedb.org/3"
DEFAULT_COUNTRY = "AU"
# offer types in display priority — a title on flatrate doesn't need its rent listing
OFFER_TYPES = ("flatrate", "free", "ads", "rent", "buy")
OFFER_LABELS = {
    "flatrate": "stream",
    "free": "free",
    "ads": "ads",
    "rent": "rent",
    "buy": "buy",
}


@dataclass
class Title:
    kind: str  # "movie" | "tv"
    id: int
    title: str
    year: int | None
    rating: float  # vote_average, 0-10
    overview: str
    popularity: float
    # filled by providers lookup
    providers: dict[str, list[str]] = field(default_factory=dict)  # offer type -> names
    watch_link: str = ""

    @property
    def url(self) -> str:
        return f"https://www.themoviedb.org/{self.kind}/{self.id}"


def _headers() -> dict[str, str]:
    token = os.environ.get("TMDB_READ_ACCESS_TOKEN")
    if not token:
        raise RuntimeError(
            "TMDB_READ_ACCESS_TOKEN is not set. Grab the v4 Read Access Token "
            "from https://www.themoviedb.org/settings/api and export it."
        )
    return {"accept": "application/json", "authorization": f"Bearer {token}"}


def _get(path: str, params: dict | None = None) -> dict:
    resp = httpx.get(f"{BASE}{path}", params=params, headers=_headers(), timeout=10)
    resp.raise_for_status()
    return resp.json()


def _title(r: dict, kind: str) -> Title:
    date = r.get("release_date") if kind == "movie" else r.get("first_air_date")
    return Title(
        kind=kind,
        id=r["id"],
        title=r.get("title") or r.get("name") or "",
        year=int(date[:4]) if date else None,
        rating=r.get("vote_average") or 0.0,
        overview=r.get("overview") or "",
        popularity=r.get("popularity") or 0.0,
    )


def search(query: str, kind: str | None = None, year: int | None = None) -> list[Title]:
    """Search movies and/or TV, merged and sorted by popularity. kind: 'movie'|'tv'|None=both."""
    out: list[Title] = []
    for k in ("movie", "tv") if kind is None else (kind,):
        params: dict = {"query": query, "include_adult": "false", "language": "en-AU"}
        if year:
            params[
                "primary_release_year" if k == "movie" else "first_air_date_year"
            ] = year
        data = _get(f"/search/{k}", params)
        out.extend(_title(r, k) for r in data.get("results") or [])
    return sorted(out, key=lambda t: t.popularity, reverse=True)


def providers(title: Title, country: str = DEFAULT_COUNTRY) -> Title:
    """Fill `title.providers` with regional offers, deduped (best offer per provider wins)."""
    data = _get(f"/{title.kind}/{title.id}/watch/providers")
    region = (data.get("results") or {}).get(country)
    if not region:
        return title
    seen: set[str] = set()
    grouped: dict[str, list[str]] = {}
    for offer in OFFER_TYPES:
        for p in region.get(offer) or []:
            name = p["provider_name"]
            if name not in seen:
                seen.add(name)
                grouped.setdefault(offer, []).append(name)
    title.providers = grouped
    title.watch_link = region.get("link") or ""
    return title


def lookup(
    query: str,
    kind: str | None = None,
    year: int | None = None,
    country: str = DEFAULT_COUNTRY,
) -> tuple[Title | None, list[Title]]:
    """The quick check: top match (most popular across movies+TV) with regional
    providers filled, plus the runner-up matches. Returns (None, []) on no hits."""
    results = search(query, kind=kind, year=year)
    if not results:
        return None, []
    return providers(results[0], country), results[1:]
