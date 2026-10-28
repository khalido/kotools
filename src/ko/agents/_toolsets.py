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
from ko.config import MissingKeyError
from ko.exa import Category as ExaCategory
from ko.exa import ExaResult
from ko.hf import Paper
from ko.hn import Story
from ko.papers import Work


def _try(label: str, fn, *args, **kwargs):
    """Call a source, returning an error note instead of raising — so one flaky source
    doesn't abort a run. Programming errors (TypeError/AttributeError/NameError/ImportError)
    are re-raised, not disguised as 'unavailable': those are our bugs and should surface
    loudly, not send the agent routing around a code defect forever.

    MissingKeyError re-raises for the same reason from the other direction: an unset
    key is *config*, which no amount of model cleverness fixes. Swallowed, the agent
    quietly routes around the tool for the whole run and Ko never learns the key is
    missing — so it aborts the run and lands as one clear line on stderr instead."""
    try:
        return fn(*args, **kwargs)
    except TypeError, AttributeError, NameError, ImportError, MissingKeyError:
        raise
    except Exception as e:
        return f"{label} unavailable ({type(e).__name__}: {e}). Note this and try another source."


# --- web ---
web = FunctionToolset(
    instructions="Web: exa_search finds sources and returns only bounded, query-relevant "
    "highlight sentences per hit — enough to judge relevance, never the whole page. Search "
    "wide, then read narrow: pass the 1-3 URLs that actually matter to exa_get (several at "
    "once, clean markdown) or fetch_url (any URL, incl. PDFs/arxiv/Wayback). Quote from a "
    "page you read in full, not from a highlight fragment."
)


@web.tool_plain
def exa_search(
    query: str,
    n: int = 5,
    category: ExaCategory | None = None,
    include_domains: list[str] | None = None,
    exclude_domains: list[str] | None = None,
    since_months: int | None = None,
    start_published_date: str | None = None,
    end_published_date: str | None = None,
) -> list[ExaResult]:
    """Semantic web search. Returns title, URL, date, and `highlights` — the handful of
    sentences most relevant to the query, bounded to ~1000 chars per result (roughly 10x
    cheaper than page text). Highlights are for TRIAGE: to actually read a page, pass its
    URL to exa_get or fetch_url. `text` and `summary` come back null here by design.

    Params (all optional):
    - category: focus Exa's index. Exactly one of 'research paper' (the scholarly index —
      the .edu-domains trick, done properly), 'news', 'company', 'pdf', 'personal site',
      'financial report', 'people'. Note it is 'research paper', NOT 'publication'.
    - include_domains / exclude_domains: e.g. ['.edu'] or ['reddit.com'] — mutually
      exclusive, passing both is an error.
    - since_months: restrict to the last N months (ergonomic shortcut for start_published_date)
    - start_published_date / end_published_date: YYYY-MM-DD date bounds (override since_months if both set)
    Results cover all time and all categories by default."""
    return _try(
        "exa_search",
        exa_mod.search,
        query,
        n=n,
        category=category,
        include_domains=include_domains,
        exclude_domains=exclude_domains,
        since_months=since_months,
        start_published_date=start_published_date,
        end_published_date=end_published_date,
        with_text=False,
        with_highlights=True,
        with_summary=False,
    )


@web.tool_plain
def exa_get(urls: list[str]) -> dict[str, str]:
    """Fetch full markdown content for known URLs (via Exa) — the deep-read step after
    exa_search's highlights. Takes several URLs in one call. Untruncated; a URL that
    failed comes back with empty text."""
    result = _try("exa_get", exa_mod.get_contents, urls)
    return result.texts if isinstance(result, exa_mod.Contents) else result


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
    """The HN front page *right now* (live), in HN's rank order. Use for 'on HN at this moment'; hn_top instead for a points-ranked window over past days."""
    return _try("hn_front_page", hn_mod.front_page, n=n)


@news.tool_plain
def hn_search(
    query: str,
    n: int = 10,
    years: int = 1,
    by_date: bool = False,
    min_comments: int = 0,
) -> list[Story]:
    """Search Hacker News stories by keyword. Returns title, url, points, comment count, date.

    Covers the last year by default — pass years for older topics (0 = all time);
    an empty result under the default window does NOT mean HN never discussed it.
    Title matches with 20+ points first, by points; if fewer than n, matches anywhere (body, url)
    at any points fill in after them. by_date=True for newest first, any field, no floor.
    min_comments skips stories with no discussion; it narrows on top of the years window, so
    start without it and add it only to cut a long list.
    A query starting with http(s):// is a lookup: every submission of that URL (any scheme/www), all time —
    answers 'has this been discussed on HN?'."""
    return _try(
        "hn_search",
        hn_mod.search,
        query,
        n=n,
        years=years,
        by_date=by_date,
        min_comments=min_comments,
    )


