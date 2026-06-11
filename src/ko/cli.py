"""ko — CLI entry point."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import typer

from . import arxiv as arxiv_mod
from . import exa as exa_mod
from . import google_auth
from . import gsheets as gsheets_mod
from .agents import research_run, research_repl


app = typer.Typer(
    help="ko — Ko's opinionated CLI (exa, arxiv, gsheets, agent).",
    no_args_is_help=True,
)

arxiv_app = typer.Typer(help="arxiv search + paper fetch.", no_args_is_help=True)
app.add_typer(arxiv_app, name="arxiv")

exa_app = typer.Typer(
    help="exa semantic web search (EXA_API_KEY required).", no_args_is_help=True
)
app.add_typer(exa_app, name="exa")

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
    long: bool = typer.Option(
        False, "--long", "-l", help="include abstract summary"
    ),
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
    as_json: bool = typer.Option(False, "--json", help="emit JSON instead of plain text"),
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
    render = (
        "UNFORMATTED_VALUE" if raw else "FORMULA" if formula else "FORMATTED_VALUE"
    )
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


# --- agent ---

agent_app = typer.Typer(help="AI agents powered by pydantic-ai.", no_args_is_help=True)
app.add_typer(agent_app, name="agent")


@agent_app.command("research")
def agent_research(
    prompt: str = typer.Argument(None, help="research prompt; omit to enter interactive mode"),
    model: str = typer.Option(None, "--model", "-m", help="model string, e.g. anthropic:claude-sonnet-4-6"),
) -> None:
    """Research agent with web search via Exa. No prompt = interactive REPL."""
    import os
    if model:
        os.environ["KO_AGENT_MODEL"] = model
    if prompt:
        typer.echo(research_run(prompt))
    else:
        research_repl()


if __name__ == "__main__":
    app()
