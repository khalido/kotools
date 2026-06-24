"""ko — CLI entry point."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import typer

from . import arxiv as arxiv_mod
from . import doc as doc_mod
from . import exa as exa_mod
from . import fetch as fetch_mod
from . import google_auth
from . import hf as hf_mod
from . import hn as hn_mod
from . import llm as llm_mod
from . import tmdb as tmdb_mod
from . import x as x_mod
from . import gsheets as gsheets_mod
from . import ticktick as ticktick_mod
from . import publish as publish_mod
from .agents import research_run, research_repl, tv_run, tv_repl


app = typer.Typer(
    help="ko — Ko's opinionated CLI (exa, arxiv, gsheets, doc, agent).",
    no_args_is_help=True,
)

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

gsheets_app = typer.Typer(
    help="Read Google Sheets. OAuth to your Google account on first run.",
    no_args_is_help=True,
)
app.add_typer(gsheets_app, name="gsheets")

x_app = typer.Typer(
    help="X (Twitter) posts via the official XDK (X_BEARER_TOKEN required, paid tier for reads).",
    no_args_is_help=True,
)
app.add_typer(x_app, name="x")

tt_app = typer.Typer(
    help="TickTick lists/tasks, read-only via its hosted MCP (TICKTICK_API_KEY). Manage tasks in TickTick itself.",
    no_args_is_help=True,
)
app.add_typer(tt_app, name="tt")

publish_app = typer.Typer(
    help="Publish a folder to Cloudflare as a static site (needs wrangler). `ko publish new <dir>` scaffolds; `ko publish [dir]` deploys.",
    no_args_is_help=True,
)
app.add_typer(publish_app, name="publish")


@app.callback()
def _startup() -> None:
    """Runs before every command: make config.toml [keys] available as env vars."""
    from . import config

    config.load_keys_into_env()


def _emit_json(items: list) -> None:
    """Shared --json path: list of dataclasses -> one JSON array on stdout."""
    typer.echo(json.dumps([asdict(i) for i in items], default=str))


# --- arxiv ---


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
) -> None:
    """Search arxiv, newest first. Defaults to the last 18 months."""
    results = arxiv_mod.search(query, since_months=since, max_results=n)
    if not results:
        typer.echo(f"No results for '{query}' in the last {since} months.")
        raise typer.Exit(0)
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
    content = arxiv_mod.fetch(arxiv_id)
    if out is None:
        typer.echo(content)
    else:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(content)
        typer.echo(f"Wrote {len(content):,} chars to {out}", err=True)


# --- exa ---


def _parse_domains(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [d.strip() for d in value.split(",") if d.strip()]


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
        False, "--no-text", help="skip content retrieval (faster; title + url only)"
    ),
    long: bool = typer.Option(False, "--long", "-l", help="show text excerpts"),
) -> None:
    """Semantic search. For labs/faculty use --domains .edu; for uni pages use --domains <uni>."""
    results = exa_mod.search(
        query,
        n=n,
        include_domains=_parse_domains(domains),
        exclude_domains=_parse_domains(exclude),
        since_months=since,
        start_published_date=start_date,
        end_published_date=end_date,
        with_text=not no_text,
    )
    if not results:
        typer.echo(f"No results for '{query}'.")
        raise typer.Exit(0)
    for r in results:
        date = r.published_date or "-"
        typer.echo(f"{r.score:.2f}  {date}  {r.title}")
        typer.echo(f"  {r.url}")
        if long and r.text:
            excerpt = r.text[:400].replace("\n", " ")
            typer.echo(f"  {excerpt}{'…' if len(r.text) > 400 else ''}")
        typer.echo("")


@exa_app.command("get")
def exa_get(
    urls: list[str] = typer.Argument(..., help="one or more URLs to fetch"),
    out: Path = typer.Option(
        None, "--out", "-o", help="write combined markdown to this path"
    ),
) -> None:
    """Fetch clean markdown for known URLs. Prints concatenated markdown to stdout by default."""
    contents = exa_mod.get_contents(urls)
    sections = [f"# {url}\n\n{text}" for url, text in contents.items()]
    combined = "\n\n---\n\n".join(sections)
    if out is None:
        typer.echo(combined)
    else:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(combined)
        typer.echo(f"Wrote {len(combined):,} chars to {out}", err=True)


# --- hf ---


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
    _echo_papers(hf_mod.top(n=n, date=date), as_json, long)


@hf_app.command("search")
def hf_search(
    query: str = typer.Argument(..., help="search query (semantic + full-text)"),
    n: int = typer.Option(hf_mod.DEFAULT_SEARCH_N, "--n", help="max results"),
    long: bool = typer.Option(False, "--long", "-l", help="include summary excerpt"),
    as_json: bool = typer.Option(False, "--json", help="emit JSON instead of text"),
) -> None:
    """Search AI papers on hf.co/papers (covers title, authors, content)."""
    results = hf_mod.search(query, n=n)
    if not results:
        typer.echo(f"No results for '{query}'.")
        raise typer.Exit(0)
    _echo_papers(results, as_json, long)


@hf_app.command("info")
def hf_info(
    ref: str = typer.Argument(..., help="arxiv id, arxiv URL, or hf.co/papers URL"),
    as_json: bool = typer.Option(False, "--json", help="emit JSON instead of text"),
) -> None:
    """One paper's metadata: upvotes, github + stars, AI summary, linked models/datasets/spaces."""
    p = hf_mod.info(ref)
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
    content = hf_mod.get(ref)
    if out is None:
        typer.echo(content)
    else:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(content)
        typer.echo(f"Wrote {len(content):,} chars to {out}", err=True)