@news.tool_plain
def hn_discussion(story_id: str, max_comments: int = 30) -> dict:
    """Fetch a Hacker News story + its comment thread (community discussion) by story id.
    Returns the first `max_comments` comments in thread order (0 = the whole thread); ~25 is
    plenty for the gist, since each comment costs tokens. An empty `comments` list means nobody
    commented: don't retry. Link a story as https://news.ycombinator.com/item?id=<id>."""

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
    "(list_dir/glob), grep to locate, read_file the few files that matter. Listings "
    "show ≈token sizes — a small file (under ~2k tok) is cheaper to read whole in one "
    "call than to grep. All paths are relative to the code root; .git and hidden dirs "
    "are off-limits."
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
def grep(
    pattern: str,
    path: str = ".",
    glob: str | None = None,
    limit: int = 100,
    context: int = 0,
    files_only: bool = False,
) -> str:
    """Regex-search file CONTENTS (ripgrep: smart-case, .gitignore respected) under
    `path`. Returns `file:line: text`, at most `limit` matches (default 100, max 200).
    `context=N` shows N lines around each match (max 10) — often saves a read_file.
    `files_only=True` returns just the matching file paths (survey mode). Narrow with
    `glob` (e.g. '*.py') or a deeper path when truncated."""
    return _files_mod.grep(
        pattern,
        path=path,
        glob=glob,
        limit=limit,
        context=context,
        files_only=files_only,
    )


@files.tool_plain
def glob(pattern: str, path: str = ".") -> str:
    """Find files by NAME glob pattern (e.g. '*.md', '**/agent*.py') under `path`;
    .gitignore respected, hidden dirs (.git/.venv, installed packages) excluded,
    capped at 200. Use to map a repo's shape before reading anything."""
    return _files_mod.glob(pattern, path=path)


# --- AI models (Vercel AI Gateway catalog + HN) ---
ai_models = FunctionToolset(
    instructions="For a question about an AI model (a release, its price, speed, or reception), "
    "call model_info first: it resolves the newest version and gives facts, hosts and HN threads; "
    "then dig with hn_discussion, papers/hf, and exa_search (include_domains=['reddit.com'] for "
    "hands-on reports)."
)


@ai_models.tool_plain
def model_info(name: str, years: int = 1) -> dict:
    """Look up an AI model by name, fuzzy ('sonnet', 'glm flash', 'kimi'): the NEWEST matching
    model on Vercel AI Gateway with release date, $/M-token prices (the cheapest host's listed
    price), context, every host's last-hour speed (first-token latency, tokens/s), other matching
    ids, and up to 3 HN threads from the last `years` (0 = all time) by points, each with its
    top-ranked comment and reply (full text). Deterministic, no LLM."""
    from dataclasses import asdict

    from ko import models as models_mod

    def _do():
        info = asdict(models_mod.info(name, years=years))
        info.pop("description", None)  # marketing copy; the facts are in the fields
        return info

    return _try("model_info", _do)


# --- packages (npm / PyPI via deps.dev + GitHub) ---
packages = FunctionToolset(
    instructions="For a question about an npm or PyPI package (is it maintained, safe, worth "
    "using, what changed), call pkg_info first. Few GitHub stars, no releases in a year, or "
    "advisories on the version in use are the warning signs to report."
)


@packages.tool_plain
def pkg_info(name: str) -> list[dict]:
    """Look up an npm or PyPI package by exact name ('npm:x' / 'pypi:x' to pick; '@scope/x' is
    npm; else both are tried and every match returned): the registry's latest version and date,
    the version uv/npm would pick under KO's 3-day release-age floor, the version locked in the
    current repo, advisories on latest and on the locked version, licence, GitHub stars, last
    push, releases in the last year, weekly downloads, and the first lines of the latest release
    notes with their link. Deterministic, no LLM."""
    from dataclasses import asdict

    from ko import pkg as pkg_mod

    return _try("pkg_info", lambda: [asdict(p) for p in pkg_mod.info(name)])


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
