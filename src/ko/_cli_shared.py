"""Shared CLI root app and cross-group helper functions."""

from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import asdict
from types import ModuleType
from typing import TYPE_CHECKING, NoReturn

import typer

if TYPE_CHECKING:
    from pathlib import Path

# The root Typer app — imported by all group modules so they can self-register.
app = typer.Typer(
    help="ko — Ko's opinionated CLI: web/papers (exa, fetch, arxiv, hf, papers, hn), "
    "Google (gsheets, gdocs, cal, gmail), llm/agents, publish, and more. `ko doctor` for setup.",
    no_args_is_help=True,
    # Agents drive ko over bash: a clean one-line error on stderr + exit 1 is parseable;
    # Typer's default colorized multi-frame traceback box is not. We catch expected errors
    # in each command and emit them via _die(); this kills the pretty-traceback fallback.
    pretty_exceptions_enable=False,
    # `_route` treats `-h` as "help was already asked for"; without this Typer never
    # registers it, so `ko -h` died with "Try 'ko --help'" while the router thought it
    # was a help flag. Register it for real, everywhere.
    context_settings={"help_option_names": ["-h", "--help"]},
)


def lazy(name: str) -> ModuleType:
    """`ko.<name>`, loaded on first attribute access, not at import. Every command group
    registers at startup, so an eager `from . import llm` made `ko cal` pay for pydantic-ai,
    exa and the X SDK (~1.3 s of a 1.6 s start). Call sites stay `llm_mod.fn()`. A module whose
    constants feed `--help` text still loads at startup — so keep SDK imports inside its
    functions (llm, exa, x do)."""
    full = f"ko.{name}"
    if full in sys.modules:
        return sys.modules[full]
    spec = importlib.util.find_spec(full)
    spec.loader = importlib.util.LazyLoader(spec.loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[full] = module
    spec.loader.exec_module(module)
    return module


def _jsonable(obj):
    """Dataclasses (alone or in a list) to dicts; json.dumps' default=str does the rest."""
    from dataclasses import is_dataclass

    if is_dataclass(obj) and not isinstance(obj, type):
        return asdict(obj)
    if isinstance(obj, list | tuple):
        return [_jsonable(o) for o in obj]
    return obj


def _emit_json(obj) -> None:
    """Shared --json path: a dataclass, a list of them, or plain data -> JSON on stdout.
    `default=str` so a date or path never crashes the output."""
    typer.echo(json.dumps(_jsonable(obj), default=str))


# Our bugs, not the user's: re-raised so they crash with a traceback and a logged crash site.
_BUGS = (TypeError, AttributeError, NameError, ImportError)


def _die_from(e: Exception, *, as_json: bool = False) -> NoReturn:
    """`except Exception as e: _die_from(e, as_json=...)`: the error exit for a caught exception.
    A missing key or bad config exits as `config`, a Google token problem as `auth` with its fix,
    both user-fixable (logged as warnings). Programming errors re-raise."""
    from ko import config, google_auth

    if isinstance(e, _BUGS):
        raise e
    if isinstance(e, google_auth.AuthError):
        _die(e.msg, as_json=as_json, code="auth", fix=e.fix)
    if isinstance(e, config.MissingKeyError | config.ConfigError):
        _die(str(e), as_json=as_json, code="config")
    _die(str(e), as_json=as_json)


def _die(
    msg: str,
    *,
    as_json: bool = False,
    code: str = "error",
    exit_code: int | None = None,
    fix: str | None = None,
) -> NoReturn:
    """Error exit: a JSON error object to stderr under --json, else plain text.
    Exit code follows AGENTS.md — `code="usage"` → 2, everything else → 1 (override with
    exit_code). Keeps stdout clean so a downstream `... | jq` never sees a half-message.
    Puts the code label and message on the command's log event (`error`, `error_msg`). `fix` is
    the command that resolves it: a `fix` field under --json, "— run `…`" in text."""
    from ko import logs

    logs.add(error=code, error_msg=msg, fix=fix)
    if as_json:
        typer.echo(json.dumps({"error": msg, "code": code} | ({"fix": fix} if fix else {})), err=True)
    else:
        typer.echo(f"{msg} — run `{fix}`" if fix else msg, err=True)
    if exit_code is None:
        exit_code = 2 if code == "usage" else 1
    raise typer.Exit(exit_code)


def _write_doc(text: str, out: Path | None) -> None:
    """Print `text`, or write it to `out` (parents created) with a size note on stderr."""
    if out is None:
        typer.echo(text)
    else:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text)
        typer.echo(f"Wrote {len(text):,} chars to {out}", err=True)


