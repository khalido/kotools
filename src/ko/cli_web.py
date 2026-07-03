"""Research/content cluster CLI commands: arxiv, exa, hf, hn, papers, x, plus root-level tv/fetch/doc."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import typer

from . import arxiv as arxiv_mod
from . import doc as doc_mod
from . import exa as exa_mod
from . import fetch as fetch_mod
from . import hf as hf_mod
from . import hn as hn_mod
from . import papers as papers_mod
from . import tmdb as tmdb_mod
from . import x as x_mod
from ._cli_shared import _die, _emit_json, _fmt_day, _no_results, app

# --- sub-apps ---

arxiv_app = typer.Typer(help="arxiv search + paper fetch.", no_args_is_help=True)
app.add_typer(arxiv_app, name="arxiv")

exa_app = typer.Typer(
    help="exa semantic web search (EXA_API_KEY required).", no_args_is_help=True
)
app.add_typer(exa_app, name="exa")

hf_app = typer.Typer(
    help="Hugging Face paper pages: daily feed, semantic search, metadata (no auth).",
    no_args_is_help=True,
)
app.add_typer(hf_app, name="hf")

hn_app = typer.Typer(
    help="Hacker News top stories + search via Algolia (no auth).",
    no_args_is_help=True,
)
app.add_typer(hn_app, name="hn")

papers_app = typer.Typer(
    help="Cross-publisher paper search + citation graph via OpenAlex (no auth; "
    "S2_API_KEY adds tldr/similar). Takes DOIs, arxiv ids, or OpenAlex W-ids.",
    no_args_is_help=True,
)
app.add_typer(papers_app, name="papers")

x_app = typer.Typer(
    help="X (Twitter) posts via the official XDK (X_BEARER_TOKEN required, paid tier for reads).",
    no_args_is_help=True,
)
app.add_typer(x_app, name="x")


# --- arxiv commands ---


@arxiv_app.command("search")
def arxiv_search(
    query: str = typer.Argument(..., help="arxiv search query"),
    since: int = typer.Option(
        arxiv_mod.DEFAULT_SINCE_MONTHS,
        "--since",
        "-s",
        help="only include papers published within the last N months",
    ),
    n: int = typer.Option(
        arxiv_mod.DEFAULT_MAX_RESULTS, "--n", help="max results to return"
    ),
    long: bool = typer.Option(False, "--long", "-l", help="include abstract summary"),
    as_json: bool = typer.Option(False, "--json", help="emit JSON instead of text"),
) -> None:
    """Search arxiv, newest first. Defaults to the last 18 months."""
    try:
        results = arxiv_mod.search(query, since_months=since, max_results=n)
    except Exception as e:
        _die(str(e), as_json=as_json)
    if not results:
        _no_results(f"No results for '{query}' in the last {since} months.", as_json)
    if as_json:
        _emit_json(results)
        return
    for r in results:
        date = r.published.strftime("%Y-%m-%d")
        authors = ", ".join(r.authors[:3]) + (" et al." if len(r.authors) > 3 else "")
        typer.echo(f"{r.short_id}  {date}  {r.title}")
        typer.echo(f"  {authors}")
        if long:
            typer.echo(f"  {r.summary[:400]}{'…' if len(r.summary) > 400 else ''}")
        typer.echo("")


@arxiv_app.command("fetch")
def arxiv_fetch(
    arxiv_id: str = typer.Argument(
        ..., help="arxiv id, e.g. 2604.02460 or 2604.02460v1"
    ),
    out: Path = typer.Option(
        None,
        "--out",
        "-o",
        help="write markdown to this path; prints to stdout if omitted",
    ),
) -> None:
    """Fetch a paper as markdown (via arxiv2md)."""
    try:
        content = arxiv_mod.fetch(arxiv_id)
    except Exception as e:
        _die(str(e))
    if out is None:
        typer.echo(content)
    else:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(content)
        typer.echo(f"Wrote {len(content):,} chars to {out}", err=True)


# --- exa helpers ---


def _parse_domains(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [d.strip() for d in value.split(",") if d.strip()]


# --- exa commands ---


@exa_app.command("search")
def exa_search(
    query: str = typer.Argument(..., help="semantic search query"),
    n: int = typer.Option(exa_mod.DEFAULT_MAX_RESULTS, "--n", help="max results"),
    domains: str = typer.Option(
        None,
        "--domains",
        "-d",
        help="comma-separated include domains (e.g. '.edu' or 'unsw.edu.au,sydney.edu.au')",
    ),
    exclude: str = typer.Option(
        None, "--exclude", help="comma-separated exclude domains"
    ),
    since: int = typer.Option(
        None,
        "--since",
        "-s",
        help="ergonomic shortcut: results published within the last N months",
    ),
    start_date: str = typer.Option(
        None, "--start-date", help="publish date floor (YYYY-MM-DD)"
    ),
    end_date: str = typer.Option(
        None, "--end-date", help="publish date ceiling (YYYY-MM-DD)"
    ),
    no_text: bool = typer.Option(
        False, "--no-text", help="skip content entirely (fastest; title + url + date only)"
    ),
    long: bool = typer.Option(False, "--long", "-l", help="fetch the full page text excerpt, not just the summary"),
    as_json: bool = typer.Option(False, "--json", help="emit JSON instead of text"),
) -> None:
    """Semantic search. Each hit shows a query-relevant summary by default. For labs/faculty
    use --domains .edu; for uni pages use --domains <uni>."""
    try:
        results = exa_mod.search(
            query,
            n=n,
            include_domains=_parse_domains(domains),
            exclude_domains=_parse_domains(exclude),
            since_months=since,
            start_published_date=start_date,
            end_published_date=end_date,
            with_text=long,
            with_summary=not no_text,
        )
    except Exception as e:
        _die(str(e), as_json=as_json)
    if not results:
        _no_results(f"No results for '{query}'.", as_json)
    if as_json:
        _emit_json(results)
        return
    for r in results:
        day = _fmt_day(r.published_date)
        title = " ".join((r.title or r.url).split())  # collapse stray newlines in the title
        typer.echo(f"{title}{'  ·  ' + day if day else ''}")
        typer.echo(f"  {r.url}")
        blurb = (r.text if long else None) or r.summary
        if blurb:
            excerpt = " ".join(blurb.split())
            if excerpt.startswith("Summary:"):  # Exa sometimes prefixes its summary
                excerpt = excerpt[len("Summary:") :].lstrip(" -")
            cap = 500 if long else 240
            typer.echo(f"  {excerpt[:cap]}{'…' if len(excerpt) > cap else ''}")
        typer.echo("")


@exa_app.command("get")
def exa_get(
    urls: list[str] = typer.Argument(..., help="one or more URLs to fetch"),
    out: Path = typer.Option(
        None, "--out", "-o", help="write combined markdown to this path"
    ),
) -> None:
    """Fetch clean markdown for known URLs. Prints concatenated markdown to stdout by default."""
    try:
        contents = exa_mod.get_contents(urls)
    except Exception as e:
        _die(str(e))
    missing = [u for u in urls if u not in contents]
    if missing:  # don't let failed URLs vanish silently — an agent should know it's partial
        typer.echo(
            f"[fetched {len(contents)} of {len(urls)}; no content for: {', '.join(missing)}]",
            err=True,
        )
    sections = [f"# {url}\n\n{text}" for url, text in contents.items()]
    combined = "\n\n---\n\n".join(sections)
    if out is None:
        typer.echo(combined)
    else:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(combined)
        typer.echo(f"Wrote {len(combined):,} chars to {out}", err=True)


# --- hf helpers ---


def _echo_papers(papers: list[hf_mod.Paper], as_json: bool, long: bool) -> None:
    if as_json:
        _emit_json(papers)
        return
    for p in papers:
        date = p.published_at.strftime("%Y-%m-%d")
        typer.echo(f"{p.id}  {p.upvotes:>4}▲  {date}  {p.title}")
        if long and (p.ai_summary or p.summary):
            excerpt = (p.ai_summary or p.summary)[:400].replace("\n", " ")
            typer.echo(f"  {excerpt}")
        typer.echo("")


# --- hf commands ---


@hf_app.command("top")
def hf_top(
    n: int = typer.Option(hf_mod.DEFAULT_TOP_N, "--n", help="how many papers"),
    date: str = typer.Option(
        None, "--date", "-d", help="YYYY-MM-DD daily feed (default: latest)"
    ),
    long: bool = typer.Option(False, "--long", "-l", help="include summary excerpt"),
    as_json: bool = typer.Option(False, "--json", help="emit JSON instead of text"),
) -> None:
    """Daily Papers by upvotes (trending). First column is the arxiv id — feeds `ko hf info|get` and `ko arxiv fetch`."""
    try:
        papers = hf_mod.top(n=n, date=date)
    except Exception as e:
        _die(str(e), as_json=as_json)
    _echo_papers(papers, as_json, long)


@hf_app.command("search")
def hf_search(
    query: str = typer.Argument(..., help="search query (semantic + full-text)"),
    n: int = typer.Option(hf_mod.DEFAULT_SEARCH_N, "--n", help="max results"),
    long: bool = typer.Option(False, "--long", "-l", help="include summary excerpt"),
    as_json: bool = typer.Option(False, "--json", help="emit JSON instead of text"),
) -> None:
    """Search AI papers on hf.co/papers (covers title, authors, content)."""
    try:
        results = hf_mod.search(query, n=n)
    except Exception as e:
        _die(str(e), as_json=as_json)
    if not results:
        _no_results(f"No results for '{query}'.", as_json)
    _echo_papers(results, as_json, long)


@hf_app.command("info")
def hf_info(
    ref: str = typer.Argument(..., help="arxiv id, arxiv URL, or hf.co/papers URL"),
    as_json: bool = typer.Option(False, "--json", help="emit JSON instead of text"),
) -> None:
    """One paper's metadata: upvotes, github + stars, AI summary, linked models/datasets/spaces."""
    try:
        p = hf_mod.info(ref)
    except Exception as e:
        _die(str(e), as_json=as_json)
    if as_json:
        typer.echo(json.dumps(asdict(p), default=str))
        return
    typer.echo(f"{p.title}  ({p.upvotes}▲, {p.published_at.strftime('%Y-%m-%d')})")
    typer.echo(p.hf_url)
    if p.github_repo:
        typer.echo(f"code: {p.github_repo}  ({p.github_stars:,}★)")
    if p.project_page:
        typer.echo(f"project: {p.project_page}")
    for label, ids in [
        ("models", p.linked_models),
        ("datasets", p.linked_datasets),
        ("spaces", p.linked_spaces),
    ]:
        if ids:
            typer.echo(f"{label}: {', '.join(ids)}")
    if p.ai_summary or p.summary:
        typer.echo(f"\n{p.ai_summary or p.summary}")


