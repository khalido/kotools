"""Research/content cluster CLI commands: arxiv, exa, hf, hn, papers, x, plus root-level tv/fetch/doc."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import typer

from ._cli_shared import _die, _emit_json, _fmt_day, _no_results, _tsv_cell, app, lazy

arxiv_mod = lazy("arxiv")
doc_mod = lazy("doc")
exa_mod = lazy("exa")
fetch_mod = lazy("fetch")
hf_mod = lazy("hf")
hn_mod = lazy("hn")
papers_mod = lazy("papers")
tmdb_mod = lazy("tmdb")
x_mod = lazy("x")
yt_mod = lazy("yt")

# --- sub-apps ---

arxiv_app = typer.Typer(help="arxiv search + paper fetch.", no_args_is_help=True)
app.add_typer(arxiv_app, name="arxiv")

exa_app = typer.Typer(
    help="exa semantic web search + URL → markdown (EXA_API_KEY required). "
    "`search` --highlights/--long/--type/--category/--max-age-hours; `ko prompt exa` for the guide.",
    no_args_is_help=True,
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
    recent: bool = typer.Option(
        False,
        "--recent",
        "-r",
        help="sort newest-first instead of by relevance (for browsing an area's latest; "
        "pair with a category query like 'cat:cs.LG'). Applies an 18-month window.",
    ),
    since: int = typer.Option(
        None,
        "--since",
        "-s",
        help="restrict to the last N months (hard filter in relevance mode)",
    ),
    n: int = typer.Option(
        arxiv_mod.DEFAULT_MAX_RESULTS, "--n", "-n", help="max results to return"
    ),
    long: bool = typer.Option(False, "--long", "-l", help="include abstract summary"),
    as_json: bool = typer.Option(False, "--json", help="emit JSON instead of text"),
) -> None:
    """Search arxiv, relevance-ranked (use --recent for newest-first).
    Note: arxiv's API is a bit slow (~2-5s). For cross-publisher relevance, `ko papers search`."""
    try:
        results = arxiv_mod.search(query, max_results=n, recent=recent, since_months=since)
    except Exception as e:
        _die(str(e), as_json=as_json)
    if not results:
        _no_results(f"No results for '{query}'.", as_json)
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
# Top-level alias under the name the whole ecosystem uses for this capability (Claude Code's
# WebSearch, the Anthropic API's web_search, every MCP server's web_search). `exa` names the
# vendor — right for the group, wrong as the only spelling of "search the web". Visible here
# because it lands in a different listing than `ko exa search`, not a duplicate of it.
@app.command("websearch")
def exa_search(
    query: str = typer.Argument(..., help="semantic search query"),
    n: int = typer.Option(exa_mod.DEFAULT_MAX_RESULTS, "--n", "-n", help="max results"),
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
    search_type: str = typer.Option(
        None,
        "--type",
        "-t",
        help="search algorithm: auto (default), instant, fast, deep-lite, deep, "
        "deep-reasoning, neural. 'auto' picks the best approach.",
    ),
    category: str = typer.Option(
        None,
        "--category",
        help="focus on a data category: 'research paper' (the scholarly index — upgrades the "
        "--domains .edu workflow), news, company, pdf, 'personal site', 'financial report', people.",
    ),
    max_age_hours: int = typer.Option(
        None,
        "--max-age-hours",
        help="freshness of cached content: 0 = always live-crawl, -1 = cached only, "
        "omitted = live-crawl only as a fallback.",
    ),
    highlights: bool = typer.Option(
        False,
        "--highlights",
        help="show the most-relevant sentences per result instead of a summary "
        "(Exa's agent default; ~10x fewer tokens than full text)",
    ),
    no_text: bool = typer.Option(
        False, "--no-text", help="skip content entirely (fastest; title + url + date only)"
    ),
    long: bool = typer.Option(False, "--long", "-l", help="fetch the full page text excerpt, not just the summary"),
    as_json: bool = typer.Option(False, "--json", help="emit JSON instead of text"),
) -> None:
    """Semantic web search; each hit shows a query-relevant summary by default (--highlights
    for key sentences, --long for full text). Alias: `ko exa search`."""
    if search_type is not None and search_type not in exa_mod.SEARCH_TYPES:
        _die(
            f"invalid --type {search_type!r}; choose one of: {', '.join(exa_mod.SEARCH_TYPES)}",
            as_json=as_json,
            code="usage",
        )
    if category is not None and category not in exa_mod.CATEGORIES:
        _die(
            f"invalid --category {category!r}; choose one of: {', '.join(exa_mod.CATEGORIES)}",
            as_json=as_json,
            code="usage",
        )
    if domains and exclude:  # Exa's API rejects both; catch it here so it's a usage error (2)
        _die(
            "--domains and --exclude are mutually exclusive — Exa's API rejects both at once",
            as_json=as_json,
            code="usage",
        )
    try:
        results = exa_mod.search(
            query,
            n=n,
            include_domains=_parse_domains(domains),
            exclude_domains=_parse_domains(exclude),
            since_months=since,
            start_published_date=start_date,
            end_published_date=end_date,
            type=search_type,
            category=category,
            max_age_hours=max_age_hours,
            with_text=long,
            with_highlights=highlights,
            with_summary=not no_text and not highlights,
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
        if highlights and r.highlights:  # render each highlight sentence on its own line
            for h in r.highlights:
                typer.echo(f"  • {' '.join(h.split())}")
            typer.echo("")
            continue
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
        result = exa_mod.get_contents(urls)
    except Exception as e:
        _die(str(e))
    # A URL can come back present-but-empty (Exa returns HTTP 200 with a per-URL status even
    # on failure); treat both "absent" and "empty text" as failed so agents see a partial run.
    got = {u: t for u, t in result.texts.items() if t}
    failed = [u for u in urls if u not in got]
    if failed:  # don't let failed URLs vanish silently — an agent should know it's partial
        # Exa's statuses carry the reason (e.g. error/source); surface them when present.
        reasons = {s.url: s.status for s in result.statuses if s.status and s.status != "success"}
        detail = ", ".join(f"{u} ({reasons[u]})" if u in reasons else u for u in failed)
        note = f"fetched {len(got)} of {len(urls)}; no content for: {detail}"
        typer.echo(f"[{note}]", err=True)
    if not got:  # all URLs failed → runtime error (no output to give)
        _die(f"no content returned for any of the {len(urls)} URL(s)")
    sections = [f"# {url}\n\n{text}" for url, text in got.items()]
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
    n: int = typer.Option(hf_mod.DEFAULT_TOP_N, "--n", "-n", help="how many papers"),
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
    n: int = typer.Option(hf_mod.DEFAULT_SEARCH_N, "--n", "-n", help="max results"),
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
    n: int = typer.Option(papers_mod.DEFAULT_N, "--n", "-n", help="max results"),
    as_json: bool = typer.Option(False, "--json", help="emit JSON instead of TSV"),
) -> None:
    """Search articles across all publishers, relevance order.
    TSV: year, cites, oa_status, title, doi, journal."""
    if not query.strip():
        _die("query cannot be empty", as_json=as_json, code="usage")
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
            except Exception:  # noqa: S112 — dead/blocked → try the next candidate
                continue
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
    n: int = typer.Option(papers_mod.DEFAULT_N, "--n", "-n", help="max results"),
    as_json: bool = typer.Option(False, "--json", help="emit JSON instead of TSV"),
) -> None:
    """Papers citing this one, most-cited first — 'who built on it?'
    Tip: a famous arXiv paper may be merged into its published record and lose its standalone
    DOI — if an arxiv id 404s, use the journal DOI, or `ko papers search "<title>"` → W-id."""
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
    n: int = typer.Option(papers_mod.DEFAULT_N, "--n", "-n", help="max results (batch cap 50)"),
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
    n: int = typer.Option(papers_mod.DEFAULT_SIMILAR_N, "--n", "-n", help="max results"),
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
        # asdict() misses @property fields (url is a field; hn_url is a property) —
        # inject hn_url explicitly so JSON consumers get the HN discussion link too.
        typer.echo(json.dumps([{**asdict(s), "hn_url": s.hn_url} for s in stories], default=str))
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
        hn_mod.DEFAULT_TOP_N, "--n", "-n", help="how many stories (10 or 20, hckrnews-style)"
    ),
    days: int = typer.Option(
        1, "--days", "-d", help="window: top stories of the last N days"
    ),
    now: bool = typer.Option(
        False, "--now", help="the live front page (right now) instead of top-by-points; ignores --days"
    ),
    as_json: bool = typer.Option(False, "--json", help="emit JSON instead of text"),
) -> None:
    """Top stories by points, last 24h by default (`--now` = the live front page). First column is the id for `ko hn item`."""
    try:
        stories = hn_mod.front_page(n=n) if now else hn_mod.top(n=n, days=days)
    except Exception as e:
        _die(str(e), as_json=as_json)
    _echo_stories(stories, as_json)


