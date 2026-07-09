"""X (Twitter) via the official XDK (X API v2). Needs X_BEARER_TOKEN.

Primitives: `search` over recent posts (the API's recent index covers ~the last
7 days); `list_posts` — an X list, addressed by name (one of mine; `ko x ai` = my
AI list), a bare list id, or an `x.com/i/lists/<id>` URL (id/URL read any public
list directly, no owned/followed lookup); `user_posts` — one user's timeline by
@handle / bare handle / profile URL; and `my_lists` — my lists with descriptions +
member counts. Param names mirror the SDK (`max_results`, `start_time`,
`sort_order`) so agent-written code composes with X's docs.

Name→id lookups (my user id, list ids) are cached in ko's state dir
(~/.local/state/ko) — rate limits on the lower tiers are tight, don't spend
calls on lookups.
Home timeline and **your likes** are NOT here: those endpoints need OAuth
user-context (a UserToken), not the app-only bearer — adding them means an OAuth
login flow. Search (incl. full-archive), list + user timelines, and owned lists all
work app-only. To scope a search: `list:<id>` (a list) / `from:<handle>` operators.

💸 Cost (as of 2026-02, X's default is **pay-per-use** — prepaid credits bought in the
X Developer Console, charged per API call):
  - ~$0.005 per post read → `ko x search/list/user --n 20` ≈ **$0.10**, `--n 10` ≈ $0.05.
  - $0.001 per *owned* read (your own lists/followers) → `ko x lists` ≈ **a cent**.
  - Cap 2M post reads/month (above that = Enterprise). Legacy Basic ($200/mo) / Pro
    ($5k/mo) remain for prior subscribers only. So mind big `--n` and tight loops.
  Pricing: https://docs.x.com/x-api/getting-started/pricing

⚠️ Tier note: search + list timelines + user timelines + owned_lists read fine on a
working bearer (verified). `followed_lists` is gated separately and can 403 while
everything else works — `my_lists` skips it rather than failing.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache

import requests
from xdk import Client

from . import config
from .dirs import state_file


DEFAULT_MAX_RESULTS = 10
DEFAULT_DAYS = 7  # recent-search index only goes back ~7 days
DEFAULT_LIST_N = 20
CACHE_FILE = state_file("x_cache.json")  # state, not config — never dotfile-synced


def _default_handle() -> str:
    """My X handle for `ko x lists` / `ko x <name>`: KO_X_HANDLE env → config `[x] handle`
    → 'ko'. The app-only bearer can't self-identify (get_me needs OAuth user-context), so the
    handle is configured once, not auto-detected."""
    return config.setting("KO_X_HANDLE", "x", "handle", "ko")


@dataclass
class XList:
    id: str
    name: str
    description: str = ""
    member_count: int = 0


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
            "X_BEARER_TOKEN is not set. Create an app at https://developer.x.com/, grab "
            "the Bearer Token, export X_BEARER_TOKEN=<token>. Reads are pay-per-use "
            "(prepaid credits, ~$0.005/post read); see https://docs.x.com/x-api/getting-started/pricing"
        )
    return Client(bearer_token=token)


def _field(obj, name, default=None):
    """Read a field whether the XDK handed us a dict or a model object.

    search_recent returns posts/users as plain dicts; other SDK paths and our
    tests use objects. Normalise both so callers don't care which.
    """
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _post(p, users: dict[str, str]) -> Post:
    metrics = _field(p, "public_metrics") or {}
    if hasattr(metrics, "model_dump"):  # pydantic model or plain dict, per SDK path
        metrics = metrics.model_dump()
    created = _field(p, "created_at")
    if isinstance(created, str):
        created = datetime.fromisoformat(created.replace("Z", "+00:00"))
    return Post(
        id=str(_field(p, "id")),
        text=_field(p, "text", ""),
        author=users.get(
            str(_field(p, "author_id", "")), "i"
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
    """Search posts. `days<=7` uses the recent index; `days>7` switches to **full-archive**
    search (goes back years — costs credits per read). `top=True` sorts by relevancy (recent
    only). Scope with operators in `query`: `list:<id>` (a list), `from:<handle>` (one account)."""
    start = datetime.now(timezone.utc) - timedelta(days=days)
    common = dict(
        query=query,
        max_results=max(10, min(n, 100)),  # API floor is 10, cap 100
        start_time=start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        tweet_fields=["created_at", "public_metrics", "author_id"],
        expansions=["author_id"],
        user_fields=["username"],
    )
    if days > 7:  # recent index only covers ~7 days; go full-archive for anything older
        pages = _client().posts.search_all(**common)
    else:
        pages = _client().posts.search_recent(
            sort_order="relevancy" if top else "recency", **common
        )
    return _collect(pages, n)


def _collect(pages, n: int) -> list[Post]:
    """Drain paginated post responses into Posts, resolving authors per page."""
    out: list[Post] = []
    for page in pages:
        includes = _field(page, "includes")
        users = {
            str(_field(u, "id")): _field(u, "username")
            for u in (_field(includes, "users") or [])
        }
        for p in _field(page, "data") or []:
            out.append(_post(p, users))
            if len(out) >= n:
                return out
    return out


def _cache() -> dict:
    if CACHE_FILE.is_file():
        try:
            return json.loads(CACHE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return {}  # corrupt cache is not fatal — it just gets rebuilt
    return {}


def _cache_write(data: dict) -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CACHE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    os.replace(tmp, CACHE_FILE)  # atomic — a crash mid-write can't corrupt the cache


def _user_id(handle: str) -> str:
    cache = _cache()
    if uid := cache.get("users", {}).get(handle):
        return uid
    try:
        resp = _client().users.get_by_username(username=handle)
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else 0
        if status in (400, 404):
            raise RuntimeError(f"no X user @{handle}") from None
        raise RuntimeError(
            f"X API error {status} looking up @{handle}: {e}"
        ) from None
    data = _field(resp, "data")  # dict or object per SDK path; None for unknown/suspended
    if not data:
        raise RuntimeError(f"no X user @{handle}")
    uid = str(_field(data, "id"))
    cache.setdefault("users", {})[handle] = uid
    _cache_write(cache)
    return uid


def my_lists(handle: str | None = None) -> list[XList]:
    """All lists I own or follow. Refreshes the name→id cache as a side effect."""
    handle = handle or _default_handle()
    uid = _user_id(handle)
    found: dict[str, XList] = {}
    methods = (_client().users.get_owned_lists, _client().users.get_followed_lists)
    for i, method in enumerate(methods):
        try:
            for page in method(id=uid, list_fields=["description", "member_count"]):
                # the XDK types owned/followed list items as untyped `data`, so they arrive
                # as plain dicts — go through _field so attribute access doesn't crash
                for lst in _field(page, "data") or []:
                    lid = str(_field(lst, "id"))
                    found[lid] = XList(
                        id=lid,
                        name=_field(lst, "name") or "",
                        description=_field(lst, "description") or "",
                        member_count=int(_field(lst, "member_count") or 0),
                    )
        except Exception:
            # followed_lists (i==1) is tier-gated and 403s on lower tiers — degrade to the
            # owned lists we already have. But an owned_lists failure is a real error: surface it.
            if i == 0:
                raise
            continue
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


_LIST_URL_RE = re.compile(r"/lists/(\d+)")


def _resolve_list(ref: str, handle: str) -> str:
    """A list reference → its id. Accepts a bare numeric id, an `x.com/i/lists/<id>` URL,
    or a list name (case-insensitive, resolved via your owned/followed lists). ID/URL work
    for ANY public list, not just ones you own or follow — no name lookup needed."""
    ref = ref.strip()
    if ref.isdigit():
        return ref
    if m := _LIST_URL_RE.search(ref):
        return m.group(1)
    return _list_id(ref, handle)


def list_posts(
    name: str, n: int = DEFAULT_LIST_N, handle: str | None = None
) -> list[Post]:
    """Recent posts from a list, newest first. `name` may be a list name (yours), a bare
    list id, or an `x.com/i/lists/<id>` URL — id/URL read any public list directly."""
    handle = handle or _default_handle()
    pages = _client().lists.get_posts(
        id=_resolve_list(name, handle),
        max_results=max(10, min(n, 100)),
        tweet_fields=["created_at", "public_metrics", "author_id"],
        expansions=["author_id"],
        user_fields=["username"],
    )
    return _collect(pages, n)


def _parse_handle(ref: str) -> str:
    """@name, bare name, or an x.com/<name> profile URL → the bare handle."""
    ref = ref.strip().lstrip("@")
    m = re.search(r"(?:twitter|x)\.com/([A-Za-z0-9_]+)", ref)
    return m.group(1) if m else ref


def user_posts(handle: str, n: int = DEFAULT_MAX_RESULTS) -> list[Post]:
    """Recent posts from one user's timeline (their tweets + retweets), newest first.
    `handle` may be @name, bare name, or an x.com/<name> profile URL."""
    uid = _user_id(_parse_handle(handle))
    pages = _client().users.get_posts(
        id=uid,
        max_results=max(5, min(n, 100)),  # user-timeline floor is 5
        tweet_fields=["created_at", "public_metrics", "author_id"],
        expansions=["author_id"],
        user_fields=["username"],
    )
    return _collect(pages, n)