@hf_app.command("get")
def hf_get(
    ref: str = typer.Argument(..., help="arxiv id, arxiv URL, or hf.co/papers URL"),
    out: Path = typer.Option(
        None,
        "--out",
        "-o",
        help="write markdown to this path; prints to stdout if omitted",
    ),
) -> None:
    """Fetch a paper as markdown. Only papers indexed on hf.co/papers; else use `ko arxiv fetch`."""
    try:
        content = hf_mod.get(ref)
    except Exception as e:
        _die(str(e))
    if out is None:
        typer.echo(content)
    else:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(content)
        typer.echo(f"Wrote {len(content):,} chars to {out}", err=True)


# --- papers helpers ---


def _echo_works(works: list[papers_mod.Work], as_json: bool) -> None:
    if as_json:
        _emit_json(works)
        return
    for w in works:
        typer.echo(
            f"{w.year}\t{w.cited_by_count}\t{w.oa_status or '—'}\t"
            f"{w.title}\t{w.doi or '—'}\t{w.journal or '—'}"
        )


def _echo_work_card(w: papers_mod.Work) -> None:
    authors = ", ".join(w.authors[:6]) + (" et al." if len(w.authors) > 6 else "")
    typer.echo(f"{w.title}  ({w.year})")
    if authors:
        typer.echo(authors)
    bits = [b for b in (w.journal, f"cited by {w.cited_by_count}", w.oa_status) if b]
    typer.echo(" · ".join(bits))
    if w.doi:
        typer.echo(f"doi: {w.doi}  {w.doi_url}")
    if w.oa_url:
        typer.echo(f"oa: {w.oa_url}")
    if w.tldr:
        typer.echo(f"\ntldr: {w.tldr}")
    if w.abstract:
        typer.echo(f"\n{w.abstract}")


