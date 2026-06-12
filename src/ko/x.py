"""X (Twitter) via the official XDK (X API v2). Needs X_BEARER_TOKEN.

One primitive for now: `search` over recent posts (the API's recent index
covers ~the last 7 days). Param names mirror the SDK (`max_results`,
`start_time`, `sort_order`) so agent-written code composes with X's docs.

⚠️ Tier note: the free X API tier is ~write-only; reads (search) generally
need Basic or above. A 403 here usually means the token's tier, not the code.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache

from xdk import Client


DEFAULT_MAX_RESULTS = 10
DEFAULT_DAYS = 7  # recent-search index only goes back ~7 days


@dataclass
class Post:
    id: str
    text: str
    author: str  # username, no @
    created_at: datetime
    likes: int
    reposts: int

    @property
    def url(self) -> str:
        return f"https://x.com/{self.author}/status/{self.id}"


@lru_cache(maxsize=1)
def _client() -> Client:
    token = os.environ.get("X_BEARER_TOKEN")
    if not token:
        raise RuntimeError(
            "X_BEARER_TOKEN is not set. Create an app at "
            "https://developer.x.com/, grab the Bearer Token, and "
            "export X_BEARER_TOKEN=<token>. Note: reading posts needs a "
            "paid tier (free is ~write-only)."
        )
    return Client(bearer_token=token)


def _post(p, users: dict[str, str]) -> Post:
    metrics = getattr(p, "public_metrics", None) or {}
    if hasattr(
        metrics, "model_dump"
    ):  # pydantic model or plain dict, depending on SDK path
        metrics = metrics.model_dump()
    created = getattr(p, "created_at", None)
    if isinstance(created, str):
        created = datetime.fromisoformat(created.replace("Z", "+00:00"))
    return Post(
        id=str(p.id),
        text=p.text,
        author=users.get(
            str(getattr(p, "author_id", "")), "i"
        ),  # 'i' → x.com/i/status/<id> still resolves
        created_at=created or datetime.now(timezone.utc),
        likes=int(metrics.get("like_count") or 0),
        reposts=int(metrics.get("retweet_count") or 0),
    )


def search(
    query: str,
    n: int = DEFAULT_MAX_RESULTS,
    days: int = DEFAULT_DAYS,
    top: bool = False,
) -> list[Post]:
    """Search recent posts (last `days` days, API max ~7). `top=True` sorts by relevancy."""
    start = datetime.now(timezone.utc) - timedelta(days=min(days, 7))
    pages = _client().posts.search_recent(
        query=query,
        max_results=max(10, min(n, 100)),  # API floor is 10, cap 100
        start_time=start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        sort_order="relevancy" if top else "recency",
        tweet_fields=["created_at", "public_metrics", "author_id"],
        expansions=["author_id"],
        user_fields=["username"],
    )
    out: list[Post] = []
    for page in pages:
        users = (
            {
                str(u.id): u.username
                for u in (getattr(page.includes, "users", None) or [])
            }
            if getattr(page, "includes", None)
            else {}
        )
        for p in page.data or []:
            out.append(_post(p, users))
            if len(out) >= n:
                return out
    return out
