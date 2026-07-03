"""Reusable agent toolsets — agentic wrappers over ko's CLI modules.

Declared once as `FunctionToolset`s; agents compose the subset they need
(`Agent(toolsets=[web, papers])`). A toolset is stateless and shareable across
any number of agents. Each toolset carries its own `instructions=` so usage
guidance travels with the tools and only appears when the toolset is attached.

Tools return an error note instead of raising — one flaky source (rate limit,
timeout) shouldn't abort a whole run; the model just routes to another source.
"""

from __future__ import annotations

from pydantic_ai.toolsets import FunctionToolset

from ko import arxiv as arxiv_mod
from ko import exa as exa_mod
from ko import fetch as fetch_mod
from ko import hf as hf_mod
from ko import hn as hn_mod
from ko import papers as papers_mod
from ko import tmdb as tmdb_mod
from ko.arxiv import SearchResult as ArxivResult
from ko.exa import ExaResult
from ko.hf import Paper
from ko.hn import Story
from ko.papers import Work


def _try(label: str, fn, *args, **kwargs):
    """Call a source, returning an error note instead of raising — so one flaky source
    doesn't abort a run. Programming errors (TypeError/AttributeError/NameError/ImportError)
    are re-raised, not disguised as 'unavailable': those are our bugs and should surface
    loudly, not send the agent routing around a code defect forever."""
    try:
        return fn(*args, **kwargs)
    except (TypeError, AttributeError, NameError, ImportError):
        raise
    except Exception as e:
        return f"{label} unavailable ({type(e).__name__}: {e}). Note this and try another source."


# --- web ---
web = FunctionToolset(
    instructions="Web: exa_search to find sources, exa_get/fetch_url to read pages in full."
)


@web.tool_plain
def exa_search(query: str, n: int = 5) -> list[ExaResult]:
    """Semantic web search. Returns title, URL, date, and text excerpt per result."""
    return _try("exa_search", exa_mod.search, query, n=n, with_text=True)


@web.tool_plain
def exa_get(urls: list[str]) -> dict[str, str]:
    """Fetch full markdown content for known URLs (via Exa). Use when a result needs deeper reading."""
    return _try("exa_get", exa_mod.get_contents, urls)


@web.tool_plain
def fetch_url(url: str) -> str:
    """Fetch any URL as clean markdown (articles, PDFs, arxiv, Wayback fallback). Read a page found via search."""
    return _try("fetch_url", lambda: fetch_mod.fetch(url, save=False).text)


# --- papers ---
papers = FunctionToolset(
    instructions="Papers: arxiv (broad academic) + Hugging Face Daily Papers (trending ML) + "
    "OpenAlex (papers_search covers every publisher, not just arxiv; papers_cites walks the "
    "citation graph). Use the *_fetch/*_get tools to read a paper in full."
)


@papers.tool_plain
def arxiv_search(query: str, n: int = 5) -> list[ArxivResult]:
    """Search arxiv for academic papers by topic. Returns id, title, authors, abstract, pdf url."""
    return _try("arxiv_search", arxiv_mod.search, query, max_results=n)


@papers.tool_plain
def arxiv_fetch(arxiv_id: str) -> str:
    """Read a full arxiv paper as markdown by id (e.g. 2401.12345). Use after arxiv_search."""
    return _try("arxiv_fetch", arxiv_mod.fetch, arxiv_id)


@papers.tool_plain
def papers_search(query: str, n: int = 5) -> list[Work]:
    """Cross-publisher paper search via OpenAlex (all journals, not just arxiv). Returns title, year, citations, DOI, journal, open-access url."""
    return _try("papers_search", papers_mod.search, query, n=n)


@papers.tool_plain
def papers_cites(ref: str, n: int = 10) -> list[Work]:
    """Papers citing a given paper (by DOI or arxiv id), most-cited first. Walk the citation graph to find follow-on work."""
    return _try("papers_cites", papers_mod.cites, ref, n=n)


@papers.tool_plain
def hf_search(query: str, n: int = 5) -> list[Paper]:
    """Search Hugging Face Daily Papers (trending ML research). Returns title, upvotes, id, summaries, linked repos/models."""
    return _try("hf_search", hf_mod.search, query, n=n)


@papers.tool_plain
def hf_get(ref: str) -> str:
    """Read a Hugging Face paper page as markdown by id or url. Use after hf_search for depth."""
    return _try("hf_get", hf_mod.get, ref)


# --- news (Hacker News) ---
news = FunctionToolset(
    instructions="Hacker News: practitioner signal; hn_discussion pulls a story's comment thread."
)


@news.tool_plain
def hn_search(query: str, n: int = 10) -> list[Story]:
    """Search Hacker News stories by keyword. Returns title, url, points, comment count, date."""
    return _try("hn_search", hn_mod.search, query, n=n)


@news.tool_plain
def hn_discussion(story_id: str, max_comments: int = 30) -> dict:
    """Fetch a Hacker News story + its comment thread (community discussion) by story id."""

    def _do():
        story, comments = hn_mod.item(story_id, max_comments=max_comments)
        return {
            "title": story.title,
            "url": story.url,
            "points": story.points,
            "comments": [
                {"author": c.author, "depth": c.depth, "text": c.text} for c in comments
            ],
        }

    return _try("hn_discussion", _do)


# --- tmdb (movies / TV) ---
tmdb = FunctionToolset(
    instructions="Movies/TV: tv_lookup gives rating, overview, and where to stream in Australia."
)


@tmdb.tool_plain
def tv_lookup(title: str, kind: str | None = None, year: int | None = None) -> dict:
    """Look up a movie or TV show: rating, overview, and Australian watch providers.

    kind: 'movie' or 'tv' to disambiguate; year to pin a specific release.
    """

    def _do():
        top, others = tmdb_mod.lookup(title, kind=kind, year=year)
        if not top:
            return {"found": False, "query": title}
        return {
            "found": True,
            "title": top.title,
            "year": top.year,
            "kind": top.kind,
            "rating": top.rating,
            "overview": top.overview,
            "url": top.url,
            "providers_au": top.providers,
            "watch_link": top.watch_link,
            "other_matches": [
                {"title": t.title, "year": t.year, "kind": t.kind} for t in others[:5]
            ],
        }

    return _try("tv_lookup", _do)