# --- hn ---


def _echo_stories(stories: list[hn_mod.Story], as_json: bool) -> None:
    if as_json:
        _emit_json(stories)
        return
    for s in stories:
        date = s.created_at.strftime("%Y-%m-%d")
        typer.echo(f"{s.id}  {s.points:>4}pts  {s.num_comments:>4}c  {date}  {s.title}")
        typer.echo(f"  {s.url or s.hn_url}")
        typer.echo("")


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
    _echo_stories(hn_mod.top(n=n, days=days), as_json)


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
    results = hn_mod.search(
        query, n=n, since_months=since, by_date=new, min_comments=min_comments
    )
    if not results:
        typer.echo(f"No results for '{query}'.")
        raise typer.Exit(0)
    _echo_stories(results, as_json)


@hn_app.command("item")
def hn_item(
    item_id: str = typer.Argument(..., help="story id (first column of top/search)"),
    n: int = typer.Option(
        hn_mod.DEFAULT_MAX_COMMENTS, "--n", help="max comments to show (0 = all)"
    ),
) -> None:
    """One story + its comment tree as readable indented text."""
    story, comments = hn_mod.item(item_id, max_comments=n)
    date = story.created_at.strftime("%Y-%m-%d")
    typer.echo(f"{story.title}  ({story.points}pts, {date})")
    if story.url:
        typer.echo(story.url)
    typer.echo(story.hn_url)
    for c in comments:
        pad = "  " * (c.depth + 1)
        typer.echo(f"\n{pad}[{c.author}]")
        for line in c.text.splitlines():
            typer.echo(f"{pad}{line}" if line else "")


# --- gsheets ---


def _emit_rows(rows: list[list], as_json: bool) -> None:
    if as_json:
        typer.echo(json.dumps(rows, default=str))
        return
    # Default: TSV — survives commas, pipes into cut/awk/mlr cleanly
    for row in rows:
        typer.echo("\t".join("" if c is None else str(c) for c in row))


@gsheets_app.command("info")
def gsheets_info(
    spreadsheet_id: str = typer.Argument(
        ..., help="Google Sheet ID (the part between /d/ and /edit in the URL)"
    ),
    as_json: bool = typer.Option(
        False, "--json", help="emit JSON instead of plain text"
    ),
) -> None:
    """Show a sheet's title and tab names."""
    info = gsheets_mod.get_info(spreadsheet_id)
    if as_json:
        typer.echo(json.dumps(asdict(info)))
        return
    typer.echo(info.title)
    for t in info.tabs:
        typer.echo(f"  {t}")


