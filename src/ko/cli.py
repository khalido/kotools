"""ko — CLI entry point."""

from __future__ import annotations

from pathlib import Path

import typer

from ._cli_shared import app, _die, _no_results, _fmt_day, _tsv_cell  # noqa: F401 — re-exported for tests
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
    """Runs before every command: make config.toml [keys] available as env vars,
    and instrument LLM telemetry if opted in ([telemetry] enabled — off by default)."""
    from . import config, telemetry

    config.load_keys_into_env()
    telemetry.setup()


@app.command("doctor")
def doctor() -> None:
    """Setup health check: every tool, what it needs, and whether it's ready."""
    import shutil
    import sys

    from rich.console import Console
    from rich.table import Table

    from . import config, google_auth, llm as _llm, mcp_client as mcp_client_mod
    from . import telemetry as telemetry_mod

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

    try:
        _mcp_servers = mcp_client_mod.load_servers()
        _mcp_status = (
            (f"✓ {len(_mcp_servers)} server{'s' if len(_mcp_servers) != 1 else ''} configured", "green")
            if _mcp_servers
            else ("– no mcp.json", "yellow")
        )
    except mcp_client_mod.MCPTestError:
        _mcp_status = ("✗ invalid mcp.json", "red")

    rows: list[tuple[str, str, str, tuple[str, str]]] = [
        ("hn", "Hacker News top/search/comments", "—", ("✓ no auth", "green")),
        ("hf", "HF papers: daily feed, search, metadata", "—", ("✓ no auth", "green")),
        ("yt", "YouTube → transcript (no auth, no download)", "—", ("✓ no auth", "green")),
        (
            "brief",
            "morning brief (cal+gmail+hn+hf → LLM)",
            f"{default_key or '?'} + Google auth (optional-degrade)",
            env(default_key) if default_key else ("? unknown provider", "yellow"),
        ),
        (
            "papers",
            "cross-publisher paper search + citation graph",
            "S2_API_KEY (optional: tldr/similar)",
            env("S2_API_KEY")
            if config.key_source("S2_API_KEY")
            else ("– optional", "yellow"),
        ),
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
            "TickTick lists/tasks (read-only, via MCP) — developer.ticktick.com",
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
            "movie/TV info + where to stream — themoviedb.org API settings",
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
        (
            "gdocs/cal/gmail",
            "Google Docs/Calendar/Gmail (shares gsheets token; gmail read-only)",
            "OAuth client + token (same as gsheets)",
            (f"✓ authed ({google_auth.active_account()})", "green")
            if google_auth.token_file().exists()
            else ("– run `ko gsheets auth`", "yellow")
            if google_auth.client_file().exists()
            else ("✗ no client file", "red"),
        ),
        ("mcp", "inspect/call MCP servers by name or url", "~/.config/ko/mcp.json", _mcp_status),
        (
            "billing",
            "balance + usage across paid services (OpenRouter)",
            "OPENROUTER_API_KEY",
            env("OPENROUTER_API_KEY"),
        ),
        (
            "telemetry",
            "LLM traces → PostHog (opt-in, metadata-only by default)",
            "[telemetry] enabled + POSTHOG_API_KEY",
            ("✓ on", "green")
            if telemetry_mod.enabled() and config.key_source("POSTHOG_API_KEY")
            else ("✗ enabled but no POSTHOG_API_KEY", "red")
            if telemetry_mod.enabled()
            else ("– off (default)", "yellow"),
        ),
    ]

    table = Table(title="ko doctor", title_justify="left")
    table.add_column("tool", style="bold")
    table.add_column("does")
    table.add_column("needs", style="dim", overflow="fold")
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

    # effective non-secret settings + where each resolves from (env / config.toml / default)
    from . import dirs, gcal, publish as _publish
    from .google_auth import active_account
    from .x import _default_handle

    settings = [
        ("llm model", _llm.default_model(), config.setting_source("KO_DEFAULT_MODEL", "llm", "model")),
        (
            "agent model",
            config.setting("KO_AGENT_MODEL", "agents", "model", "(per-agent default)"),
            config.setting_source("KO_AGENT_MODEL", "agents", "model"),
        ),
        *(
            (f"{tier} tier", _llm.model_for(tier), "config" if config.get("llm", tier) else "default")
            for tier in ("basic", "medium", "smart", "ultra")
        ),
        ("cal timezone", gcal.tz_name(), "config" if config.get("cal", "timezone") else "default"),
        ("x handle", _default_handle(), config.setting_source("KO_X_HANDLE", "x", "handle")),
        ("google account", active_account(), config.setting_source("KO_GOOGLE_ACCOUNT", "google", "account")),
        ("publish domain", _publish.publish_domain() or "(workers.dev)", "config" if config.get("publish", "domain") else "default"),
    ]
    Console().print(
        "[dim]settings: "
        + " · ".join(f"{name}={val} ({src})" for name, val, src in settings)
        + "[/dim]"
    )
    Console().print(
        f"[dim]dirs: config={dirs.config_dir()} · state={dirs.state_dir()} · cache={dirs.cache_dir()}[/dim]"
    )
    if err := config.config_error():
        Console().print(f"[red]⚠ config.toml is malformed — keys/settings above are IGNORED: {err}[/red]")
    elif not (dirs.config_dir() / "config.toml").exists():
        Console().print("[dim]no config.toml (env vars + baked defaults only)[/dim]")

    # live OpenRouter key check — /credits is a free authenticated GET, so this both
    # proves the key works and shows money left (the agent-tier default rides on it)
    if config.key_source("OPENROUTER_API_KEY"):
        from . import billing

        b = billing.openrouter()  # never raises — errors land in b.error
        if b.error:
            Console().print(f"[red]openrouter: key set but /credits failed — {b.error}[/red]")
        elif b.remaining is not None:
            Console().print(
                f"[dim]openrouter: key works — ${b.remaining:.2f} of ${b.total:.2f} credits left"
                + (f" ({b.trend})" if b.trend else "")
                + "[/dim]"
            )