@hn_app.command("search")
def hn_search(
    query: str = typer.Argument(..., help="search query"),
    n: int = typer.Option(hn_mod.DEFAULT_SEARCH_N, "--n", "-n", help="max results"),
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
        window = f" in the last {since} months (use --since 0 for all time)" if since else ""
        _no_results(f"No results for '{query}'{window}.", as_json)
    _echo_stories(results, as_json)


@hn_app.command("item")
def hn_item(
    item_id: str = typer.Argument(..., help="story id (first column of top/search)"),
    n: int = typer.Option(
        hn_mod.DEFAULT_MAX_COMMENTS, "--n", "-n", help="max comments to show (0 = all)"
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
        help="PDF, Office doc, or image (.docx reads directly; other Office files need LibreOffice; images use OCR)",
    ),
    pages: str = typer.Option(
        None, "--pages", "-p", help="page range, e.g. '1-5' or '3'"
    ),
    out: Path = typer.Option(
        None, "--out", "-o", help="write text to this path; prints to stdout if omitted"
    ),
    ocr: bool = typer.Option(
        None, "--ocr/--no-ocr", help="OCR every page / none (default: only PDF pages with no text layer)"
    ),
    md: bool = typer.Option(False, "--md", help="markdown per page (tables as pipe tables)"),
    as_json: bool = typer.Option(False, "--json", help="emit [{page, label, text}]"),
    deep: bool = typer.Option(
        False, "--deep", help="docling: reads multi-column layouts in order; figures saved beside -o (slow, ~3 s a page)"
    ),
    describe: bool = typer.Option(False, "--describe", help="with --deep: a Gemini description under each figure (paid)"),
) -> None:
    """Document → text via liteparse, each page under a `<!-- page N -->` line so claims can cite
    it. A .docx reads directly as markdown (no pages); `--pages`/`--json` lay it out via
    LibreOffice instead. Local + fast, no models. Shortcut: `ko <file>`."""
    if describe and not deep:
        _die("--describe needs --deep", code="usage")
    if deep:
        if as_json or md:
            _die("--deep already writes markdown; drop --json/--md", code="usage")
        assets = out.with_name(out.stem + "_assets") if out else None
        typer.echo(f"docling: converting {file.name} (first run downloads ~1 GB of models)…", err=True)
        try:
            parsed, figures = doc_mod.deep(file, pages=pages, assets_dir=assets, describe=describe)
        except Exception as e:
            _die(str(e))
        _write_doc(doc_mod.marked(parsed), out)
        if figures and assets:
            kept = len(list(assets.glob("*.png")))
            typer.echo(f"{figures} figure(s), {kept} distinct, saved to {assets}", err=True)
        elif figures:
            typer.echo(f"{figures} figure(s) left as placeholders — pass -o to save them", err=True)
        return
    if file.suffix.lower() == ".docx" and pages is None and not as_json:
        try:
            head, body = doc_mod.docx_markdown(file)
        except Exception as e:
            _die(str(e))
        _write_doc(f"{head}\n\n{body}", out)
        return
    try:
        parsed = doc_mod.parse_pages(file, pages=pages, ocr=ocr, markdown=md)
    except Exception as e:
        _die(str(e), as_json=as_json)
    if as_json:
        import json

        text = json.dumps([{"page": p.num, "label": p.label, "text": p.text} for p in parsed.pages])
    else:
        text = doc_mod.marked(parsed)
    _write_doc(text, out)


def _write_doc(text: str, out: Path | None) -> None:
    if out is None:
        typer.echo(text)
    else:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text)
        typer.echo(f"Wrote {len(text):,} chars to {out}", err=True)