@gsheets_app.command("tabs")
def gsheets_tabs(
    spreadsheet_id: str = typer.Argument(..., help="Google Sheet ID"),
) -> None:
    """List tab names, one per line (machine-friendly)."""
    info = gsheets_mod.get_info(spreadsheet_id)
    for t in info.tabs:
        typer.echo(t)


@gsheets_app.command("get")
def gsheets_get(
    spreadsheet_id: str = typer.Argument(..., help="Google Sheet ID"),
    range_name: str = typer.Argument(
        ..., help="A1 range, e.g. 'Jobs!A4:T10' or \"'New biz props'!A4:S100\""
    ),
    as_json: bool = typer.Option(
        False, "--json", help="emit JSON (2D array) instead of TSV"
    ),
    raw: bool = typer.Option(
        False, "--raw", help="return raw unformatted values (numbers stay numbers)"
    ),
    formula: bool = typer.Option(
        False, "--formula", help="return formulas instead of values"
    ),
) -> None:
    """Fetch a range. TSV by default; use --json for structured output."""
    if raw and formula:
        typer.echo("--raw and --formula are mutually exclusive", err=True)
        raise typer.Exit(2)
    render = "UNFORMATTED_VALUE" if raw else "FORMULA" if formula else "FORMATTED_VALUE"
    rows = gsheets_mod.get_range(spreadsheet_id, range_name, value_render=render)
    _emit_rows(rows, as_json)


@gsheets_app.command("auth")
def gsheets_auth(
    out: bool = typer.Option(
        False, "--logout", help="remove the cached token and exit"
    ),
) -> None:
    """Trigger or reset Google OAuth. Opens a browser on first run."""
    if out:
        removed = google_auth.logout()
        typer.echo("Signed out." if removed else "No cached token to remove.")
        return
    google_auth.get_credentials(readonly=True)
    typer.echo(f"Signed in. Token cached at {google_auth.TOKEN_FILE}.")


# --- doc ---


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
    text = doc_mod.parse(file, pages=pages, ocr=not no_ocr)
    if out is None:
        typer.echo(text)
    else:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text)
        typer.echo(f"Wrote {len(text):,} chars to {out}", err=True)


# --- x ---


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
    posts = x_mod.search(query, n=n, days=days, top=top)
    if not posts:
        typer.echo(f"No posts for '{query}' in the last {days} days.")
        raise typer.Exit(0)
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
    posts = x_mod.list_posts(name, n=n)
    if not posts:
        typer.echo(f"No recent posts in list '{name}'.")
        raise typer.Exit(0)
    _echo_posts(posts, as_json)


@x_app.command("lists")
def x_lists() -> None:
    """Your X lists (owned + followed), one per line."""
    for lst in x_mod.my_lists():
        typer.echo(f"{lst.id}\t{lst.name}")


# --- llm ---


@app.command("llm")
def llm_cmd(
    prompt: str = typer.Argument(
        ..., help="what to do; piped stdin becomes the input text"
    ),
    model: str = typer.Option(
        None,
        "--model",
        "-m",
        help=f"model string, e.g. {llm_mod.FALLBACK_MODEL} (default: KO_DEFAULT_MODEL or that)",
        autocompletion=llm_mod.available_models,
    ),
    system: str = typer.Option(
        None, "--system", "-s", help="replace the default system prompt"
    ),
) -> None:
    """One-shot LLM call, no tools: `ko hn item 123 | ko llm "summarize the debate"`."""
    import sys

    llm_mod.refresh_openrouter_models()  # keeps -m autocomplete catalog fresh (once/day)
    stdin = None if sys.stdin.isatty() else sys.stdin.read()
    typer.echo(llm_mod.run(prompt, stdin=stdin, model=model, system=system))


@app.command("models")
def models_cmd(
    refresh: bool = typer.Option(
        False,
        "--refresh",
        help="force re-fetch the OpenRouter catalog, ignoring the 24h cache",
    ),
) -> None:
    """List model strings usable with -m, one per line — filtered to providers whose key is set."""
    llm_mod.refresh_openrouter_models(force=refresh)
    for name in sorted(llm_mod.available_models()):
        typer.echo(name)


