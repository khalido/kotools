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
from ko.agents import _files as _files_mod
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
def exa_search(
    query: str,
    n: int = 5,
    include_domains: list[str] | None = None,
    exclude_domains: list[str] | None = None,
    since_months: int | None = None,
    start_published_date: str | None = None,
    end_published_date: str | None = None,
) -> list[ExaResult]:
    """Semantic web search. Returns title, URL, date, and a text excerpt per result
    (capped ~3000 chars — use exa_get/fetch_url when a page needs full reading).

    Filter params (all optional):
    - include_domains / exclude_domains: e.g. [".edu"] or ["reddit.com"]
    - since_months: restrict to the last N months (ergonomic shortcut for start_published_date)
    - start_published_date / end_published_date: YYYY-MM-DD date bounds (override since_months if both set)
    Results cover all time by default."""
    return _try(
        "exa_search",
        exa_mod.search,
        query,
        n=n,
        include_domains=include_domains,
        exclude_domains=exclude_domains,
        since_months=since_months,
        start_published_date=start_published_date,
        end_published_date=end_published_date,
        with_text=True,
    )


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
    """Search arxiv for academic papers by topic. Relevance-ranked across all years (not
    newest-first — recent work may rank low). Returns id, title, authors, abstract, pdf url."""
    return _try("arxiv_search", arxiv_mod.search, query, max_results=n)


@papers.tool_plain
def arxiv_fetch(arxiv_id: str) -> str:
    """Read a full arxiv paper as markdown by id (e.g. 2401.12345). Use after arxiv_search."""
    return _try("arxiv_fetch", arxiv_mod.fetch, arxiv_id)


@papers.tool_plain
def papers_search(query: str, n: int = 5) -> list[Work]:
    """Cross-publisher paper search via OpenAlex (all journals, not just arxiv). Relevance-ranked,
    all years. Returns title, year, citations, DOI, journal, open-access url."""
    return _try("papers_search", papers_mod.search, query, n=n)


@papers.tool_plain
def papers_cites(ref: str, n: int = 10) -> list[Work]:
    """Papers citing a given paper (by DOI or arxiv id), most-cited first — walk the citation graph forward for follow-on work.
    If an arxiv id 404s (famous preprints get merged into their published record), papers_search the title and pass the resulting W-id or journal DOI instead."""
    return _try("papers_cites", papers_mod.cites, ref, n=n)


@papers.tool_plain
def papers_refs(ref: str, n: int = 10) -> list[Work]:
    """A paper's own references (by DOI or arxiv id), most-cited first — walk the citation graph backward
    for foundational work. The other half of snowballing, paired with papers_cites. Drawn from the
    paper's first 50 references, so a long bibliography isn't fully covered."""
    return _try("papers_refs", papers_mod.refs, ref, n=n)


@papers.tool_plain
def papers_get(ref: str) -> Work:
    """Metadata for any paper by DOI/arxiv id (title, authors, year, journal, citations, open-access url, tldr, abstract). Works for journal papers that arxiv_fetch/hf_get can't reach; use to verify a citation is real."""
    return _try("papers_get", papers_mod.get, ref)


@papers.tool_plain
def hf_search(query: str, n: int = 5) -> list[Paper]:
    """Search Hugging Face Daily Papers (trending ML research). Only papers featured on hf.co/papers
    are indexed — zero results ≠ the paper doesn't exist (fall back to arxiv_search/papers_search).
    Returns title, upvotes, id, summaries, linked repos/models."""
    return _try("hf_search", hf_mod.search, query, n=n)


@papers.tool_plain
def hf_get(ref: str) -> str:
    """Read a Hugging Face paper page as markdown by id or url. Use after hf_search for depth."""
    return _try("hf_get", hf_mod.get, ref)


# --- news (Hacker News) ---
news = FunctionToolset(
    instructions="Hacker News: practitioner signal — comments carry deployment caveats and "
    "failure reports that papers and blog posts omit, so use hn_discussion to sanity-check "
    "claims found via web/paper search. hn_top for what's hot now, hn_search for a topic's "
    "discussion history."
)


@news.tool_plain
def hn_top(n: int = 10, days: int = 1) -> list[Story]:
    """Top Hacker News stories by points over the last `days` days (days=1 ≈ today's front page). The 'what's hot on HN' tool."""
    return _try("hn_top", hn_mod.top, n=n, days=days)


@news.tool_plain
def hn_front_page(n: int = 10) -> list[Story]:
    """The HN front page *right now* (live). Use for 'on HN at this moment'; hn_top instead for a points-ranked window over past days."""
    return _try("hn_front_page", hn_mod.front_page, n=n)


@news.tool_plain
def hn_search(
    query: str,
    n: int = 10,
    since_months: int = 12,
    by_date: bool = False,
    min_comments: int = 0,
) -> list[Story]:
    """Search Hacker News stories by keyword. Returns title, url, points, comment count, date.

    Covers the last 12 months by default — pass since_months for older topics (0 = all time);
    an empty result under the default window does NOT mean HN never discussed it.
    Relevance order (by_date=True for newest first); min_comments skips stories with no discussion.
    Algolia matches URLs and exact titles through plain search, so searching a URL/DOI/title
    answers 'has this been discussed on HN?'."""
    return _try(
        "hn_search",
        hn_mod.search,
        query,
        n=n,
        since_months=since_months,
        by_date=by_date,
        min_comments=min_comments,
    )


@news.tool_plain
def hn_discussion(story_id: str, max_comments: int = 30) -> dict:
    """Fetch a Hacker News story + its comment thread (community discussion) by story id.
    Returns the first `max_comments` comments in thread order (0 = the whole thread)."""

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


# --- files (read-only, confined to ~/code — the repo-explorer's tools) ---
files = FunctionToolset(
    instructions="Files: read-only access under the code root. Survey cheap first "
    "(list_dir/find_files), grep to locate, read_file only the few files that matter. "
    "All paths are relative to the code root; .git and hidden dirs are off-limits."
)


@files.tool_plain
def list_dir(path: str = ".") -> str:
    """List a directory under the code root: subdirs first ('name/'), then files with
    sizes. Hidden entries skipped; capped at 200. Start here to orient in a repo."""
    return _files_mod.list_dir(path)


@files.tool_plain
def read_file(path: str, offset: int = 0, limit: int = 400) -> str:
    """Read a file's text with line numbers (cite findings as file:line). Returns at
    most `limit` lines from `offset` — a continuation pointer appears when truncated.
    Binary files return a placeholder."""
    return _files_mod.read_file(path, offset=offset, limit=limit)


@files.tool_plain
def grep(pattern: str, path: str = ".", glob: str | None = None) -> str:
    """Regex-search file CONTENTS (ripgrep: smart-case, .gitignore respected) under
    `path`. Returns `file:line: text`, capped at 100 matches — narrow with `glob`
    (e.g. '*.py') or a deeper path when truncated. The fastest way to find where
    something is implemented."""
    return _files_mod.grep(pattern, path=path, glob=glob)


@files.tool_plain
def find_files(glob: str, path: str = ".") -> str:
    """Find files by NAME glob (e.g. '*.md', '**/agent*.py') under `path`; .gitignore
    respected, capped at 200. Use to map a repo's shape before reading anything."""
    return _files_mod.find_files(glob, path=path)


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
