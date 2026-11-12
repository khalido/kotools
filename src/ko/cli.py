"""ko — CLI entry point."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import typer

from . import (  # noqa: F401  — triggers sub-app self-registration
    cli_ai,
    cli_doctor,
    cli_google,
    cli_web,
)
from ._cli_shared import (  # noqa: F401 — re-exported for tests
    _die,
    _die_from,
    _fmt_day,
    _no_results,
    _reject_group_flags,
    _tsv_cell,
    app,
)
from .cli_ai import publish_app, tt_app  # needed by main()'s bare-arg dispatcher

# Re-export group-local helpers that tests import via ko.cli
from .cli_google import _norm_block, _parse_cells, _short_from  # noqa: F401
from .cli_web import x_app, yt_app  # needed by main()'s bare-arg dispatcher
from .refs import DEFAULT_JOBS as REFS_JOBS


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
    import os

    from . import config, telemetry

    # pydantic-ai ≥2.51 prints an ASCII-art Logfire ad before the first agent run; stderr is
    # for notes an agent can act on, so ko opts out (a real env var still wins).
    os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")
    # google-genai (≥2.25) warns once per process about "automatic function calling" on every
    # pydantic-ai Gemini call — advice to a library author, not to whoever ran `ko llm`.
    import logging

    logging.getLogger("google_genai.models").setLevel(logging.ERROR)
    config.load_keys_into_env()
    telemetry.setup()


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
            status = refs_mod.http_status(res.error)
            if status:  # one line; the transport error is identical across repos
                typer.echo(f"{res.repo}: FAILED (HTTP {status})", err=True)
            else:
                typer.echo(f"{res.repo}: FAILED", err=True)
                typer.echo(res.error, err=True)
        results.append(res)
    updated = sum(1 for r in results if r.change)
    failed = [r.repo for r in results if not r.ok]
    parts = [f"{updated} updated", f"{len(results) - updated - len(failed)} already up to date"]
    if failed:
        parts.append(f"{len(failed)} failed ({', '.join(failed)})")
    typer.echo(", ".join(parts), err=True)
    forbidden = [r for r in results if not r.ok and refs_mod.http_status(r.error) == 403]
    if forbidden:
        typer.echo(f"{len(forbidden)} repo(s) got 403. {refs_mod.GITHUB_403_HINT}", err=True)
    if skipped:
        typer.echo(f"Skipped {len(skipped)} non-git dir(s): {', '.join(skipped)}", err=True)
    if failed:
        raise typer.Exit(1)


@refs_app.callback()
def refs_main(
    ctx: typer.Context,
    yes: bool = typer.Option(False, "--yes", "-y", help="skip the confirm prompt (TTY only; pipes never prompt)"),
    jobs: int = typer.Option(REFS_JOBS, "--jobs", "-j", help="max concurrent pulls"),
) -> None:
    """Bare `ko refs` = pull all reference repos (same as `ko refs pull`)."""
    if ctx.invoked_subcommand:
        _reject_group_flags(ctx, yes="--yes", jobs="--jobs")
        return
    _refs_pull(yes, jobs)


@refs_app.command("pull")
def refs_pull(
    yes: bool = typer.Option(False, "--yes", "-y", help="skip the confirm prompt (TTY only; pipes never prompt)"),
    jobs: int = typer.Option(REFS_JOBS, "--jobs", "-j", help="max concurrent pulls"),
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
    n: int = typer.Option(20, "--n", "-n", help="how many recent events"),
    errors: bool = typer.Option(False, "--errors", help="only failed commands (warnings + errors)"),
    as_json: bool = typer.Option(False, "--json", help="emit JSON: every field each event carried"),
) -> None:
    """Recent ko commands from the local log: what ran, who ran it, cost, and why it failed.
    ✗ = error (something broke), ! = warning (a fix the user can make). Off: `\\[logs] enabled = false` in config.toml.

    --json fields: ts, level, cmd, argv, exit_code, duration_ms, error, error_msg, model, cost_usd, …
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
        mark = {"error": "✗", "warning": "!"}.get(e.get("level"), " ")
        what = e.get("argv") or e.get("cmd") or e.get("event", "")
        what = what if len(what) <= 48 else what[:47] + "…"
        dur = e.get("duration_ms")
        parts = [f"exit={e.get('exit_code', '?')}", f"{dur}ms" if dur is not None else ""]
        if (usd := e.get("cost_usd")) is not None:
            cost = "free" if e.get("cost_source") == "free" else f"${usd:.4f}" if usd >= 0.0001 else "<$0.0001"
            parts.append(cost + f" {e.get('model', '')}".rstrip())
        if e.get("caller"):
            parts.append(str(e["caller"]).split("_")[0])
        if e.get("error"):
            msg = e.get("error_msg") or ""
            msg = msg if len(msg) <= 80 else msg[:79] + "…"  # full text in --json
            parts.append(f"{e['error']}: {msg}" if msg else e["error"])
        typer.echo(f"{mark} {ts}  {what:48}  " + "  ".join(p for p in parts if p))