# --- refs: reference-repo folder manager ---

refs_app = typer.Typer(
    help="Manage the ~/code/refs folder of read-only reference repos. Bare `ko refs` = pull "
    "all clones (parallel, --ff-only). Also: setup (bootstrap the baked-in set), add <url>, list.",
    invoke_without_command=True,
)
app.add_typer(refs_app, name="refs")


def _refs_pull(yes: bool, jobs: int) -> None:
    import sys
    import textwrap

    from . import refs as refs_mod

    base = refs_mod.refs_dir()
    if not base.is_dir():
        _die(f"refs dir {base} does not exist — run `ko refs setup` first")
    repos, skipped = refs_mod.find_repos(base)
    if not repos:
        _no_results(f"no git repos in {base}", False)
    typer.echo(f"Found {len(repos)} git repos in {base}:", err=True)
    typer.echo(
        textwrap.fill(
            ", ".join(repos), width=80, initial_indent="  ",
            subsequent_indent="  ", break_on_hyphens=False,
        ),
        err=True,
    )
    # confirm only on a TTY — agents/pipes proceed (an --ff-only pull of read-only
    # clones is safe, and the agent contract bans interactive prompts)
    if not yes and sys.stdin.isatty():
        confirm = input("Proceed with update? [Y/n] ").strip().lower()
        if confirm and not confirm.startswith("y"):
            typer.echo("Aborted.", err=True)
            raise typer.Exit(0)
    results = []
    for res in refs_mod.pull_all(base, repos, jobs=jobs):
        if res.change:
            typer.echo(f"{res.repo}: {res.change}")  # stdout: what actually moved
        elif not res.ok:
            typer.echo(f"{res.repo}: FAILED", err=True)
            typer.echo(res.error, err=True)
        results.append(res)
    updated = sum(1 for r in results if r.change)
    failed = [r.repo for r in results if not r.ok]
    parts = [f"{updated} updated", f"{len(results) - updated - len(failed)} already up to date"]
    if failed:
        parts.append(f"{len(failed)} failed ({', '.join(failed)})")
    typer.echo(", ".join(parts), err=True)
    if skipped:
        typer.echo(f"Skipped {len(skipped)} non-git dir(s): {', '.join(skipped)}", err=True)
    if failed:
        raise typer.Exit(1)


@refs_app.callback()
def refs_main(
    ctx: typer.Context,
    yes: bool = typer.Option(False, "--yes", "-y", help="skip the confirm prompt (TTY only; pipes never prompt)"),
    jobs: int = typer.Option(8, "--jobs", "-j", help="max concurrent pulls"),
) -> None:
    """Bare `ko refs` = pull all reference repos (same as `ko refs pull`)."""
    if ctx.invoked_subcommand:
        return
    _refs_pull(yes, jobs)