# --- fetch ---


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
    r = fetch_mod.fetch(
        url, archive=archive, archive_is=archive_is, date=date, save=not no_save
    )
    if r.note:
        typer.echo(f"[{r.note}]", err=True)
    if out is None:
        typer.echo(r.text)
    else:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(r.text)
        typer.echo(f"Wrote {len(r.text):,} chars to {out}", err=True)


# --- tv (tmdb) ---


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
    top, rest = tmdb_mod.lookup(query, kind=kind, year=year, country=country)
    if top is None:
        typer.echo(f"No matches for '{query}'.")
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


# --- doctor ---


@app.command("doctor")
def doctor() -> None:
    """Setup health check: every tool, what it needs, and whether it's ready."""
    import shutil
    import sys

    from rich.console import Console
    from rich.table import Table

    from . import config, google_auth, llm as _llm

    def env(var: str) -> tuple[str, str]:
        return {
            "env": ("✓ env", "green"),
            "config": ("✓ config", "cyan"),
        }.get(config.key_source(var), ("✗ missing", "red"))

    arxiv2md = (
        shutil.which("arxiv2md") or (Path(sys.executable).parent / "arxiv2md").exists()
    )
    soffice = (
        bool(shutil.which("soffice")) or Path("/Applications/LibreOffice.app").exists()
    )
    default = _llm.default_model()
    default_key = _llm.PROVIDER_KEYS.get(default.split(":")[0], "")

    rows: list[tuple[str, str, str, tuple[str, str]]] = [
        ("hn", "Hacker News top/search/comments", "—", ("✓ no auth", "green")),
        ("hf", "HF papers: daily feed, search, metadata", "—", ("✓ no auth", "green")),
        (
            "fetch",
            "URL → markdown (articles, PDFs, Wayback)",
            "—",
            ("✓ no auth", "green"),
        ),
        (
            "arxiv",
            "arxiv search + paper → markdown",
            "arxiv2md binary",
            ("✓ found", "green") if arxiv2md else ("✗ missing", "red"),
        ),
        (
            "doc",
            "PDF/Office/image → text (local)",
            "LibreOffice for Office files",
            ("✓ found", "green") if soffice else ("– optional", "yellow"),
        ),
        ("exa", "semantic web search (paid)", "EXA_API_KEY", env("EXA_API_KEY")),
        (
            "llm",
            f"one-shot LLM (default {default})",
            default_key or "?",
            env(default_key) if default_key else ("? unknown provider", "yellow"),
        ),
        ("x", "X posts (paid tier for reads)", "X_BEARER_TOKEN", env("X_BEARER_TOKEN")),
        (
            "tt",
            "TickTick lists/tasks (read-only, via MCP)",
            "TICKTICK_API_KEY",
            env("TICKTICK_API_KEY"),
        ),
        (
            "publish",
            "deploy a folder to Cloudflare (static)",
            "wrangler + KO_CLOUDFLARE_API_TOKEN/ACCOUNT_ID",
            {
                "local": ("✓ repo-local", "green"),
                "path": ("✓ on PATH", "green"),
                "env": ("✓ KO_WRANGLER", "green"),
                "npx": ("– via npx (run `npm install`)", "yellow"),
            }.get(publish_mod.wrangler_source(), ("✗ no wrangler/npx", "red")),
        ),
        (
            "agent",
            "research agent (web search via exa)",
            "OPENROUTER_API_KEY + EXA_API_KEY",
            env("OPENROUTER_API_KEY"),
        ),
        (
            "tv",
            "movie/TV info + where to stream",
            "TMDB_READ_ACCESS_TOKEN",
            env("TMDB_READ_ACCESS_TOKEN"),
        ),
        (
            "gsheets",
            "read Google Sheets",
            "OAuth client + token",
            ("✓ authed", "green")
            if google_auth.TOKEN_FILE.exists()
            else ("– run `ko gsheets auth`", "yellow")
            if google_auth.CLIENT_FILE.exists()
            else ("✗ no client file", "red"),
        ),
    ]

    table = Table(title="ko doctor", title_justify="left")
    table.add_column("tool", style="bold")
    table.add_column("does")
    table.add_column("needs", style="dim")
    table.add_column("status")
    for name, does, needs, (status, color) in rows:
        table.add_row(name, does, needs, f"[{color}]{status}[/{color}]")
    Console().print(table)

    _llm.refresh_openrouter_models()  # populate/refresh the OR catalog cache
    extra = [m for m in _llm.available_models() if ":" in m]
    providers = sorted({m.split(":")[0] for m in extra})
    Console().print(
        f"[dim]models available to -m: {len(extra)} across {', '.join(providers) or 'none'}[/dim]"
    )