# --- papers commands ---


@papers_app.command("search")
def papers_search(
    query: str = typer.Argument(..., help="search query (title, abstract, full-text)"),
    n: int = typer.Option(papers_mod.DEFAULT_N, "--n", help="max results"),
    as_json: bool = typer.Option(False, "--json", help="emit JSON instead of TSV"),
) -> None:
    """Search articles across all publishers, relevance order.
    TSV: year, cites, oa_status, title, doi, journal."""
    try:
        results = papers_mod.search(query, n=n)
    except Exception as e:
        _die(str(e), as_json=as_json)
    if not results:
        _no_results(f"No results for '{query}'.", as_json)
    _echo_works(results, as_json)


@papers_app.command("get")
def papers_get(
    ref: str = typer.Argument(..., help="DOI, doi.org URL, arxiv id, or OpenAlex W-id"),
    info: bool = typer.Option(
        False, "--info", "-i", help="metadata card only, skip the full-text fetch"
    ),
    out: Path = typer.Option(
        None, "--out", "-o", help="write full text to this path; stdout if omitted"
    ),
    as_json: bool = typer.Option(
        False, "--json", help="emit metadata as JSON (implies --info)"
    ),
) -> None:
    """One paper: full text if an open-access copy exists, else a metadata card
    (title, authors, journal, tldr, abstract). Paywalled ≠ error — the card is the answer."""
    try:
        w = papers_mod.get(ref)
    except Exception as e:
        _die(str(e), as_json=as_json)
    if as_json:
        typer.echo(json.dumps(asdict(w), default=str))
        return
    if not info:
        # try each OA candidate in turn — direct PDFs first, landing page last;
        # a direct PDF often works where the landing page bot-blocks (MDPI etc.)
        for url in w.full_text_urls:
            try:
                r = fetch_mod.fetch(url, save=out is None)
            except Exception:
                continue  # dead/blocked → next candidate, else fall through to the card
            note = f"oa copy: {url}" + (f"; {r.note}" if r.note else "")
            typer.echo(f"[{note}]", err=True)
            if out is None:
                typer.echo(r.text)
            else:
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(r.text)
                typer.echo(f"Wrote {len(r.text):,} chars to {out}", err=True)
            return
        if w.full_text_urls:  # had OA links but none yielded text — card is the fallback
            typer.echo("[no OA copy fetched — metadata card instead]", err=True)
    _echo_work_card(w)


