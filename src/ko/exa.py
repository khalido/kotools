"""exa semantic search + content retrieval.

Two primitives — `search` and `get_contents` — that cover the research workflows we
care about (finding labs/faculty/admissions pages; pulling clean text from known URLs).
Use cases compose from flags, not new commands.

Sync-only (simpler for CLI).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from exa_py import Exa


DEFAULT_MAX_RESULTS = 10
DEFAULT_MAX_CHARS = 3000


@dataclass
class ExaResult:
    url: str
    title: str
    score: float
    published_date: str | None
    text: str | None  # populated only when with_text=True
    summary: str | None = None  # a query-relevant blurb, when with_summary=True


def _client() -> Exa:
    key = os.environ.get("EXA_API_KEY")
    if not key:
        raise RuntimeError(
            "EXA_API_KEY is not set. Get a key at https://exa.ai/ and "
            "export EXA_API_KEY=<key> (or add it to your shell profile)."
        )
    return Exa(api_key=key)


def search(
    query: str,
    n: int = DEFAULT_MAX_RESULTS,
    include_domains: list[str] | None = None,
    exclude_domains: list[str] | None = None,
    since_months: int | None = None,
    start_published_date: str | None = None,
    end_published_date: str | None = None,
    with_text: bool = False,
    with_summary: bool = True,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> list[ExaResult]:
    """Semantic web search via Exa. Returns results with an optional query-relevant summary
    (default) and/or the full page text (`with_text`).

    Param names mirror Exa's SDK so agent-written code composes with upstream docs.
    `since_months` is our ergonomic shortcut for "last N months" — sets
    `start_published_date` if one isn't given.
    """
    client = _client()
    kwargs: dict = {"num_results": n}
    if include_domains:
        kwargs["include_domains"] = include_domains
    if exclude_domains:
        kwargs["exclude_domains"] = exclude_domains
    if start_published_date is None and since_months is not None:
        start = datetime.now(timezone.utc) - timedelta(days=30 * since_months)
        start_published_date = start.strftime("%Y-%m-%d")
    if start_published_date:
        kwargs["start_published_date"] = start_published_date
    if end_published_date:
        kwargs["end_published_date"] = end_published_date
    # Summary is a short query-relevant blurb (cheap, useful in a list); text is the full page.
    # exa-py 2.16 folded search_and_contents() into search(contents=...). contents=False
    # keeps the no-content path lean — search() now returns text by default.
    contents: dict = {}
    if with_text:
        contents["text"] = {"max_characters": max_chars}
    if with_summary:
        contents["summary"] = {"query": query}
    response = client.search(query, contents=contents or False, **kwargs)

    return [
        ExaResult(
            url=item.url,
            title=item.title or "",
            score=float(item.score or 0.0),
            published_date=item.published_date,
            text=getattr(item, "text", None),
            summary=getattr(item, "summary", None),
        )
        for item in response.results
    ]


def get_contents(urls: list[str], max_chars: int | None = None) -> dict[str, str]:
    """Fetch clean text for known URLs. Returns {url: text}. No text truncation unless max_chars is set."""
    client = _client()
    text_arg: bool | dict = True if max_chars is None else {"max_characters": max_chars}
    response = client.get_contents(urls=urls, text=text_arg)
    return {item.url: (item.text or "") for item in response.results}
