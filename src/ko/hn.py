"""Hacker News via the Algolia API (hn.algolia.com). No auth, no key.

Four primitives matching how I actually read HN:
- `top` — top stories by points in a day window (my hckrnews.com top-10/20 habit;
  top-by-points ≈ hckrnews's front-page filter, since stories can't score without front-paging)
- `front_page` — what's on the front page *right now*, in HN's rank order (Firebase's
  `topstories` is the only source of rank; Algolia's `front_page` tag has the data but no order)
- `search` — matches from the last year, ranked by points (Algolia ranks word proximity
  above points, so its own order surfaces 2-point stories over 2000-point ones)
- `item` — one story + its comment tree as readable text

Algolia beats RSS and Firebase here: points + comment counts + date filters + whole comment
trees in one JSON call (Firebase is one request per item, and has no search).
"""

from __future__ import annotations

import html
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx2 as httpx

ALGOLIA = "https://hn.algolia.com/api/v1"
FIREBASE = "https://hacker-news.firebaseio.com/v0"
DEFAULT_TOP_N = 10
DEFAULT_SEARCH_N = 10
DEFAULT_YEARS = 1
SEARCH_POOL = 30  # relevance hits fetched, then re-ranked by points
SEARCH_MIN_POINTS = 20  # drops never-front-paged noise; retried without when it leaves < n
DEFAULT_MAX_COMMENTS = 50


@dataclass
class Story:
    id: str
    title: str
    url: str | None  # None for Ask HN / text posts
    points: int
    num_comments: int
    created_at: datetime

    @property
    def hn_url(self) -> str:
        return f"https://news.ycombinator.com/item?id={self.id}"


@dataclass
class Comment:
    author: str
    text: str
    depth: int  # 0 = top-level


def _get(path: str, params: dict | None = None, base: str = ALGOLIA) -> dict | list:
    resp = httpx.get(f"{base}/{path}", params=params, timeout=30)
    try:
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        status = e.response.status_code
        # Extract a user-friendly id from the path for item lookups
        if status == 404 and path.startswith("items/"):
            item_id = path.split("/", 1)[1]
            raise RuntimeError(f"HN item {item_id} not found") from None
        raise RuntimeError(f"HN API error {status}") from None
    return resp.json()


def _story(hit: dict) -> Story:
    return Story(
        id=str(hit["objectID"]),
        title=hit.get("title") or "",
        url=hit.get("url") or None,
        points=hit.get("points") or 0,
        num_comments=hit.get("num_comments") or 0,
        created_at=datetime.fromtimestamp(hit["created_at_i"], tz=UTC),
    )


def top(n: int = DEFAULT_TOP_N, days: int = 1) -> list[Story]:
    """Top stories by points over the last `days` days. `top(10)` ≈ hckrnews top-10."""
    cutoff = int((datetime.now(UTC) - timedelta(days=days)).timestamp())
    data = _get(
        "search",  # no query + popularity ranking ≈ points; we re-sort to guarantee it
        {
            "tags": "story",
            "numericFilters": f"created_at_i>{cutoff}",
            "hitsPerPage": n,
        },
    )
    stories = [_story(h) for h in data["hits"]]
    return sorted(stories, key=lambda s: s.points, reverse=True)


def front_page(n: int = DEFAULT_TOP_N) -> list[Story]:
    """Stories on the HN front page *right now*, in HN's own rank order.

    Rank comes from Firebase `topstories`; story data from Algolia's `front_page` tag (one call
    instead of one per item). Stories Algolia hasn't tagged yet are fetched from Firebase.
    """
    with ThreadPoolExecutor() as pool:
        ranked = pool.submit(_get, "topstories.json", base=FIREBASE)
        tagged = pool.submit(_get, "search", {"tags": "front_page", "hitsPerPage": 100})
        ids = [str(i) for i in ranked.result()[:n]]
        by_id = {h["objectID"]: _story(h) for h in tagged.result()["hits"]}
        missing = [i for i in ids if i not in by_id]
        for story in pool.map(_firebase_story, missing):
            by_id[story.id] = story
    return [by_id[i] for i in ids]


def _firebase_story(item_id: str) -> Story:
    d = _get(f"item/{item_id}.json", base=FIREBASE)
    if not d or "time" not in d:  # Firebase answers an unknown id with 200 + null, not a 404
        raise RuntimeError(f"HN item {item_id} not found")
    return Story(
        id=str(d["id"]),
        title=d.get("title") or "",
        url=d.get("url") or None,
        points=d.get("score") or 0,
        num_comments=d.get("descendants") or 0,
        created_at=datetime.fromtimestamp(d["time"], tz=UTC),
    )


_URL_PREFIX_RE = re.compile(r"^https?://(www\.)?")


def _is_url(query: str) -> bool:
    return query.startswith(("http://", "https://"))