@papers_app.command("cites")
def papers_cites(
    ref: str = typer.Argument(..., help="DOI, doi.org URL, arxiv id, or OpenAlex W-id"),
    n: int = typer.Option(papers_mod.DEFAULT_N, "--n", help="max results"),
    as_json: bool = typer.Option(False, "--json", help="emit JSON instead of TSV"),
) -> None:
    """Papers citing this one, most-cited first — 'who built on it?'"""
    try:
        results = papers_mod.cites(ref, n=n)
    except Exception as e:
        _die(str(e), as_json=as_json)
    if not results:
        _no_results(f"No citing works for {ref}.", as_json)
    _echo_works(results, as_json)


@papers_app.command("refs")
def papers_refs(
    ref: str = typer.Argument(..., help="DOI, doi.org URL, arxiv id, or OpenAlex W-id"),
    n: int = typer.Option(papers_mod.DEFAULT_N, "--n", help="max results (batch cap 50)"),
    as_json: bool = typer.Option(False, "--json", help="emit JSON instead of TSV"),
) -> None:
    """This paper's references, most-cited first — 'what does it build on?'"""
    try:
        results = papers_mod.refs(ref, n=n)
    except Exception as e:
        _die(str(e), as_json=as_json)
    if not results:
        _no_results(f"No resolvable references for {ref}.", as_json)
    _echo_works(results, as_json)


