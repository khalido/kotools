"""ko — CLI entry point."""

from __future__ import annotations

from pathlib import Path

import typer

from ._cli_shared import app, _die, _no_results, _fmt_day  # noqa: F401 — re-exported for tests
from . import cli_google, cli_web, cli_ai  # noqa: F401  — triggers sub-app self-registration

# These sub-app objects are needed by main()'s bare-arg dispatcher.
from .cli_web import x_app
from .cli_ai import tt_app, publish_app

# Re-export group-local helpers that tests import via ko.cli
from .cli_google import _short_from, _parse_cells, _norm_block  # noqa: F401

from . import publish as publish_mod


def _version_callback(value: bool) -> None:
    if value:
        from importlib.metadata import version

        typer.echo(f"ko {version('kotools')}")
        raise typer.Exit()


@app.callback()
def _startup(
    version: bool = typer.Option(
        None, "--version", "-V", callback=_version_callback, is_eager=True, help="show version and exit"
    ),
) -> None:
    """Runs before every command: make config.toml [keys] available as env vars."""
    from . import config

    config.load_keys_into_env()


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
            "read & write Google Sheets",
            "OAuth client + token",
            (f"✓ authed ({google_auth.active_account()})", "green")
            if google_auth.token_file().exists()
            else ("– run `ko gsheets auth`", "yellow")
            if google_auth.client_file().exists()
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


def main() -> None:
    """Entry point with deterministic bare-argument shortcuts (command names
    always win): `ko paper.pdf` routes to `ko doc`, and `ko x ai` routes to
    `ko x list ai` (anything after `x` that isn't an x command is a list name).
    """
    import sys

    args = sys.argv[1:]
    # `help` as a trailing word == --help, so you can slap it on any command:
    # `ko help`, `ko exa help`, `ko exa search help`. (To *search* for the literal word
    # "help", use the flag form for help instead — `ko exa search --help` is help.)
    if args and args[-1] == "help" and "--help" not in args and "-h" not in args:
        sys.argv[-1] = "--help"
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
