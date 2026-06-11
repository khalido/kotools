"""ko — CLI entry point."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import typer

from . import arxiv as arxiv_mod
from . import doc as doc_mod
from . import exa as exa_mod
from . import google_auth
from . import hf as hf_mod
from . import hn as hn_mod
from . import gsheets as gsheets_mod
from .agents import research_run, research_repl


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
        typer.echo(json.dumps([asdict(p) for p in papers], default=str))
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
        typer.echo(json.dumps([asdict(s) for s in stories], default=str))
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


# --- agent ---

agent_app = typer.Typer(help="AI agents powered by pydantic-ai.", no_args_is_help=True)
app.add_typer(agent_app, name="agent")


@agent_app.command("research")
def agent_research(
    prompt: str = typer.Argument(
        None, help="research prompt; omit to enter interactive mode"
    ),
    model: str = typer.Option(
        None, "--model", "-m", help="model string, e.g. anthropic:claude-sonnet-4-6"
    ),
) -> None:
    """Research agent with web search via Exa. No prompt = interactive REPL."""
    import os

    if model:
        os.environ["KO_AGENT_MODEL"] = model
    if prompt:
        typer.echo(research_run(prompt))
    else:
        research_repl()


def main() -> None:
    """Entry point with a bare-argument shortcut: if the first arg is an
    existing file (not a command name), route to `ko doc` — so `ko paper.pdf`
    just works. Deterministic: command names always win over file names."""
    import sys

    args = sys.argv[1:]
    if args and not args[0].startswith("-"):
        known = {g.name for g in app.registered_groups} | {
            c.name for c in app.registered_commands
        }
        if args[0] not in known and Path(args[0]).is_file():
            sys.argv.insert(1, "doc")
    app()


if __name__ == "__main__":
    main()
