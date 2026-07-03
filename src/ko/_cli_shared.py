"""Shared CLI root app and cross-group helper functions."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import NoReturn

import typer

# The root Typer app — imported by all group modules so they can self-register.
app = typer.Typer(
    help="ko — Ko's opinionated CLI: web/papers (exa, fetch, arxiv, hf, papers, hn), "
    "Google (gsheets, gdocs, cal, gmail), llm/agents, publish, and more. `ko doctor` for setup.",
    no_args_is_help=True,
    # Agents drive ko over bash: a clean one-line error on stderr + exit 1 is parseable;
    # Typer's default colorized multi-frame traceback box is not. We catch expected errors
    # in each command and emit them via _die(); this kills the pretty-traceback fallback.
    pretty_exceptions_enable=False,
)


def _emit_json(items: list) -> None:
    """Shared --json path: list of dataclasses -> one JSON array on stdout."""
    typer.echo(json.dumps([asdict(i) for i in items], default=str))


def _die(
    msg: str, *, as_json: bool = False, code: str = "error", exit_code: int | None = None
) -> NoReturn:
    """Error exit: a JSON error object to stderr under --json, else plain text.
    Exit code follows AGENTS.md — `code="usage"` → 2, everything else → 1 (override with
    exit_code). Keeps stdout clean so a downstream `... | jq` never sees a half-message."""
    if as_json:
        typer.echo(json.dumps({"error": msg, "code": code}), err=True)
    else:
        typer.echo(msg, err=True)
    if exit_code is None:
        exit_code = 2 if code == "usage" else 1
    raise typer.Exit(exit_code)


def _no_results(note: str, as_json: bool) -> NoReturn:
    """Empty-result exit (exit 0, not an error): under --json emit `[]` to stdout so the pipe
    stays valid JSON; the human-readable note always goes to stderr, never stdout."""
    if as_json:
        typer.echo("[]")
    typer.echo(note, err=True)
    raise typer.Exit(0)


def _tsv_cell(value) -> str:
    """One value rendered safe for a single TSV field: an embedded tab or newline would
    otherwise split the field / spill the row onto the next line (silently mis-shaping the
    output an agent parses with `cut -f`). Tabs → `\\t`, newlines → `\\n`; content preserved,
    one row stays one line. `None` → ''."""
    if value is None:
        return ""
    s = str(value).replace("\r\n", "\n").replace("\r", "\n")
    return s.replace("\t", "\\t").replace("\n", "\\n")


def _fmt_day(iso: str | None) -> str:
    """An ISO date/datetime string -> human '22 Jun 2018' (day only, no time); '' if missing."""
    if not iso:
        return ""
    from datetime import datetime

    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).strftime("%d %b %Y")
    except ValueError:
        return iso[:10]  # fall back to the date portion