@app.command("pdf")
# Spelled-out alias for when `pdf` alone reads ambiguous. Hidden from the listing so one
# function prints one line — the name still works, and the docstring names it.
@app.command("md-to-pdf", hidden=True)
def pdf(
    file: Path = typer.Argument(
        ..., exists=True, dir_okay=False, help="Input .md file (YAML front matter honoured)"
    ),
    out: Path = typer.Option(
        None,
        "--out",
        "-o",
        help="output .pdf (default: a pdf/ dir beside the input if one exists, else next to it)",
    ),
    style: str = typer.Option(
        None,
        "--style",
        "-s",
        help="note (default: typography + page footer) | report (+ letterhead, Contents, "
        "numbered sections) | bare (typography only) | any ~/.config/ko/pdf/<name>.css",
    ),
    letterhead: bool = typer.Option(
        None, "--letterhead/--no-letterhead", help="force the page-1 letterhead on/off"
    ),
    brand: str = typer.Option(
        None,
        "--brand",
        help="which identity to send as: a name in ~/.config/ko/brands/ (or a path to a "
        "brand.yml); else $KO_BRAND or `[pdf] brand`",
    ),
) -> None:
    """Markdown → print-ready PDF (A4, code highlighting, page footer). Prints the path it
    wrote to stdout, so `-a "$(ko pdf doc.md)"` composes. Alias: `ko md-to-pdf`."""
    from . import pdf as pdf_mod

    try:
        written, used_brand = pdf_mod.render(
            file, out, style=style, letterhead=letterhead, brand_file=brand
        )
    except ValueError as e:  # unknown --style / --brand: a usage error, not a runtime one
        _die(str(e), code="usage")
    except FileNotFoundError as e:
        _die(f"{e.filename}: no such file")
    except (OSError, RuntimeError) as e:
        _die(str(e))
    # The ops script's recurring bug: forgetting --brand shipped a PDF footed
    # "yourbrand.dev". Only worth saying when the document actually shows the brand.
    # `--brand ""` is an explicit "render unbranded" — warning about a choice you just made is noise.
    if brand != "" and pdf_mod.is_placeholder(used_brand) and pdf_mod.resolve_style(style).shows_brand:
        typer.echo(
            f"note: no brand.yml found, so this reads '{pdf_mod.BRAND['name']}' — "
            f"drop one in {pdf_mod.brand_dir()}/ or set `[pdf] brand` in config.toml",
            err=True,
        )
    typer.echo(written)


