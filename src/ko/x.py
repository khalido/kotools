"""X (Twitter) via the official XDK (X API v2). Needs X_BEARER_TOKEN.

Two primitives: `search` over recent posts (the API's recent index covers
~the last 7 days) and `list_posts` — recent posts from one of my X lists by
name (the actual daily habit; `ko x ai` = my AI list). Param names mirror
the SDK (`max_results`, `start_time`, `sort_order`) so agent-written code
composes with X's docs.

Name→id lookups (my user id, list ids) are cached at ~/.config/ko/x_cache.json
— rate limits on the lower tiers are tight, don't spend calls on lookups.
Home timeline is NOT here: that endpoint needs OAuth user-context, not the
app-only bearer token. Lists + search work app-only.

⚠️ Tier note: the free X API tier is ~write-only; reads generally need
Basic or above. A 403 here usually means the token's tier, not the code.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

from xdk import Client


DEFAULT_MAX_RESULTS = 10
DEFAULT_DAYS = 7  # recent-search index only goes back ~7 days
DEFAULT_LIST_N = 20
DEFAULT_HANDLE = os.environ.get("KO_X_HANDLE", "ko")
CACHE_FILE = Path.home() / ".config" / "ko" / "x_cache.json"


@dataclass
class XList:
    id: str
    name: str


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
    return _collect(pages, n)


def _collect(pages, n: int) -> list[Post]:
    """Drain paginated post responses into Posts, resolving authors per page."""
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


def _cache() -> dict:
    if CACHE_FILE.is_file():
        return json.loads(CACHE_FILE.read_text())
    return {}


def _cache_write(data: dict) -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(data, indent=1))


def _user_id(handle: str) -> str:
    cache = _cache()
    if uid := cache.get("users", {}).get(handle):
        return uid
    resp = _client().users.get_by_username(username=handle)
    uid = str(resp.data.id)
    cache.setdefault("users", {})[handle] = uid
    _cache_write(cache)
    return uid


def my_lists(handle: str = DEFAULT_HANDLE) -> list[XList]:
    """All lists I own or follow. Refreshes the name→id cache as a side effect."""
    uid = _user_id(handle)
    found: dict[str, XList] = {}
    for method in (_client().users.get_owned_lists, _client().users.get_followed_lists):
        for page in method(id=uid):
            for lst in page.data or []:
                found[str(lst.id)] = XList(id=str(lst.id), name=lst.name)
    cache = _cache()
    cache.setdefault("lists", {})[handle] = {
        lst.name.lower(): lst.id for lst in found.values()
    }
    _cache_write(cache)
    return sorted(found.values(), key=lambda lst: lst.name.lower())


def _list_id(name: str, handle: str) -> str:
    cached = _cache().get("lists", {}).get(handle, {})
    if lid := cached.get(name.lower()):
        return lid
    lists = my_lists(handle)  # cache miss → refresh from the API
    for lst in lists:
        if lst.name.lower() == name.lower():
            return lst.id
    names = ", ".join(lst.name for lst in lists) or "(none found)"
    raise RuntimeError(f"no X list named {name!r} for @{handle}. Your lists: {names}")


def list_posts(
    name: str, n: int = DEFAULT_LIST_N, handle: str = DEFAULT_HANDLE
) -> list[Post]:
    """Recent posts from one of my lists, by name (case-insensitive), newest first."""
    pages = _client().lists.get_posts(
        id=_list_id(name, handle),
        max_results=max(10, min(n, 100)),
        tweet_fields=["created_at", "public_metrics", "author_id"],
        expansions=["author_id"],
        user_fields=["username"],
    )
    return _collect(pages, n)