@papers_app.command("similar")
def papers_similar(
    ref: str = typer.Argument(..., help="DOI, doi.org URL, arxiv id, or OpenAlex W-id"),
    n: int = typer.Option(papers_mod.DEFAULT_SIMILAR_N, "--n", help="max results"),
    as_json: bool = typer.Option(False, "--json", help="emit JSON instead of TSV"),
) -> None:
    """Related papers via Semantic Scholar recommendations (needs S2_API_KEY, free)."""
    try:
        results = papers_mod.similar(ref, n=n)
    except Exception as e:
        _die(str(e), as_json=as_json)
    if not results:
        _no_results(f"No recommendations for {ref}.", as_json)
    _echo_works(results, as_json)


# --- hn helpers ---


def _echo_stories(stories: list[hn_mod.Story], as_json: bool) -> None:
    if as_json:
        _emit_json(stories)
        return
    for s in stories:
        date = s.created_at.strftime("%Y-%m-%d")
        typer.echo(f"{s.id}  {s.points:>4}pts  {s.num_comments:>4}c  {date}  {s.title}")
        typer.echo(f"  {s.url or s.hn_url}")
        typer.echo("")


# --- hn commands ---


@hn_app.command("top")
def hn_top(
    n: int = typer.Option(
        hn_mod.DEFAULT_TOP_N, "--n", help="how many stories (10 or 20, hckrnews-style)"
    ),
    days: int = typer.Option(
        1, "--days", "-d", help="window: top stories of the last N days"
    ),
    as_json: bool = typer.Option(False, "--json", help="emit JSON instead of text"),
) -> None:
    """Top stories by points, last 24h by default. First column is the id for `ko hn item`."""
    try:
        stories = hn_mod.top(n=n, days=days)
    except Exception as e:
        _die(str(e), as_json=as_json)
    _echo_stories(stories, as_json)


@hn_app.command("search")
def hn_search(
    query: str = typer.Argument(..., help="search query"),
    n: int = typer.Option(hn_mod.DEFAULT_SEARCH_N, "--n", help="max results"),
    since: int = typer.Option(
        hn_mod.DEFAULT_SINCE_MONTHS,
        "--since",
        "-s",
        help="only stories from the last N months (0 = all time)",
    ),
    new: bool = typer.Option(
        False, "--new", help="sort newest first instead of by relevance"
    ),
    min_comments: int = typer.Option(
        0, "--min-comments", "-c", help="only stories with at least N comments"
    ),
    as_json: bool = typer.Option(False, "--json", help="emit JSON instead of text"),
) -> None:
    """Search HN stories. Relevance order, last 12 months by default."""
    try:
        results = hn_mod.search(
            query, n=n, since_months=since, by_date=new, min_comments=min_comments
        )
    except Exception as e:
        _die(str(e), as_json=as_json)
    if not results:
        _no_results(f"No results for '{query}'.", as_json)
    _echo_stories(results, as_json)


@hn_app.command("item")
def hn_item(
    item_id: str = typer.Argument(..., help="story id (first column of top/search)"),
    n: int = typer.Option(
        hn_mod.DEFAULT_MAX_COMMENTS, "--n", help="max comments to show (0 = all)"
    ),
    as_json: bool = typer.Option(False, "--json", help="emit JSON instead of text"),
) -> None:
    """One story + its comment tree (as readable indented text, or `{story, comments}` JSON)."""
    try:
        story, comments = hn_mod.item(item_id, max_comments=n)
    except Exception as e:
        _die(str(e), as_json=as_json)
    if as_json:
        typer.echo(
            json.dumps(
                {"story": asdict(story), "comments": [asdict(c) for c in comments]},
                default=str,
            )
        )
        return
    date = story.created_at.strftime("%Y-%m-%d")
    typer.echo(f"{story.title}  ({story.points}pts, {date})")
    if story.url:
        typer.echo(story.url)
    typer.echo(story.hn_url)
    if len(comments) < story.num_comments:
        typer.echo(
            f"[showing {len(comments)} of {story.num_comments} comments — raise --n for more]",
            err=True,
        )
    for c in comments:
        pad = "  " * (c.depth + 1)
        typer.echo(f"\n{pad}[{c.author}]")
        for line in c.text.splitlines():
            typer.echo(f"{pad}{line}" if line else "")