# --- x helpers ---


def _echo_posts(posts: list[x_mod.Post], as_json: bool) -> None:
    if as_json:
        # asdict() misses @property fields — inject url explicitly so JSON consumers
        # get the post link (text path already shows it via the property directly).
        typer.echo(json.dumps([{**asdict(p), "url": p.url} for p in posts], default=str))
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
        ..., help="X search query (supports operators like from:user, list:<id>, -is:retweet)"
    ),
    scope_list: str = typer.Option(
        None, "--list", "-l", help="restrict to a list (name/id/url) — adds list:<id> to the query"
    ),
    n: int = typer.Option(
        x_mod.DEFAULT_MAX_RESULTS,
        "--n", "-n",
        help="max posts; API minimum 10 per read (X bills per post read) — smaller --n just truncates the display",
    ),
    days: int = typer.Option(
        x_mod.DEFAULT_DAYS,
        "--days",
        "-d",
        help="how far back; >7 switches to full-archive search (years back, costs credits/read)",
    ),
    top: bool = typer.Option(
        False, "--top", help="sort by relevancy instead of newest first (≤7-day only)"
    ),
    as_json: bool = typer.Option(False, "--json", help="emit JSON instead of text"),
) -> None:
    """Search X posts. ≤7 days uses the recent index; >7 uses full-archive.
    `--list ai` scopes to a list; `ko x search "claude code" --list ai --days 90`."""
    q = query
    if scope_list:
        try:
            lid = x_mod._resolve_list(scope_list, x_mod._default_handle())
        except Exception as e:
            _die(str(e), as_json=as_json)
        q = f"{query} list:{lid}"
    if days > 7:
        note = f"full-archive search, {days}d back — costs credits per read"
        if top:
            note += "; --top ignored (relevancy is recent-index only)"
        typer.echo(f"[{note}]", err=True)
    try:
        posts = x_mod.search(q, n=n, days=days, top=top)
    except Exception as e:
        _die(str(e), as_json=as_json)
    if not posts:
        _no_results(f"No posts for '{query}' in the last {days} days.", as_json)
    _echo_posts(posts, as_json)