# --- agent ---

agent_app = typer.Typer(help="AI agents powered by pydantic-ai.", no_args_is_help=True)
app.add_typer(agent_app, name="agent")
app.add_typer(agent_app, name="a", hidden=True)  # shortcut: `ko a research`


@agent_app.command("research")
def agent_research(
    prompt: str = typer.Argument(
        None, help="research prompt; omit to enter interactive mode"
    ),
    model: str = typer.Option(
        None, "--model", "-m", help="model override, e.g. openrouter:anthropic/claude-sonnet-4"
    ),
    resume: str = typer.Option(
        None, "--resume", "-r", help="resume a saved session id (enters interactive mode)"
    ),
) -> None:
    """Research agent across web (exa), papers (arxiv, hf), and HN. No prompt = interactive REPL."""
    if prompt:
        research_run(prompt, model=model, resume=resume)  # prints itself; resume continues a session
    else:
        research_repl(model=model, resume=resume)


@agent_app.command("tv")
def agent_tv(
    prompt: str = typer.Argument(
        None, help="what you're in the mood for; omit to enter interactive mode"
    ),
    model: str = typer.Option(None, "--model", "-m", help="model override"),
    resume: str = typer.Option(
        None, "--resume", "-r", help="resume a saved session id (enters interactive mode)"
    ),
) -> None:
    """TV/movie agent — what to watch in Australia, tuned to Ko. No prompt = interactive REPL."""
    if prompt:
        tv_run(prompt, model=model, resume=resume)
    else:
        tv_repl(model=model, resume=resume)


@agent_app.command("sessions")
def agent_sessions() -> None:
    """List saved agent sessions, newest first (TSV: id, agent, model, title)."""
    from ko import sessions

    rows = sessions.listing()
    if not rows:
        typer.echo("no sessions yet", err=True)
        raise typer.Exit(0)
    for s in rows:
        typer.echo(f"{s['id']}\t{s['agent']}\t{s['model']}\t{s['title']}")


# --- ticktick ---


@tt_app.command("lists")
def tt_lists(
    json_out: bool = typer.Option(False, "--json", help="JSON instead of TSV"),
) -> None:
    """List your TickTick lists (TSV: id, name)."""
    projects = ticktick_mod.list_projects()
    if json_out:
        _emit_json(projects)
        return
    if not projects:
        typer.echo("no lists", err=True)
        raise typer.Exit(0)
    for p in projects:
        typer.echo(f"{p.id}\t{p.name}")


@tt_app.command("items")
def tt_items(
    list_name: str = typer.Argument(..., help="list name (substring ok) or id"),
    json_out: bool = typer.Option(False, "--json", help="JSON instead of TSV"),
) -> None:
    """Open tasks in a list (TSV: priority, due, title). `ko tt <list>` is a shortcut."""
    proj = ticktick_mod.resolve_list(list_name)
    if not proj:
        typer.echo(f"no list matching {list_name!r}", err=True)
        raise typer.Exit(1)
    tasks = ticktick_mod.get_tasks(proj.id)
    if json_out:
        _emit_json(tasks)
        return
    if not tasks:
        typer.echo(f"{proj.name}: no open tasks", err=True)
        raise typer.Exit(0)
    for t in tasks:
        typer.echo(f"{t.priority}\t{t.due or '—'}\t{t.title}")


# --- publish ---


