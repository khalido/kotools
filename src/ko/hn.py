"""Hacker News via the Algolia API (hn.algolia.com). No auth, no key.

Three primitives matching how I actually read HN:
- `top` — top stories by points in a day window (my hckrnews.com top-10/20 habit;
  top-by-points ≈ hckrnews's front-page filter, since stories can't score without front-paging)
- `search` — relevance search, restricted to the last year by default
- `item` — one story + its comment tree as readable text

Algolia beats RSS here: points + comment counts + date filters, one JSON API.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx


ALGOLIA = "https://hn.algolia.com/api/v1"
DEFAULT_TOP_N = 10
DEFAULT_SEARCH_N = 10
DEFAULT_SINCE_MONTHS = 12
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


def _get(path: str, params: dict | None = None) -> dict:
    resp = httpx.get(f"{ALGOLIA}/{path}", params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _story(hit: dict) -> Story:
    return Story(
        id=str(hit["objectID"]),
        title=hit.get("title") or "",
        url=hit.get("url") or None,
        points=hit.get("points") or 0,
        num_comments=hit.get("num_comments") or 0,
        created_at=datetime.fromtimestamp(hit["created_at_i"], tz=timezone.utc),
    )


def top(n: int = DEFAULT_TOP_N, days: int = 1) -> list[Story]:
    """Top stories by points over the last `days` days. `top(10)` ≈ hckrnews top-10."""
    cutoff = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
    data = _get(
        "search",  # no query + points sort = day's leaderboard
        {
            "tags": "story",
            "numericFilters": f"created_at_i>{cutoff}",
            "hitsPerPage": n,
        },
    )
    return [_story(h) for h in data["hits"]]


def search(
    query: str,
    n: int = DEFAULT_SEARCH_N,
    since_months: int = DEFAULT_SINCE_MONTHS,
    by_date: bool = False,
    min_comments: int = 0,
) -> list[Story]:
    """Search HN stories. Relevance order by default, last 12 months. `since_months=0` = all time."""
    filters = []
    if since_months:
        cutoff = datetime.now(timezone.utc) - timedelta(days=30 * since_months)
        filters.append(f"created_at_i>{int(cutoff.timestamp())}")
    if min_comments:
        filters.append(f"num_comments>={min_comments}")
    params = {"query": query, "tags": "story", "hitsPerPage": n}
    if filters:
        params["numericFilters"] = ",".join(filters)
    data = _get("search_by_date" if by_date else "search", params)
    return [_story(h) for h in data["hits"]]


_TAG_RE = re.compile(r"<[^>]+>")
_LINK_RE = re.compile(r'<a href="([^"]+)"[^>]*>.*?</a>', re.DOTALL)


def _strip_html(text: str) -> str:
    """Algolia comment text is HTML — flatten to plain text, keep paragraph breaks.

    Links become their full href (HN truncates anchor text with an ellipsis).
    """
    text = text.replace("<p>", "\n\n").replace("</p>", "")
    text = _LINK_RE.sub(r"\1", text)
    return html.unescape(_TAG_RE.sub("", text)).strip()


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
        num_comments=0,  # items endpoint doesn't report it; len(comments) is the truth
        created_at=datetime.fromtimestamp(data["created_at_i"], tz=timezone.utc),
    )
    comments: list[Comment] = []
    _walk(data.get("children") or [], 0, comments, max_comments)
    story.num_comments = len(comments)
    return story, comments