def _cmd_label(args: list[str]) -> str:
    """The command label for logging and grouping: the group/command + a *validated* subcommand
    only, so an arg value never becomes the label — `ko exa search rust` is `exa search`. (The
    args themselves go to the log's `argv` field.)"""
    toks = [a for a in args if not a.startswith("-")]
    if not toks:
        return "(root)"
    head = toks[0]
    if len(toks) > 1:
        grp = next((g for g in app.registered_groups if g.name == head), None)
        if grp is not None and toks[1] in {c.name for c in grp.typer_instance.registered_commands}:
            return f"{head} {toks[1]}"
    return head


@lru_cache(maxsize=1)
def _group_paths() -> frozenset[tuple[str, ...]]:
    """Every arg path that names a Typer *group* rather than a leaf command, nested groups
    included: ('exa',), ('gmail',), ('gmail','draft'), ('agent','sessions')… plus the empty
    path for the root, so bare `ko help` resolves too."""
    paths: set[tuple[str, ...]] = {()}

    def walk(t: typer.Typer, prefix: tuple[str, ...]) -> None:
        for g in t.registered_groups:
            path = (*prefix, g.name)
            paths.add(path)
            walk(g.typer_instance, path)

    walk(app, ())
    return frozenset(paths)


def _route(args: list[str]) -> list[str]:
    """Deterministic bare-argument routing (explicit command names always win). Pure:
    takes the args after the program name, returns the rewritten list. Shortcuts:
    trailing `help` == `--help` at group level only; `ko <url>` -> `fetch <url>`;
    `ko <file>` -> `doc <file>`; `ko x <name>` -> `x list <name>`; `ko tt <name>` ->
    `tt items <name>`; `ko yt <url>` -> `yt transcript <url>`; `ko publish [dir]` -> `publish up [dir]`."""
    args = list(args)
    groups = {g.name for g in app.registered_groups}
    # `help` as a trailing word == --help, but only at *group* level (`ko help`, `ko exa
    # help`, `ko gmail draft help`). After a leaf command it stays an ordinary argument, so
    # `ko hn search help` searches for the word "help" — previously that was unreachable at
    # any spelling, since the rewrite happened before Typer ever saw the query. `--help`
    # still works everywhere. Matching the *whole* prefix against real group paths (not just
    # `args[-2]` against top-level names) is what keeps nested groups working and stops
    # `ko hn search agent help` from being swallowed because "agent" happens to be a group.
    if (
        args
        and args[-1] == "help"
        and "--help" not in args
        and "-h" not in args
        and tuple(args[:-1]) in _group_paths()
    ):
        args[-1] = "--help"
    if not args or args[0].startswith("-"):
        return args
    known = groups | {c.name for c in app.registered_commands}
    if args[0].startswith(("http://", "https://")):
        args.insert(0, "fetch")
    elif args[0] not in known and Path(args[0]).is_file():
        args.insert(0, "doc")
    elif args[0] == "x" and len(args) > 1 and not args[1].startswith("-"):
        if args[1] not in {c.name for c in x_app.registered_commands}:
            args.insert(1, "list")
    elif args[0] in ("tt", "todo"):
        tt_known = {c.name for c in tt_app.registered_commands}
        if not args[1:]:
            args.insert(1, "lists")  # bare `ko tt` -> your lists (house style: do the useful thing)
        elif not args[1].startswith("-") and args[1] not in tt_known:
            args.insert(1, "items")  # `ko tt Shopping` -> `ko tt items Shopping`
    elif args[0] == "yt" and args[1:] and args[1] not in ("--help", "-h"):
        # `ko yt <url> [-s]` -> `ko yt transcript <url> [-s]`; subcommands pass through
        if args[1] not in {c.name for c in yt_app.registered_commands}:
            args.insert(1, "transcript")
    elif args[0] == "publish":
        pub_known = {c.name for c in publish_app.registered_commands}
        # `ko publish` / `ko publish ./dir` / `ko publish -n foo` -> `... up ...`,
        # but leave subcommands and `--help` to the group.
        if not args[1:] or (args[1] not in pub_known and args[1] not in ("--help", "-h")):
            args.insert(1, "up")
    return args