@publish_app.command("new")
def publish_new(
    path: str = typer.Argument(..., help="folder to scaffold (created if missing)"),
    title: str = typer.Option(None, "--title", "-t", help="page title (default: folder name)"),
    md: bool = typer.Option(False, "--md", help="markdown doc site (write .md, README is the hub)"),
    bare: bool = typer.Option(False, "--bare", help="just a CLAUDE.md of hints; build from scratch"),
) -> None:
    """Scaffold a site to publish. Default: static (Tailwind + Alpine). `--md` or `--bare`."""
    from pathlib import Path

    if md and bare:
        typer.echo("--md and --bare are mutually exclusive", err=True)
        raise typer.Exit(2)
    mode = "md" if md else "bare" if bare else "static"
    folder = Path(path)
    written = publish_mod.scaffold(folder, title=title, mode=mode)
    if written:
        for p in written:
            typer.echo(f"created {p}")
    else:
        typer.echo(f"{folder} already scaffolded (nothing overwritten)", err=True)
    edit = "README.md" if mode == "md" else "index.html"
    hint = f"edit {folder}/{edit}, then: ko publish {folder}"
    if mode == "bare":
        hint = f"build an index.html in {folder}, then: ko publish {folder}"
    typer.echo(f"\n{hint}", err=True)


@publish_app.command("up")
def publish_up(
    path: str = typer.Argument(".", help="folder to publish (default: current dir)"),
    name: str = typer.Option(
        None, "--name", "-n", help="subdomain/name (default: derived from folder, sticky)"
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="take over an existing subdomain/Worker name"
    ),
) -> None:
    """Deploy a folder to Cloudflare. Prints the URL. Re-running overwrites the same URL."""
    from pathlib import Path

    try:
        url = publish_mod.deploy(Path(path), name=name, force=force)
    except RuntimeError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from None
    if url:
        typer.echo(url)  # stdout: the URL itself (pipeable)
        code = publish_mod.check_url(url)  # sanity check: is it actually live?
        if code == 200:
            typer.echo("✓ live (HTTP 200)", err=True)
        elif code:
            typer.echo(f"⚠ deployed but HTTP {code} (cert may still be provisioning)", err=True)
        else:
            typer.echo("⚠ deployed but not reachable yet (cert may be provisioning)", err=True)
    else:
        typer.echo("deployed — couldn't parse the URL; run `wrangler deployments list`", err=True)


@publish_app.command("list")
def publish_list() -> None:
    """List everything published (TSV: name, url, folder)."""
    rows = publish_mod.published()
    if not rows:
        typer.echo("nothing published yet", err=True)
        raise typer.Exit(0)
    for p in rows:
        typer.echo(f"{p.name}\t{p.url}\t{p.folder}")


def main() -> None:
    """Entry point with deterministic bare-argument shortcuts (command names
    always win): `ko paper.pdf` routes to `ko doc`, and `ko x ai` routes to
    `ko x list ai` (anything after `x` that isn't an x command is a list name).
    """
    import sys

    args = sys.argv[1:]
    if args and not args[0].startswith("-"):
        known = {g.name for g in app.registered_groups} | {
            c.name for c in app.registered_commands
        }
        if args[0].startswith(("http://", "https://")):
            sys.argv.insert(1, "fetch")
        elif args[0] not in known and Path(args[0]).is_file():
            sys.argv.insert(1, "doc")
        elif args[0] == "x" and len(args) > 1 and not args[1].startswith("-"):
            x_known = {c.name for c in x_app.registered_commands}
            if args[1] not in x_known:
                sys.argv.insert(2, "list")
        elif args[0] == "tt" and len(args) > 1 and not args[1].startswith("-"):
            tt_known = {c.name for c in tt_app.registered_commands}
            if args[1] not in tt_known:
                sys.argv.insert(2, "items")  # `ko tt Shopping` -> `ko tt items Shopping`
        elif args[0] == "publish":
            pub_known = {c.name for c in publish_app.registered_commands}
            # `ko publish` / `ko publish ./dir` / `ko publish -n foo` -> `... up ...`,
            # but leave subcommands and `--help` to the group.
            if not args[1:] or (args[1] not in pub_known and args[1] not in ("--help", "-h")):
                sys.argv.insert(2, "up")
    app()


if __name__ == "__main__":
    main()