@refs_app.command("pull")
def refs_pull(
    yes: bool = typer.Option(False, "--yes", "-y", help="skip the confirm prompt (TTY only; pipes never prompt)"),
    jobs: int = typer.Option(8, "--jobs", "-j", help="max concurrent pulls"),
) -> None:
    """Update all clones: parallel `git pull --ff-only --prune`; prints only repos whose
    HEAD actually moved (`repo: old -> new` via git describe). Exit 1 if any failed."""
    _refs_pull(yes, jobs)


@refs_app.command("setup")
def refs_setup() -> None:
    """Idempotent bootstrap: create the refs dir, write its CLAUDE.md (only if missing —
    it accumulates takeaways), clone every baked-in + refs.txt repo not already present."""
    from . import refs as refs_mod

    base = refs_mod.refs_dir()
    base.mkdir(parents=True, exist_ok=True)
    if refs_mod.write_claude_md(base):
        typer.echo(f"wrote {refs_mod.claude_md(base)}", err=True)
    urls = [url for _, url, _ in refs_mod.BAKED_REPOS] + refs_mod.extra_repos()
    cloned, present, failed = [], [], []
    for url in urls:
        name = refs_mod.repo_name(url)
        if (base / name).exists():
            present.append(name)
            continue
        res = refs_mod.clone(base, url)
        if res.ok:
            cloned.append(name)
            typer.echo(f"cloned {name}")
        else:
            failed.append(name)
            typer.echo(f"{name}: FAILED", err=True)
            typer.echo(res.error, err=True)
    parts = [f"{len(cloned)} cloned", f"{len(present)} already present"]
    if failed:
        parts.append(f"{len(failed)} failed ({', '.join(failed)})")
    typer.echo(", ".join(parts), err=True)
    if failed:
        raise typer.Exit(1)


@refs_app.command("add")
def refs_add(
    url: str = typer.Argument(..., help="git URL to clone, e.g. https://github.com/owner/repo"),
    note: str = typer.Option(
        "(no deep dive yet)", "--note", help="one-line note for the CLAUDE.md repo list"
    ),
) -> None:
    """Clone a repo into the refs dir, remember it in ~/.config/ko/refs.txt (so `setup`
    restores it on any machine), and stub an entry in the folder's CLAUDE.md."""
    from . import refs as refs_mod

    base = refs_mod.refs_dir()
    base.mkdir(parents=True, exist_ok=True)
    try:
        name = refs_mod.repo_name(url)
    except ValueError as e:
        _die(str(e), code="usage")
    if (base / name).exists():
        _die(f"{base / name} already exists")
    res = refs_mod.clone(base, url)
    if not res.ok:
        typer.echo(res.error, err=True)
        _die(f"clone failed for {url}")
    refs_mod.append_claude_entry(base, url, note)
    remembered = refs_mod.remember_extra(url)
    typer.echo(f"cloned {name}")
    typer.echo(
        f"[noted in CLAUDE.md{'; remembered in ' + str(refs_mod.extras_file()) if remembered else ''}]",
        err=True,
    )


@refs_app.command("list")
def refs_list(
    as_json: bool = typer.Option(False, "--json", help="emit JSON instead of TSV"),
) -> None:
    """All cloned refs (TSV: name, version via git describe, origin URL)."""
    from . import refs as refs_mod

    base = refs_mod.refs_dir()
    if not base.is_dir():
        _no_results(f"refs dir {base} does not exist — run `ko refs setup`", as_json)
    repos, _ = refs_mod.find_repos(base)
    if not repos:
        _no_results(f"no git repos in {base}", as_json)
    rows = [
        {"name": r, "version": refs_mod.describe(base, r), "origin": refs_mod.origin_url(base, r)}
        for r in repos
    ]
    if as_json:
        import json

        typer.echo(json.dumps(rows))
        return
    for row in rows:
        typer.echo(f"{row['name']}\t{row['version']}\t{row['origin']}")


