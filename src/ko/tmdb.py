"""TMDB — movie/TV lookup, details + where-to-watch. Needs TMDB_READ_ACCESS_TOKEN (or TMDB_TOKEN).

The quick "is it any good and where can I stream it" check. Watch-provider
data is supplied to TMDB by JustWatch, so regional availability (default AU)
comes free with the same API. Uses the v4 Read Access Token (Bearer) — the
legacy v3 key isn't needed. Ported from my chota-bot TypeScript tool.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from . import config

if TYPE_CHECKING:
    from .exa import ExaResult

BASE = "https://api.themoviedb.org/3"
DEFAULT_COUNTRY = "AU"
# offer types in display priority — a title on flatrate doesn't need its rent listing
OFFER_TYPES = ("flatrate", "free", "ads", "rent", "buy")
SUBSCRIPTION_OFFERS = ("flatrate", "free", "ads")  # what a subscription covers; rent/buy cost extra
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
    # filled by providers() / details()
    providers: dict[str, list[str]] = field(default_factory=dict)  # offer type -> names
    watch_link: str = ""
    # filled by details()
    runtime: int | None = None  # minutes (a typical episode for TV)
    genres: list[str] = field(default_factory=list)
    certification: str = ""  # the country's rating, e.g. "M", "MA 15+"
    people: list[str] = field(default_factory=list)  # director(s), or a series' creators
    cast: list[str] = field(default_factory=list)  # top billed
    imdb_id: str = ""
    trailer: str = ""  # YouTube URL of the official trailer
    cinema_date: str = ""  # YYYY-MM-DD, the country's cinema release (movies)
    digital_date: str = ""  # YYYY-MM-DD, the country's digital release (movies)
    status: str = ""  # TV: "Returning Series", "Ended"…
    seasons: int | None = None
    next_episode: str = ""  # TV: "S3E1 2026-01-16"

    @property
    def url(self) -> str:
        return f"https://www.themoviedb.org/{self.kind}/{self.id}"


def _headers() -> dict[str, str]:
    import os

    # TMDB_TOKEN is the same v4 token under chota's name (KO's ~/.zshrc.local, 2026-11-15)
    token = os.environ.get("TMDB_READ_ACCESS_TOKEN") or os.environ.get("TMDB_TOKEN") or config.require_key(
        "TMDB_READ_ACCESS_TOKEN",
        "Grab the v4 Read Access Token from https://www.themoviedb.org/settings/api "
        "and export it (TMDB_TOKEN works too).",
    )
    return {"accept": "application/json", "authorization": f"Bearer {token}"}


def _get(path: str, params: dict | None = None) -> dict:
    import httpx2 as httpx

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
    return _fill_providers(title, (data.get("results") or {}).get(country))


def _fill_providers(title: Title, region: dict | None) -> Title:
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
    title.providers = {o: _mine_first(names) if o in SUBSCRIPTION_OFFERS else names for o, names in grouped.items()}
    title.watch_link = region.get("link") or ""
    return title


def _norm(name: str) -> str:
    return "".join(ch for ch in name.lower().replace("+", "plus") if ch.isalnum())


def my_providers() -> list[str]:
    """KO's own services, from `[tmdb] my_providers` in config.toml (e.g. ["Netflix", "Stan"])."""
    return list(config.get("tmdb", "my_providers", []) or [])


def mine(title: Title) -> list[str]:
    """My services this title is on, subscription offers only (renting costs extra anyway)."""
    return [n for o in SUBSCRIPTION_OFFERS for n in title.providers.get(o, []) if is_mine(n)]


def is_mine(name: str) -> bool:
    """Is this provider one of mine? Loose on spelling: "Disney+" matches "Disney Plus"."""
    mine = {_norm(m) for m in my_providers()}
    return any(_norm(name).startswith(m) for m in mine) if mine else False


def _mine_first(names: list[str]) -> list[str]:
    return sorted(names, key=lambda n: not is_mine(n))  # stable: TMDB's order otherwise


def _date(iso: str | None) -> str:
    return (iso or "")[:10]


