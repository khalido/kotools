"""exa semantic search + content retrieval.

Two primitives — `search` and `get_contents` — that cover the research workflows we
care about (finding labs/faculty/admissions pages; pulling clean text from known URLs).
Use cases compose from flags, not new commands.

Sync-only (simpler for CLI).

Param/value names mirror Exa's SDK (`exa_py`, `SearchType`/`Category` literals) so
agent-written code composes with upstream docs. The valid `SEARCH_TYPES`/`CATEGORIES`
below are copied from the installed SDK — the CLI validates against them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal, get_args

from exa_py import Exa

from ko import config

DEFAULT_MAX_RESULTS = 10
DEFAULT_MAX_CHARS = 3000
DEFAULT_HIGHLIGHT_CHARS = 1000  # bound highlights — unbounded, Exa returns near-full-page text

# Mirror the installed SDK's SearchType / Category literals (exa_py.api). Kept here so the
# CLI can validate a flag before spending a request. 'auto' is Exa's recommended default;
# 'neural' is the legacy embedding search. NOTE: the papers index category is "research
# paper" (there is no "publication" category in the SDK, despite older docs).
SEARCH_TYPES = ("auto", "instant", "fast", "deep-lite", "deep", "deep-reasoning", "neural")

# A Literal so agent tools can type the param (pydantic-ai renders it as an enum in the
# tool schema, so a model can't invent a category); CATEGORIES stays the tuple the CLI
# validates against — one source of truth, two shapes.
Category = Literal[
    "company",
    "research paper",
    "news",
    "pdf",
    "personal site",
    "financial report",
    "people",
]
CATEGORIES = get_args(Category)


@dataclass
class ExaResult:
    url: str
    title: str
    score: float
    published_date: str | None
    text: str | None  # populated only when with_text=True
    summary: str | None = None  # a query-relevant blurb, when with_summary=True
    highlights: list[str] | None = None  # relevant sentences, when with_highlights=True


@dataclass
class UrlStatus:
    """Per-URL retrieval status from the /contents endpoint (returned even on HTTP 200)."""

    url: str
    status: str  # e.g. "success" | "error"
    source: str | None = None  # e.g. "live" | "cache"


@dataclass
class Contents:
    """Result of get_contents: text per URL plus Exa's per-URL status list.

    A URL can appear in `texts` with an empty string when its fetch failed — `statuses`
    is the authoritative signal for what actually succeeded.
    """

    texts: dict[str, str]
    statuses: list[UrlStatus] = field(default_factory=list)


def _client() -> Exa:
    key = config.require_key(
        "EXA_API_KEY",
        "Get a key at https://exa.ai/ and export EXA_API_KEY=<key> "
        "(or add it to your shell profile).",
    )
    return Exa(api_key=key)


def _cap(text: str | None, max_chars: int) -> str | None:
    """Trim `text` to `max_chars`, marking it when content was actually cut.

    Exa truncates server-side via `max_characters`, which gives no "there was more"
    signal — so callers ask for one character of headroom: anything longer than
    `max_chars` proves the page continued, and the marker tells a reader (agent or
    human) to fetch the URL in full rather than assume it saw everything."""
    if text is None or len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}\n\n[... truncated at {max_chars} characters]"


def search(
    query: str,
    n: int = DEFAULT_MAX_RESULTS,
    include_domains: list[str] | None = None,
    exclude_domains: list[str] | None = None,
    since_months: int | None = None,
    start_published_date: str | None = None,
    end_published_date: str | None = None,
    type: str | None = None,
    category: Category | None = None,
    max_age_hours: int | None = None,
    with_text: bool = False,
    with_highlights: bool = False,
    with_summary: bool = True,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> list[ExaResult]:
    """Semantic web search via Exa. Returns results with an optional query-relevant summary
    (default), highlight sentences (`with_highlights`), and/or the full page text (`with_text`).

    Param names mirror Exa's SDK so agent-written code composes with upstream docs.
    - `type`: search algorithm — one of SEARCH_TYPES (default None → Exa's 'auto').
    - `category`: focus on a data category — one of CATEGORIES (e.g. "research paper" for
      the scholarly index, "news", "company"). Caller validates before calling.
    - `max_age_hours`: freshness of cached content. 0 = always livecrawl, -1 = cached only,
      None (omitted) = livecrawl only as a fallback. Applied inside the contents request.
    - `since_months`: ergonomic shortcut for "last N months" — sets `start_published_date`
      if one isn't given.
    - `include_domains`/`exclude_domains`: mutually exclusive (Exa's API rejects both).

    Page text is capped at `max_chars` and marked when cut — see `_cap`.
    """
    if include_domains and exclude_domains:
        raise ValueError(
            "include_domains and exclude_domains are mutually exclusive — Exa's API "
            "rejects both at once; pass one or the other."
        )
    client = _client()
    kwargs: dict = {"num_results": n}
    if include_domains:
        kwargs["include_domains"] = include_domains
    if exclude_domains:
        kwargs["exclude_domains"] = exclude_domains
    if type:
        kwargs["type"] = type
    if category:
        kwargs["category"] = category
    if start_published_date is None and since_months is not None:
        start = datetime.now(UTC) - timedelta(days=30 * since_months)
        start_published_date = start.strftime("%Y-%m-%d")
    if start_published_date:
        kwargs["start_published_date"] = start_published_date
    if end_published_date:
        kwargs["end_published_date"] = end_published_date
    # Summary = short query-relevant blurb (cheap, useful in a list); highlights = the most
    # relevant sentences (Exa's recommended agent default, ~10x fewer tokens than full text);
    # text = the full page. exa-py 2.16 folded search_and_contents() into search(contents=...).
    # contents=False keeps the no-content path lean.
    contents: dict = {}
    if with_text:
        # +1 character of headroom: if Exa hands back more than max_chars, the page had
        # more to give — that's how _cap knows to mark the excerpt as truncated.
        contents["text"] = {"max_characters": max_chars + 1}
    if with_highlights:
        # Bias highlights to the query and bound them — highlights=True returns near-full-page
        # text, defeating the ~10x token saving that makes highlights worthwhile.
        contents["highlights"] = {"query": query, "max_characters": DEFAULT_HIGHLIGHT_CHARS}
    if with_summary:
        contents["summary"] = {"query": query}
    if max_age_hours is not None:
        contents["max_age_hours"] = max_age_hours
    response = client.search(query, contents=contents or False, **kwargs)

    return [
        ExaResult(
            url=item.url,
            title=item.title or "",
            score=float(item.score or 0.0),
            published_date=item.published_date,
            text=_cap(getattr(item, "text", None), max_chars),
            summary=getattr(item, "summary", None),
            highlights=getattr(item, "highlights", None),
        )
        for item in response.results
    ]


def get_contents(urls: list[str], max_chars: int | None = None) -> Contents:
    """Fetch clean text for known URLs. Returns a Contents (`texts` = {url: text}, `statuses`
    = per-URL retrieval status). The /contents endpoint returns HTTP 200 even when a URL
    fails — a failed URL yields an empty string in `texts`, so inspect `statuses` (or the
    empty text) to detect partial failures. No text truncation unless max_chars is set
    (and when set, a clipped page carries a truncation marker — see `_cap`).
    """
    client = _client()
    text_arg: bool | dict = True if max_chars is None else {"max_characters": max_chars + 1}
    response = client.get_contents(urls=urls, text=text_arg)
    texts = {
        item.url: (_cap(item.text, max_chars) if max_chars is not None else item.text) or ""
        for item in response.results
    }
    statuses = [
        UrlStatus(url=s.id, status=s.status, source=getattr(s, "source", None))
        for s in (response.statuses or [])
    ]
    return Contents(texts=texts, statuses=statuses)