# --- doc command (root-level) ---


@app.command("doc")
def doc(
    file: Path = typer.Argument(
        ...,
        exists=True,
        dir_okay=False,
        help="PDF, Office doc, or image (Office needs LibreOffice; images use OCR)",
    ),
    pages: str = typer.Option(
        None, "--pages", "-p", help="page range, e.g. '1-5' or '3'"
    ),
    out: Path = typer.Option(
        None, "--out", "-o", help="write text to this path; prints to stdout if omitted"
    ),
    no_ocr: bool = typer.Option(
        False, "--no-ocr", help="skip OCR of image-based text (faster)"
    ),
) -> None:
    """Document → plain text via liteparse. Local + fast, no models. Shortcut: `ko <file>`."""
    try:
        text = doc_mod.parse(file, pages=pages, ocr=not no_ocr)
    except Exception as e:
        _die(str(e))
    if out is None:
        typer.echo(text)
    else:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text)
        typer.echo(f"Wrote {len(text):,} chars to {out}", err=True)


# --- x helpers ---


def _echo_posts(posts: list[x_mod.Post], as_json: bool) -> None:
    if as_json:
        _emit_json(posts)
        return
    for p in posts:
        date = p.created_at.strftime("%Y-%m-%d")
        text = " ".join(p.text.split())  # collapse newlines for scanability
        if len(text) > 200:
            text = text[:200] + "…"
        typer.echo(f"@{p.author}  {date}  {p.likes}♥ {p.reposts}rt")
        typer.echo(f"  {text}")
        typer.echo(f"  {p.url}")
        typer.echo("")


# --- x commands ---


@x_app.command("search")
def x_search(
    query: str = typer.Argument(
        ..., help="X search query (supports operators like from:user, -is:retweet)"
    ),
    n: int = typer.Option(x_mod.DEFAULT_MAX_RESULTS, "--n", help="max posts"),
    days: int = typer.Option(
        x_mod.DEFAULT_DAYS, "--days", "-d", help="how far back (API max ~7 days)"
    ),
    top: bool = typer.Option(
        False, "--top", help="sort by relevancy instead of newest first"
    ),
    as_json: bool = typer.Option(False, "--json", help="emit JSON instead of text"),
) -> None:
    """Search recent X posts. Newest first by default."""
    try:
        posts = x_mod.search(query, n=n, days=days, top=top)
    except Exception as e:
        _die(str(e), as_json=as_json)
    if not posts:
        _no_results(f"No posts for '{query}' in the last {days} days.", as_json)
    _echo_posts(posts, as_json)


@x_app.command("list")
def x_list(
    name: str = typer.Argument(
        ..., help="list name, case-insensitive (e.g. 'ai'). Shortcut: `ko x ai`"
    ),
    n: int = typer.Option(x_mod.DEFAULT_LIST_N, "--n", help="max posts"),
    as_json: bool = typer.Option(False, "--json", help="emit JSON instead of text"),
) -> None:
    """Recent posts from one of your X lists, newest first."""
    try:
        posts = x_mod.list_posts(name, n=n)
    except Exception as e:
        _die(str(e), as_json=as_json)
    if not posts:
        _no_results(f"No recent posts in list '{name}'.", as_json)
    _echo_posts(posts, as_json)