@x_app.command("list")
def x_list(
    name: str = typer.Argument(
        ...,
        help="list name (yours, e.g. 'ai'; shortcut `ko x ai`), a bare list id, "
        "or an x.com/i/lists/<id> URL — id/URL read any public list",
    ),
    n: int = typer.Option(
        x_mod.DEFAULT_LIST_N,
        "--n", "-n",
        help="max posts; API minimum 10 per read (X bills per post read) — smaller --n just truncates the display",
    ),
    as_json: bool = typer.Option(False, "--json", help="emit JSON instead of text"),
) -> None:
    """Recent posts from an X list, newest first. By your list name, a list id, or a list URL."""
    try:
        posts = x_mod.list_posts(name, n=n)
    except Exception as e:
        _die(str(e), as_json=as_json)
    if not posts:
        _no_results(f"No recent posts in list '{name}'.", as_json)
    _echo_posts(posts, as_json)


@x_app.command("user")
def x_user(
    handle: str = typer.Argument(
        ..., help="@handle, bare handle, or an x.com/<handle> profile URL"
    ),
    n: int = typer.Option(
        x_mod.DEFAULT_MAX_RESULTS,
        "--n", "-n",
        help="max posts; API minimum 5 per read (X bills per post read) — smaller --n just truncates the display",
    ),
    as_json: bool = typer.Option(False, "--json", help="emit JSON instead of text"),
) -> None:
    """Recent posts from one user's timeline, newest first (their tweets + retweets).
    Goes back further than search's ~7-day window. `ko x user bcherny`."""
    try:
        posts = x_mod.user_posts(handle, n=n)
    except Exception as e:
        _die(str(e), as_json=as_json)
    if not posts:
        _no_results(f"No recent posts for {handle!r}.", as_json)
    _echo_posts(posts, as_json)


@x_app.command("lists")
def x_lists(
    as_json: bool = typer.Option(False, "--json", help="emit JSON instead of TSV"),
) -> None:
    """Your X lists, biggest first. TSV: id, name, members, description.
    (Shows lists you own; lists you only *follow* need a higher API tier.)"""
    try:
        lists = x_mod.my_lists()
    except Exception as e:
        _die(str(e), as_json=as_json)
    if not lists:
        _no_results("no lists found (or your API tier can't read them)", as_json)
    lists = sorted(lists, key=lambda lst: lst.member_count, reverse=True)
    if as_json:
        _emit_json(lists)
        return
    for lst in lists:
        typer.echo(f"{lst.id}\t{lst.name}\t{lst.member_count}\t{_tsv_cell(lst.description)}")


# --- fetch command (root-level) ---


def _tokens(r: fetch_mod.Fetched) -> int:
    return len(r.text) // 4


def _fetch_banner(r: fetch_mod.Fetched) -> str:
    """One consistent stderr note per fetched doc: source, ~token estimate, and any detail
    (saved path, wayback timestamp). ~4 chars/token — the heuristic used across ko — so a
    caller can decide read-vs-pipe-into-llm on every fetch, not just some."""
    bits = [r.source, f"~{_tokens(r):,} tokens"]
    if r.note:
        bits.append(r.note)
    return "[" + " · ".join(bits) + "]"