def search(
    query: str,
    n: int = DEFAULT_SEARCH_N,
    years: int = DEFAULT_YEARS,
    by_date: bool = False,
    min_comments: int = 0,
) -> list[Story]:
    """Search HN stories from the last `years` years (0 = all time), highest points first.

    Title matches with >= SEARCH_MIN_POINTS come first; if they're fewer than n, the rest fill in
    after them (title matches at any points, then body/url matches). `by_date` = newest first
    within the same tiers, no points floor. A URL query is a lookup ("was this on HN?"): all time,
    every submission of it.
    """
    is_url = _is_url(query)
    if is_url:  # Algolia matches scheme and www as words: https:// would miss the http:// posts
        query = _URL_PREFIX_RE.sub("", query)
    filters = []
    if years and not is_url:
        cutoff = datetime.now(UTC) - timedelta(days=365 * years)
        filters.append(f"created_at_i>{int(cutoff.timestamp())}")
    if min_comments:
        filters.append(f"num_comments>={min_comments}")

    def fetch(min_points: int = 0, field: str | None = None) -> list[dict]:
        f = filters + ([f"points>={min_points}"] if min_points else [])
        params = {
            "query": query,
            "tags": "story",
            "hitsPerPage": max(n, SEARCH_POOL),
            # typos let "trust"/"robust" match "rust" — harmless in Algolia's order (typos rank
            # last), but our points/date re-ranking lifts them to the top
            "typoTolerance": "false",
        }
        if f:
            # comma-joined, as Algolia documents it: repeated params silently drop all but the first
            params["numericFilters"] = ",".join(f)
        if field:
            params["restrictSearchableAttributes"] = field
        return _get("search_by_date" if by_date else "search", params)["hits"]

    def rank(hits: list[dict]) -> list[dict]:
        # title matches before body/url matches; then points (or Algolia's newest-first order)
        def key(h: dict) -> tuple[bool, int]:
            title_hit = h.get("_highlightResult", {}).get("title", {}).get("matchLevel") == "full"
            return title_hit, 0 if by_date else h.get("points") or 0

        return sorted(hits, key=key, reverse=True)  # stable: by_date order survives ties

    if is_url:
        hits = sorted(fetch(field="url"), key=lambda h: h.get("points") or 0, reverse=True)
    else:
        hits = rank(fetch(0 if by_date else SEARCH_MIN_POINTS, field="title"))
        if len(hits) < n:  # niche topic: its best stories sit under the floor or only in bodies
            seen = {h["objectID"] for h in hits}
            hits += rank([h for h in fetch() if h["objectID"] not in seen])
    return [_story(h) for h in hits[:n]]


_TAG_RE = re.compile(r"<[^>]+>")
_LINK_RE = re.compile(r'<a href="([^"]+)"[^>]*>.*?</a>', re.DOTALL)


def _strip_html(text: str) -> str:
    """Algolia comment text is HTML — flatten to plain text, keep paragraph breaks.

    Links become their full href (HN truncates anchor text with an ellipsis).
    """
    text = text.replace("<p>", "\n\n").replace("</p>", "")
    text = _LINK_RE.sub(r"\1", text)
    return html.unescape(_TAG_RE.sub("", text)).strip()


def _count_comments(children: list[dict]) -> int:
    """Total text-bearing comments in the full tree — the truth, independent of any display cap."""
    return sum(
        (1 if c.get("text") else 0) + _count_comments(c.get("children") or [])
        for c in children
    )


def _walk(children: list[dict], depth: int, out: list[Comment], limit: int) -> None:
    for child in children:
        if limit and len(out) >= limit:
            return
        if child.get("text"):  # deleted/flagged comments come back text-less
            out.append(
                Comment(
                    author=child.get("author") or "[deleted]",
                    text=_strip_html(child["text"]),
                    depth=depth,
                )
            )
        _walk(child.get("children") or [], depth + 1, out, limit)


def item(
    item_id: str, max_comments: int = DEFAULT_MAX_COMMENTS
) -> tuple[Story, list[Comment]]:
    """One story + its comments, thread order (depth-first, as displayed on HN).

    `max_comments=0` = no cap. Comment text is plain (HTML stripped).
    """
    data = _get(f"items/{item_id}")
    story = Story(
        id=str(data["id"]),
        title=data.get("title") or "",
        url=data.get("url") or None,
        points=data.get("points") or 0,
        num_comments=0,  # set below to the full-tree total (not the capped count)
        created_at=datetime.fromtimestamp(data["created_at_i"], tz=UTC),
    )
    children = data.get("children") or []
    comments: list[Comment] = []
    _walk(children, 0, comments, max_comments)
    # true total, so a capped result is detectable (len(comments) < num_comments)
    story.num_comments = _count_comments(children)
    return story, comments
