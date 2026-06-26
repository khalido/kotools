"""Shared CLI root app and cross-group helper functions."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import NoReturn

import typer

# The root Typer app — imported by all group modules so they can self-register.
app = typer.Typer(
    help="ko — Ko's opinionated CLI (exa, arxiv, gsheets, doc, agent).",
    no_args_is_help=True,
    # Agents drive ko over bash: a clean one-line error on stderr + exit 1 is parseable;
    # Typer's default colorized multi-frame traceback box is not. We catch expected errors
    # in each command and emit them via _die(); this kills the pretty-traceback fallback.
    pretty_exceptions_enable=False,
)


def _emit_json(items: list) -> None:
    """Shared --json path: list of dataclasses -> one JSON array on stdout."""
    typer.echo(json.dumps([asdict(i) for i in items], default=str))


def _die(msg: str, *, as_json: bool = False, code: str = "error") -> NoReturn:
    """Runtime-error exit: a JSON error object to stderr under --json, else plain text; exit 1.
    Keeps stdout clean so a downstream `... | jq` never sees a half-message."""
    if as_json:
        typer.echo(json.dumps({"error": msg, "code": code}), err=True)
    else:
        typer.echo(msg, err=True)
    raise typer.Exit(1)


def _no_results(note: str, as_json: bool) -> NoReturn:
    """Empty-result exit (exit 0, not an error): under --json emit `[]` to stdout so the pipe
    stays valid JSON; the human-readable note always goes to stderr, never stdout."""
    if as_json:
        typer.echo("[]")
    typer.echo(note, err=True)
    raise typer.Exit(0)


def _fmt_day(iso: str | None) -> str:
    """An ISO date/datetime string -> human '22 Jun 2018' (day only, no time); '' if missing."""
    if not iso:
        return ""
    from datetime import datetime

    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).strftime("%d %b %Y")
    except ValueError:
        return iso[:10]  # fall back to the date portion