@app.command("fetch")
# The WebFetch/web_fetch spelling, for the same reason as `ko websearch`. Hidden from the
# listing so one function prints one line — the name still works, and the docstring names it.
@app.command("webfetch", hidden=True)
def fetch_cmd(
    urls: list[str] = typer.Argument(
        ...,
        help="one or more URLs (article, PDF, or arxiv link). With several, each document is "
        "printed under a `# <url>` header, separated by a `---` rule; a per-URL failure is "
        "noted on stderr and skipped.",
    ),
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
    as_json: bool = typer.Option(
        False, "--json", help="array of {url, source, tokens, note, text} — or {url, error} per failed URL"
    ),
    fresh: bool = typer.Option(False, "--fresh", help="skip the local cache (default: reuse a fetch < 1 h old)"),
) -> None:
    """URL → clean markdown, routed by host (arxiv, YouTube, PDF, reddit, wikipedia), with
    archive.today for paywalls and Wayback for dead links. Takes several URLs.
    Shortcut: `ko <url>`. Alias: `ko webfetch`."""
    # Sequential fetch, mirroring `ko exa get`: per-URL failures go to stderr and are skipped;
    # exit 0 if any URL succeeded, 1 if all failed.
    docs: list[tuple[str, fetch_mod.Fetched]] = []
    rows: list[dict] = []  # --json: one per URL, failures included, in the order asked
    for u in urls:
        try:
            r = fetch_mod.fetch(
                u, archive=archive, archive_is=archive_is, date=date, save=not no_save, fresh=fresh
            )
        except Exception as e:
            typer.echo(f"[failed: {u} — {e}]", err=True)
            rows.append({"url": u, "error": str(e)})
            continue
        docs.append((u, r))
        rows.append({"url": u, "source": r.source, "tokens": _tokens(r), "note": r.note, "text": r.text})
        typer.echo(_fetch_banner(r), err=True)  # one token banner per document
    if not docs:  # nothing succeeded → runtime error (no output to give)
        _die(f"no content returned for any of the {len(urls)} URL(s)", as_json=as_json)
    if as_json:
        import json

        typer.echo(json.dumps(rows))
        return
    # Single URL stays byte-identical on stdout (it's piped everywhere): raw text, no header.
    if len(urls) == 1:
        combined = docs[0][1].text
    else:
        combined = "\n\n---\n\n".join(f"# {u}\n\n{r.text}" for u, r in docs)
    if out is None:
        typer.echo(combined)
    else:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(combined)
        typer.echo(f"Wrote {len(combined):,} chars to {out}", err=True)


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
    n: int = typer.Option(3, "--n", "-n", help="how many runner-up matches to list"),
    as_json: bool = typer.Option(False, "--json", help="emit JSON instead of text"),
) -> None:
    """Movie/TV quick check: rating, overview, and where to watch (AU by default)."""
    if tv and movie:
        # via _die so --json gets the {error, code} object AGENTS.md promises
        _die("--tv and --movie are mutually exclusive", as_json=as_json, code="usage")
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


# --- yt command (root-level) ---


def _yt_models() -> list[str]:
    """Lazy model completion — cli_web doesn't import llm at module load."""
    from . import llm as llm_mod

    return llm_mod.available_models()


@app.command("yt")
def yt_cmd(
    url_or_id: str = typer.Argument(..., help="YouTube URL or 11-char video id"),
    summarize: bool = typer.Option(
        False, "--summarize", "-s", help="pipe the transcript through the LLM (key points, who should watch)"
    ),
    model: str = typer.Option(
        None,
        "--model",
        "-m",
        help="model for --summarize (ignored without -s)",
        autocompletion=_yt_models,
    ),
    as_json: bool = typer.Option(
        False, "--json", help="emit [{start, text, duration}] snippets instead of plain text"
    ),
) -> None:
    """YouTube → transcript (plain text, pipeable). No auth, no download.

    `ko yt <url>` → transcript to stdout (pipe to `ko llm` or redirect to a file).
    `ko yt <url> -s` → one-shot LLM summary (key points, notable claims, who should watch).
    `ko yt <url> --json` → [{start, text, duration}] snippet array.

    Shortcut: `ko <youtube-url>` routes here via bare-url dispatch in fetch.
    Fails cleanly when captions are disabled (no silent fall-through to the description page).
    """
    try:
        vid = yt_mod.video_id(url_or_id)
    except ValueError as e:
        _die(str(e), as_json=as_json, code="usage")

    if as_json:
        try:
            segs = yt_mod.snippets(vid)
        except yt_mod.YtError as e:
            _die(str(e), as_json=as_json)
        typer.echo(json.dumps([{"start": s.start, "text": s.text, "duration": s.duration} for s in segs]))
        return

    try:
        text = yt_mod.transcript(vid)
    except yt_mod.YtError as e:
        _die(str(e), as_json=as_json)

    # Rough token estimate: ~4 chars/token is the heuristic used across the codebase.
    token_est = len(text) // 4
    typer.echo(f"[{vid}  ~{token_est:,} tokens]", err=True)

    if summarize:
        from . import llm as llm_mod

        prompt = (
            "Summarize this video transcript: key points, notable claims, who should watch. "
            "Concise bullets, no preamble."
        )
        try:
            result = llm_mod.run(prompt, stdin=text, model=model)
        except Exception as e:
            _die(str(e))
        typer.echo(result)
        if llm_mod.last_cost:
            typer.echo(llm_mod.last_cost.note, err=True)
        return

    typer.echo(text)