# _die() codes that mean "the user can fix this" — logged as warnings, not errors, so
# `ko logs --errors` level=error is what broke: crashes, upstream and network failures.
_USER_ERRORS = frozenset({"usage", "auth", "config", "not_found"})


def _crash_site(e: BaseException) -> str | None:
    """`file.py:line in func` of the innermost frame inside ko's own code, for a crash's log."""
    import traceback

    try:  # runs mid-crash: must never replace the real exception with its own
        ko_dir = Path(__file__).parent
        frames = [f for f in traceback.extract_tb(e.__traceback__) if Path(f.filename).is_relative_to(ko_dir)]
        if not frames:
            return None
        f = frames[-1]
        return f"{Path(f.filename).relative_to(ko_dir)}:{f.lineno} in {f.name}"
    except Exception:
        return None


def main() -> None:
    """Entry point with deterministic bare-argument shortcuts (see `_route`): `ko paper.pdf`
    routes to `ko doc`, `ko x ai` to `ko x list ai`, `ko <url>` to `ko fetch <url>`."""
    import os
    import sys
    import time

    from . import config, google_auth

    sys.argv[1:] = _route(sys.argv[1:])
    if os.environ.get("_KO_COMPLETE"):  # shell-completion run — never log it
        app()
        return
    # One wide event per command (local JSONL; see logs.py): code anywhere adds fields with
    # logs.add(), this writes them once at the end. Best-effort.
    import shlex

    from . import logs

    logs.reset()
    logs.add(argv=shlex.join(sys.argv[1:]), **logs.caller())
    label = _cmd_label(sys.argv[1:])
    start = time.monotonic()
    code, level = 0, None
    try:
        app()
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else (1 if e.code else 0)
        if logs.get("error") in _USER_ERRORS:  # set by _die(): the user can fix it, nothing broke
            level = "warning"
        raise
    except (google_auth.AuthError, config.ConfigError, config.MissingKeyError) as e:
        # Failures any command can hit before its own error net: no usable Google token, an
        # unreadable config file, an unset key. One line naming the fix, never a traceback
        # (agents read stderr, not frames).
        code, level = 1, "warning"
        try:
            _die_from(e, as_json="--json" in sys.argv)
        except typer.Exit as x:  # outside app() nothing turns typer.Exit into an exit code
            raise SystemExit(x.exit_code) from None
    except BaseException as e:
        code = 1
        logs.add(error=type(e).__name__, error_msg=str(e) or None, error_at=_crash_site(e))
        raise
    finally:
        logs.command_event(label, int((time.monotonic() - start) * 1000), code, level)


if __name__ == "__main__":
    main()