def _reject_group_flags(ctx: typer.Context, **flags: str) -> None:
    """Refuse bare-group flags that were passed *before* a subcommand.

    Typer binds them to the group callback, which then returns early once a subcommand
    runs — so `ko gmail --json search foo` used to accept `--json`, ignore it, print plain
    text and exit 0. An agent asking for JSON got unparseable output with no signal; loud
    beats wrong. Pass `param_name="--flag"` for each flag that only applies to the bare
    group. `--json` among them still gets a JSON error object, since that's what was asked
    for. Group-wide options (e.g. `--account`) are not passed here — they apply to both.

    Compares the source by *name*: Typer vendors its own click, so the enum member handed
    back is `typer._click.core.ParameterSource.COMMANDLINE`, which `==` against the one
    imported from `click.core` is silently always False (no error, just a dead check).
    Same reason for the `is None` raise: click returns None for a name it doesn't know, so
    a typo'd or renamed param would quietly restore the accept-and-ignore behaviour."""
    # command_path, not command.name: nested groups need the full path, or `ko agent
    # sessions --json summarize` advises you about a `ko sessions` that doesn't exist.
    group = ctx.command_path
    stray = []
    for name, label in flags.items():
        src = ctx.get_parameter_source(name)
        if src is None:
            raise RuntimeError(
                f"_reject_group_flags: `{group}` has no parameter {name!r} "
                "— the guard would never fire"
            )
        if getattr(src, "name", None) == "COMMANDLINE":
            stray.append(label)
    if not stray:
        return
    shown = ", ".join(sorted(stray))
    verb = "applies" if len(stray) == 1 else "apply"
    # Deliberately no fabricated command line: the subcommand may not accept these flags
    # at all (`ko cal day` has no --days), and echoing them back reads as a promise it does.
    _die(
        f"{shown} {verb} to bare `{group}` only, not `{group} {ctx.invoked_subcommand}` "
        f"(see: {group} {ctx.invoked_subcommand} --help)",
        as_json="--json" in stray,
        code="usage",
    )


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
        return datetime.fromisoformat(iso).strftime("%d %b %Y")
    except ValueError:
        return iso[:10]  # fall back to the date portion


def _link(text: str, url: str, links: bool) -> str:
    """Clickable on a terminal (OSC 8); piped, the URL follows the text so an agent can use it."""
    return f"\033]8;;{url}\033\\{text}\033]8;;\033\\" if links else f"{text}  {url}"


def _clip(text: str, n: int = 180) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _big(n) -> str:
    """12, 3.4k, 1.05M, 1.2B; – for none."""
    if n is None:
        return "–"
    for unit, size in (("B", 1e9), ("M", 1e6), ("k", 1e3)):
        if n >= size * 0.9995:  # 999,600 is 1M, not "1e+03k"
            return f"{n / size:.3g}{unit}"
    return str(n)


def _table(rows: list[list[str]]) -> list[str]:
    """Left-aligned columns, two spaces apart; rows[0] is the header."""
    widths = [max(len(r[c]) for r in rows) + 2 for c in range(len(rows[0]))]
    return ["".join(cell.ljust(w) for cell, w in zip(r, widths, strict=True)).rstrip() for r in rows]