@x_app.command("lists")
def x_lists(
    as_json: bool = typer.Option(False, "--json", help="emit JSON instead of TSV"),
) -> None:
    """Your X lists (owned + followed). TSV: id, name."""
    try:
        lists = x_mod.my_lists()
    except Exception as e:
        _die(str(e), as_json=as_json)
    if as_json:
        _emit_json(lists)
        return
    for lst in lists:
        typer.echo(f"{lst.id}\t{lst.name}")


# --- fetch command (root-level) ---


@app.command("fetch")
def fetch_cmd(
    url: str = typer.Argument(..., help="URL — article, PDF link, or arxiv link"),
    archive: bool = typer.Option(
        False, "--archive", "-a", help="fetch from the Wayback Machine instead of live"
    ),
    archive_is: bool = typer.Option(
        False,
        "--archive-is",
        help="fetch the latest archive.today snapshot (best for hard paywalls)",
    ),
    date: str = typer.Option(
        None, "--date", help="preferred Wayback snapshot date, YYYYMMDD"
    ),
    no_save: bool = typer.Option(
        False, "--no-save", help="don't keep downloaded PDFs in ~/Downloads"
    ),
    out: Path = typer.Option(
        None, "--out", "-o", help="write to this path; prints to stdout if omitted"
    ),
) -> None:
    """URL → clean markdown. Articles via trafilatura, PDFs download + parse,
    arxiv links via arxiv2md, paywalls via archive.today, dead links via Wayback.
    Shortcut: `ko <url>`."""
    try:
        r = fetch_mod.fetch(
            url, archive=archive, archive_is=archive_is, date=date, save=not no_save
        )
    except Exception as e:
        _die(str(e))
    if r.note:
        typer.echo(f"[{r.note}]", err=True)
    if out is None:
        typer.echo(r.text)
    else:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(r.text)
        typer.echo(f"Wrote {len(r.text):,} chars to {out}", err=True)


# --- tv command (root-level) ---


@app.command("tv")
def tv(
    query: str = typer.Argument(..., help="movie or TV title to look up"),
    tv: bool = typer.Option(False, "--tv", help="TV shows only"),
    movie: bool = typer.Option(False, "--movie", help="movies only"),
    year: int = typer.Option(None, "--year", "-y", help="release year filter"),
    country: str = typer.Option(
        tmdb_mod.DEFAULT_COUNTRY, "--country", "-c", help="watch-provider region"
    ),
    n: int = typer.Option(3, "--n", help="how many runner-up matches to list"),
    as_json: bool = typer.Option(False, "--json", help="emit JSON instead of text"),
) -> None:
    """Movie/TV quick check: rating, overview, and where to watch (AU by default)."""
    if tv and movie:
        typer.echo("--tv and --movie are mutually exclusive", err=True)
        raise typer.Exit(2)
    kind = "tv" if tv else "movie" if movie else None
    try:
        top, rest = tmdb_mod.lookup(query, kind=kind, year=year, country=country)
    except Exception as e:
        _die(str(e), as_json=as_json)
    if top is None:
        if as_json:  # keep the {top, matches} shape valid for a downstream parser
            typer.echo(json.dumps({"top": None, "matches": []}))
        typer.echo(f"No matches for '{query}'.", err=True)
        raise typer.Exit(0)
    if as_json:
        typer.echo(
            json.dumps({"top": asdict(top), "matches": [asdict(t) for t in rest[:n]]})
        )
        return
    year_s = top.year or "—"
    typer.echo(f"{top.title} ({year_s})  ★{top.rating:.1f}  {top.kind}")
    if top.overview:
        typer.echo(f"  {top.overview[:300]}{'…' if len(top.overview) > 300 else ''}")
    if top.providers:
        offers = " · ".join(
            f"{tmdb_mod.OFFER_LABELS[o]}: {', '.join(names)}"
            for o, names in top.providers.items()
        )
        typer.echo(f"  {offers}")
    else:
        typer.echo(f"  not streaming in {country}")
    typer.echo(f"  {top.watch_link or top.url}")
    if rest[:n]:
        others = " · ".join(
            f"{t.title} ({t.year or '—'}, {t.kind}) ★{t.rating:.1f}" for t in rest[:n]
        )
        typer.echo(f"\nOther matches: {others}")