def details(title: Title, country: str = DEFAULT_COUNTRY) -> Title:
    """Fill providers plus runtime, genres, the country's rating, director or creators, top
    cast, IMDb id, official trailer, and (movies) the country's cinema and digital release
    dates or (TV) status, seasons and next episode. One request: TMDB's append_to_response."""
    extra = "release_dates" if title.kind == "movie" else "content_ratings"
    d = _get(
        f"/{title.kind}/{title.id}",
        {"language": "en-AU", "append_to_response": f"{extra},credits,external_ids,videos,watch/providers",
         "include_video_language": "en,null"},
    )
    _fill_providers(title, ((d.get("watch/providers") or {}).get("results") or {}).get(country))
    title.genres = [g["name"] for g in d.get("genres") or []]
    credits = d.get("credits") or {}
    title.cast = [c["name"] for c in (credits.get("cast") or [])[:4]]
    title.imdb_id = (d.get("external_ids") or {}).get("imdb_id") or d.get("imdb_id") or ""
    videos = [v for v in (d.get("videos") or {}).get("results") or [] if v.get("site") == "YouTube" and v.get("type") == "Trailer"]
    videos.sort(key=lambda v: not v.get("official"))
    title.trailer = f"https://www.youtube.com/watch?v={videos[0]['key']}" if videos else ""
    if title.kind == "movie":
        title.runtime = d.get("runtime") or None
        title.people = [c["name"] for c in credits.get("crew") or [] if c.get("job") == "Director"]
        here = next((r["release_dates"] for r in (d.get("release_dates") or {}).get("results") or []
                     if r.get("iso_3166_1") == country), [])
        title.certification = next((r["certification"] for r in here if r.get("certification")), "")
        title.cinema_date = min((_date(r["release_date"]) for r in here if r.get("type") in (2, 3)), default="")
        title.digital_date = min((_date(r["release_date"]) for r in here if r.get("type") == 4), default="")
    else:
        runs = d.get("episode_run_time") or []  # TMDB mostly stopped filling this; fall back
        title.runtime = runs[0] if runs else (d.get("last_episode_to_air") or {}).get("runtime") or None
        title.people = [c["name"] for c in d.get("created_by") or []]
        title.certification = next((r["rating"] for r in (d.get("content_ratings") or {}).get("results") or []
                                    if r.get("iso_3166_1") == country), "")
        title.status = d.get("status") or ""
        title.seasons = d.get("number_of_seasons")
        nxt = d.get("next_episode_to_air") or {}
        if nxt:
            title.next_episode = f"S{nxt.get('season_number')}E{nxt.get('episode_number')} {nxt.get('air_date') or ''}".strip()
    return title


def now_playing(country: str = DEFAULT_COUNTRY, n: int = 20) -> list[Title]:
    """Films TMDB lists as now playing in the country's cinemas, most popular first. A
    release-date window, not a screenings list: treat it as "probably still showing"."""
    out: list[Title] = []
    page = 1
    while len(out) < n:
        data = _get("/movie/now_playing", {"region": country, "language": "en-AU", "page": page})
        out.extend(_title(r, "movie") for r in data.get("results") or [])
        if page >= (data.get("total_pages") or 1):
            break
        page += 1
    return sorted(out, key=lambda t: t.popularity, reverse=True)[:n]


def lookup(
    query: str,
    kind: str | None = None,
    year: int | None = None,
    country: str = DEFAULT_COUNTRY,
) -> tuple[Title | None, list[Title]]:
    """The quick check: top match (most popular across movies+TV) with its details and
    regional providers filled, plus the runner-up matches. Returns (None, []) on no hits."""
    results = search(query, kind=kind, year=year)
    if not results:
        return None, []
    return details(results[0], country), results[1:]


DEFAULT_REVIEWS = 5

# Same recipe as book.py (WORKLOG 2026-11-15): the query names the kind of page; the
# objective steers deep types and costs nothing on `auto`.
REVIEWS_OBJECTIVE = (
    "Find critics' reviews of this film or TV series. Rank first: reviews in established "
    "publications (newspapers, film and TV magazines, trade press like Variety or The Hollywood "
    "Reporter) and long-form reviews by serious critics. Exclude streaming and retailer pages, "
    "plot summaries, trailers, cast lists and fan wikis. Pull the critic's overall verdict, the "
    "main praise, the main criticism, and the publication and date."
)


def reviews_query(title: Title) -> str:
    """`Dune: Part Two` (2024, movie) -> `Dune: Part Two 2024 film review`: TMDB's exact title
    and year pick out the right one of several films or shows with the same name."""
    kind = "film" if title.kind == "movie" else "TV series"
    return " ".join(str(x) for x in (title.title, title.year, kind, "review") if x)


def reviews(title: Title, n: int = DEFAULT_REVIEWS) -> list[ExaResult]:
    """Critics' reviews of a TMDB title via Exa: highlights per review."""
    from . import exa

    return exa.search(reviews_query(title), n=n, objective=REVIEWS_OBJECTIVE)