@app.command("logs")
def logs_cmd(
    n: int = typer.Option(20, "--n", help="how many recent events"),
    errors: bool = typer.Option(False, "--errors", help="only error events"),
    as_json: bool = typer.Option(False, "--json", help="emit JSON"),
) -> None:
    """Recent ko command logs — local structured JSONL (one event per command, no args/secrets).
    On by default; disable with `[logs] enabled = false` in config.toml. A PostHog sync is later.
    """
    import json as _json

    from . import logs as logs_mod

    events = logs_mod.recent(n, errors_only=errors)
    if not events:
        _no_results("no logs yet", as_json)
    if as_json:
        typer.echo(_json.dumps(events, default=str))
        return
    for e in events:
        ts = e.get("ts", "")[:19].replace("T", " ")
        mark = "✗" if e.get("level") == "error" else " "
        dur = e.get("duration_ms")
        durs = f"{dur}ms" if dur is not None else ""
        err = f"  {e['error']}" if e.get("error") else ""
        cmd = e.get("cmd") or e.get("event", "")
        typer.echo(f"{mark} {ts}  {cmd:22} exit={e.get('exit_code', '?')}  {durs}{err}")


def _cmd_label(args: list[str]) -> str:
    """A privacy-safe command label for logging: the group/command + a *validated* subcommand only.
    A subcommand must be a registered command of the group, so an arg value (query, url, id) is
    never captured — `ko exa search rust` logs `exa search`, never `rust`."""
    toks = [a for a in args if not a.startswith("-")]
    if not toks:
        return "(root)"
    head = toks[0]
    if len(toks) > 1:
        grp = next((g for g in app.registered_groups if g.name == head), None)
        if grp is not None and toks[1] in {c.name for c in grp.typer_instance.registered_commands}:
            return f"{head} {toks[1]}"
    return head


def _route(args: list[str]) -> list[str]:
    """Deterministic bare-argument routing (explicit command names always win). Pure:
    takes the args after the program name, returns the rewritten list. Shortcuts:
    `help` trailing == `--help` at any level; `ko <url>` -> `fetch <url>`; `ko <file>` ->
    `doc <file>`; `ko x <name>` -> `x list <name>`; `ko tt <name>` -> `tt items <name>`;
    `ko publish [dir]` -> `publish up [dir]`."""
    args = list(args)
    # `help` as a trailing word == --help (`ko help`, `ko exa search help`). To *search* the
    # literal word "help", use the flag form instead — `ko exa search --help` is help.
    if args and args[-1] == "help" and "--help" not in args and "-h" not in args:
        args[-1] = "--help"
    if not args or args[0].startswith("-"):
        return args
    known = {g.name for g in app.registered_groups} | {
        c.name for c in app.registered_commands
    }
    if args[0].startswith(("http://", "https://")):
        args.insert(0, "fetch")
    elif args[0] not in known and Path(args[0]).is_file():
        args.insert(0, "doc")
    elif args[0] == "x" and len(args) > 1 and not args[1].startswith("-"):
        if args[1] not in {c.name for c in x_app.registered_commands}:
            args.insert(1, "list")
    elif args[0] == "tt" and len(args) > 1 and not args[1].startswith("-"):
        if args[1] not in {c.name for c in tt_app.registered_commands}:
            args.insert(1, "items")  # `ko tt Shopping` -> `ko tt items Shopping`
    elif args[0] == "publish":
        pub_known = {c.name for c in publish_app.registered_commands}
        # `ko publish` / `ko publish ./dir` / `ko publish -n foo` -> `... up ...`,
        # but leave subcommands and `--help` to the group.
        if not args[1:] or (args[1] not in pub_known and args[1] not in ("--help", "-h")):
            args.insert(1, "up")
    return args


def main() -> None:
    """Entry point with deterministic bare-argument shortcuts (see `_route`): `ko paper.pdf`
    routes to `ko doc`, `ko x ai` to `ko x list ai`, `ko <url>` to `ko fetch <url>`."""
    import os
    import sys
    import time

    sys.argv[1:] = _route(sys.argv[1:])
    if os.environ.get("_KO_COMPLETE"):  # shell-completion run — never log it
        app()
        return
    # One wide structured event per command (local JSONL; see logs.py). Best-effort.
    label = _cmd_label(sys.argv[1:])
    start = time.monotonic()
    code, err = 0, None
    from . import _cli_shared as _shared
    _shared._last_error = None  # reset per-command so a prior run's label doesn't bleed through
    try:
        app()
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else (1 if e.code else 0)
        # _die() raises typer.Exit (a SystemExit); pick up the error code label it recorded.
        if code not in (0, None) and err is None:
            err = _shared._last_error
        raise
    except BaseException as e:
        code, err = 1, type(e).__name__
        raise
    finally:
        from . import logs

        logs.command_event(label, int((time.monotonic() - start) * 1000), code, err)


if __name__ == "__main__":
    main()
