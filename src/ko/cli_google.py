"""Google cluster CLI commands: gsheets, gdocs, cal, gmail."""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import asdict
from pathlib import Path

import typer

from . import gcal as gcal_mod
from . import gdocs as gdocs_mod
from . import gdrive as gdrive_mod
from . import gmail as gmail_mod
from . import google_auth
from . import gsheets as gsheets_mod
from ._cli_shared import _die, _no_results, app

# --- sub-apps ---

gsheets_app = typer.Typer(
    help=(
        "Read & write Google Sheets. OAuth to your Google account on first run "
        "(`ko gsheets auth` grants read+write). Reads: info / tabs / get / find. "
        "Writes (refuse to clobber non-empty cells unless --overwrite): set / put / "
        "header / add-tab / new / clear."
    ),
    no_args_is_help=True,
)
app.add_typer(gsheets_app, name="gsheets")
app.add_typer(gsheets_app, name="gs", hidden=True)  # shortcut alias

gdocs_app = typer.Typer(
    help=(
        "Read & write Google Docs as Markdown (same OAuth token as `ko gsheets`). "
        "Read: get (Doc→Markdown) / info / comments. Write: push (Markdown→new or existing Doc) / "
        "replace / append / reply / shade-table."
    ),
    no_args_is_help=True,
)
app.add_typer(gdocs_app, name="gdocs")
app.add_typer(gdocs_app, name="gd", hidden=True)  # shortcut alias

cal_app = typer.Typer(
    help=(
        "Google Calendar: agenda + quick-add (same OAuth token as `ko gsheets`). "
        "`ko cal` shows the next 7 days; also `cal day`, `cal find`, `cal add`, `cal cals`."
    ),
)
app.add_typer(cal_app, name="cal")

gmail_app = typer.Typer(
    help=(
        "Read Gmail (read-only; same OAuth token as `ko gsheets`). Bare `ko gmail` = recent "
        "inbox; also `search` (Gmail query syntax), `from <who>`, `view <id>`, `thread <id>`."
    ),
)
app.add_typer(gmail_app, name="gmail")


# --- google auth helpers (shared across gsheets / gdocs / cal — one token per account) ---


def _set_account(account: str | None) -> None:
    if account:
        os.environ["KO_GOOGLE_ACCOUNT"] = account


def _google_auth(out: bool, readonly: bool) -> None:
    acct = google_auth.active_account()
    if out:
        removed = google_auth.logout()
        typer.echo(f"Signed out of '{acct}'." if removed else f"No cached token for '{acct}'.")
        return
    google_auth.get_credentials(readonly=readonly)
    scope = "read-only" if readonly else "read+write"
    typer.echo(
        f"Signed in as '{acct}' ({scope}). One token covers Sheets + Docs + Calendar. "
        f"Cached at {google_auth.token_file()}."
    )


def _google_accounts() -> None:
    active = google_auth.active_account()
    authed = google_auth.list_accounts()
    for name in sorted(set(authed) | {active}):
        mark = "*" if name == active else " "
        note = "" if name in authed else f"  (no token — `ko gsheets -a {name} auth`)"
        typer.echo(f"{mark} {name}{note}")


# --- gsheets helpers ---


def _emit_rows(rows: list[list], as_json: bool) -> None:
    if as_json:
        typer.echo(json.dumps(rows, default=str))
        return
    # Default: TSV — survives commas, pipes into cut/awk/mlr cleanly
    for row in rows:
        typer.echo("\t".join("" if c is None else str(c) for c in row))


def _parse_cells(text: str) -> list[list]:
    """CLI write data -> 2D values. A JSON array (1D -> one row, 2D -> as-is) if it
    parses; else TSV (newlines = rows, tabs = cells); a bare scalar -> a single cell."""
    text = text.strip()
    if text[:1] in ("[", "{"):  # tuple, not substring — "" in "[{" is True
        data = json.loads(text)
        if isinstance(data, list):
            if data and all(isinstance(r, list) for r in data):
                return data
            return [data]  # 1D list -> a single row
        return [[data]]
    if "\t" in text or "\n" in text:
        return [line.split("\t") for line in text.splitlines()]  # drops trailing-newline row
    return [[text]]


def _norm_block(val) -> list[list]:
    """A `ko gsheets put` value (scalar | row list | 2D list) -> 2D values."""
    if isinstance(val, list):
        if val and all(isinstance(r, list) for r in val):
            return val
        return [val]  # 1D list -> a single row
    return [[val]]


# --- gsheets commands ---


@gsheets_app.callback()
def _gsheets_account(
    account: str = typer.Option(
        None, "--account", "-a",
        help="Google account to use (else KO_GOOGLE_ACCOUNT / [google] account / 'default')",
    ),
) -> None:
    """Pick the Google account for this command (one token covers Sheets/Docs/Calendar)."""
    _set_account(account)


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
    as_json: bool = typer.Option(False, "--json", help="emit a JSON array of tab names"),
) -> None:
    """List tab names, one per line (machine-friendly)."""
    try:
        info = gsheets_mod.get_info(spreadsheet_id)
    except gsheets_mod.SheetsError as e:
        _die(str(e), as_json=as_json)
    if as_json:
        typer.echo(json.dumps(info.tabs))
        return
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


@gsheets_app.command("set")
def gsheets_set(
    spreadsheet_id: str = typer.Argument(..., help="Google Sheet ID or URL"),
    range_name: str = typer.Argument(..., help="target anchor, e.g. 'Tab!A2' (top-left cell)"),
    data: str = typer.Argument(
        None, help="value, TSV, or JSON 2D array (omit to read stdin)"
    ),
    raw: bool = typer.Option(
        False, "--raw", help="write literally (default parses formulas/dates like typing)"
    ),
    overwrite: bool = typer.Option(False, "--overwrite", help="replace occupied cells"),
) -> None:
    """Write to a sheet from a top-left anchor; the range comes from the data shape.
    Refuses to clobber non-empty cells unless --overwrite (the error lists them)."""
    text = data if data is not None else sys.stdin.read()
    anchor = range_name.split(":")[0]
    try:
        n = gsheets_mod.write_values(
            gsheets_mod.sheet_id(spreadsheet_id),
            anchor,
            _parse_cells(text),
            raw=raw,
            overwrite=overwrite,
        )
    except (gsheets_mod.SheetsError, ValueError, json.JSONDecodeError) as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from None
    typer.echo(f"{n} cell(s) updated", err=True)


@gsheets_app.command("put")
def gsheets_put(
    spreadsheet_id: str = typer.Argument(..., help="Google Sheet ID or URL"),
    json_file: Path = typer.Argument(
        None, help="JSON {anchor: value} file (omit to read stdin)"
    ),
    raw: bool = typer.Option(
        False, "--raw", help="write literally (default parses like typing)"
    ),
    overwrite: bool = typer.Option(False, "--overwrite", help="replace occupied cells"),
) -> None:
    """Bulk-write many ranges in ONE API call. JSON maps each top-left anchor to a value
    (scalar | row list | 2D list): {"Tab!A1": [["h1","h2"],[1,2]], "Tab!E1": "note"}.
    Same overwrite guard as `set`, checked across every target."""
    raw_json = json_file.read_text() if json_file else sys.stdin.read()
    try:
        obj = json.loads(raw_json)
        if not isinstance(obj, dict):
            raise ValueError('`put` expects a JSON object: {"anchor": value, ...}')
        data = [(anchor, _norm_block(val)) for anchor, val in obj.items()]
        n = gsheets_mod.write_ranges(
            gsheets_mod.sheet_id(spreadsheet_id), data, raw=raw, overwrite=overwrite
        )
    except (gsheets_mod.SheetsError, ValueError, json.JSONDecodeError) as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from None
    typer.echo(f"{n} cell(s) updated", err=True)


@gsheets_app.command("find")
def gsheets_find(
    spreadsheet_id: str = typer.Argument(..., help="Google Sheet ID or URL"),
    text: str = typer.Argument(..., help="case-insensitive substring to find"),
    formula: bool = typer.Option(
        False, "--formula", help="search formulas, not displayed values"
    ),
    tab: list[str] = typer.Option(None, "--tab", help="limit to these tab(s); repeatable"),
    as_json: bool = typer.Option(False, "--json", help="emit JSON array of {tab, ref, cell}"),
) -> None:
    """Search every cell of every tab. TSV: tab, A1 ref, cell."""
    try:
        hits = gsheets_mod.find_cells(
            gsheets_mod.sheet_id(spreadsheet_id), text, formula=formula, tabs=tab or None
        )
    except gsheets_mod.SheetsError as e:
        _die(str(e), as_json=as_json)
    if not hits:
        _no_results("no matches", as_json)
    if as_json:
        typer.echo(json.dumps([{"tab": t, "ref": ref, "cell": cell} for t, ref, cell in hits]))
        return
    for t, ref, cell in hits:
        typer.echo(f"{t}\t{ref}\t{cell}")


@gsheets_app.command("header")
def gsheets_header(
    spreadsheet_id: str = typer.Argument(..., help="Google Sheet ID or URL"),
    range_name: str = typer.Argument(..., help="header range, e.g. 'Tab!A3:T3'"),
    no_bold: bool = typer.Option(False, "--no-bold", help="don't bold"),
    no_fill: bool = typer.Option(False, "--no-fill", help="don't add a background fill"),
    freeze: bool = typer.Option(
        False, "--freeze", help="freeze rows up to and including this one (needs a bounded range)"
    ),
) -> None:
    """Format a row as a header: bold + fill, optionally frozen."""
    try:
        gsheets_mod.format_header(
            gsheets_mod.sheet_id(spreadsheet_id),
            range_name,
            bold=not no_bold,
            bg=None if no_fill else gsheets_mod.DEFAULT_HEADER_BG,
            freeze=freeze,
        )
    except (gsheets_mod.SheetsError, ValueError) as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from None
    typer.echo("header formatted", err=True)


@gsheets_app.command("add-tab")
def gsheets_add_tab(
    spreadsheet_id: str = typer.Argument(..., help="Google Sheet ID or URL"),
    title: str = typer.Argument(..., help="new tab name"),
) -> None:
    """Add a tab; prints its sheetId (gid)."""
    try:
        gid = gsheets_mod.add_tab(gsheets_mod.sheet_id(spreadsheet_id), title)
    except gsheets_mod.SheetsError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from None
    typer.echo(str(gid))


@gsheets_app.command("new")
def gsheets_new(
    title: str = typer.Argument(..., help="title for the new spreadsheet"),
) -> None:
    """Create a new spreadsheet; prints its ID (stdout) and URL (stderr)."""
    try:
        new_id = gsheets_mod.create_spreadsheet(title)
    except gsheets_mod.SheetsError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from None
    typer.echo(new_id)
    typer.echo(f"https://docs.google.com/spreadsheets/d/{new_id}/edit", err=True)


@gsheets_app.command("clear")
def gsheets_clear(
    spreadsheet_id: str = typer.Argument(..., help="Google Sheet ID or URL"),
    range_name: str = typer.Argument(..., help="range to clear (values only; formatting survives)"),
) -> None:
    """Clear the values in a range. Formatting, notes, and validation survive."""
    try:
        cleared = gsheets_mod.clear_range(gsheets_mod.sheet_id(spreadsheet_id), range_name)
    except gsheets_mod.SheetsError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from None
    typer.echo(f"cleared {cleared}", err=True)


@gsheets_app.command("auth")
def gsheets_auth(
    out: bool = typer.Option(
        False, "--logout", help="remove the cached token and exit"
    ),
    readonly: bool = typer.Option(
        False, "--readonly", help="grant read-only scope (default grants read+write)"
    ),
) -> None:
    """Trigger or reset Google OAuth for the active account (`-a <name>` to pick). Opens a
    browser on first run. Default grants read+write (covers Sheets, Docs, and Calendar) so every
    command works. Authed read-only or a narrower scope before? `--logout` then re-run to upgrade."""
    _google_auth(out, readonly)


@gsheets_app.command("accounts")
def gsheets_accounts() -> None:
    """List Google accounts with a cached token; `*` marks the active one."""
    _google_accounts()


# --- gdocs commands ---


@gdocs_app.callback()
def _gdocs_account(
    account: str = typer.Option(
        None, "--account", "-a",
        help="Google account to use (else KO_GOOGLE_ACCOUNT / [google] account / 'default')",
    ),
) -> None:
    """Pick the Google account for this command (one token covers Sheets/Docs/Calendar)."""
    _set_account(account)


@gdocs_app.command("get")
def gdocs_get(
    doc: str = typer.Argument(..., help="Google Doc ID or URL"),
    out: Path = typer.Option(None, "--out", "-o", help="write to a file instead of stdout"),
) -> None:
    """Read a Doc as Markdown (tables included) — pipe it to an LLM/agent, or save with -o."""
    try:
        md = gdrive_mod.export_markdown(doc)
    except gdrive_mod.DriveError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from None
    if out:
        out.write_text(md)
        typer.echo(f"wrote {len(md)} char(s) to {out}", err=True)
    else:
        typer.echo(md)


@gdocs_app.command("info")
def gdocs_info(doc: str = typer.Argument(..., help="Google Doc ID or URL")) -> None:
    """Show a doc's title (stdout) and id (stderr)."""
    try:
        info = gdocs_mod.get_info(doc)
    except gdocs_mod.DocsError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from None
    typer.echo(info.title)
    typer.echo(info.id, err=True)


@gdocs_app.command("append")
def gdocs_append(
    doc: str = typer.Argument(..., help="Google Doc ID or URL"),
    text: str = typer.Argument(None, help="text to append (omit to read stdin)"),
) -> None:
    """Append text to the end of a doc."""
    body = text if text is not None else sys.stdin.read()
    try:
        n = gdocs_mod.append_text(doc, body)
    except gdocs_mod.DocsError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from None
    typer.echo(f"appended {n} char(s)", err=True)


@gdocs_app.command("replace")
def gdocs_replace(
    doc: str = typer.Argument(..., help="Google Doc ID or URL"),
    find: str = typer.Argument(..., help="text to find"),
    replace: str = typer.Argument(..., help="replacement text"),
    match_case: bool = typer.Option(False, "--match-case", help="case-sensitive match"),
) -> None:
    """Replace every occurrence of FIND with REPLACE across the doc."""
    try:
        n = gdocs_mod.replace_text(doc, find, replace, match_case=match_case)
    except gdocs_mod.DocsError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from None
    typer.echo(f"replaced {n} occurrence(s)", err=True)


_DRIVE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{20,}$")


def _resolve_folder(folder: str | None) -> str | None:
    """A `--folder` value → a folder ID. A `/folders/` URL or a bare ID passes through; anything
    else is treated as a name and find-or-created (so `--folder Proposals` just works)."""
    if not folder:
        return None
    if "/folders/" in folder or _DRIVE_ID_RE.match(folder):
        return folder
    return gdrive_mod.ensure_folder(folder)


def _ask_tty(prompt: str) -> bool:
    """Yes/no from the controlling terminal — works even when stdin is piped Markdown. No TTY → False
    (so non-interactive callers must pass --force)."""
    try:
        with open("/dev/tty", "r+") as tty:
            tty.write(prompt)
            tty.flush()
            return tty.readline().strip().lower() in ("y", "yes")
    except OSError:
        typer.echo("no TTY for confirmation — pass --force to overwrite", err=True)
        return False


def _confirm_overwrite(target: str, new_md: str, force: bool) -> None:
    """Guard an in-place doc overwrite: pull the current doc, show its title + a diff vs the new
    Markdown, and confirm — unless --force. Aborts if declined or there's no TTY. The title is the
    real wrong-id catch (you notice 'Overwrite "Budget 2026"?' when you meant the proposal)."""
    try:
        current = gdrive_mod.export_markdown(target)
        title = gdocs_mod.get_info(target).title
    except gdrive_mod.DriveError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from None
    summary, diff = gdrive_mod.diff_summary(current, new_md)
    typer.echo(f'Overwrite "{title}" ({gdocs_mod.doc_id(target)[:8]}…): {summary}', err=True)
    if force:
        return
    for ln in diff[:80]:
        typer.echo(ln, err=True)
    if len(diff) > 80:
        typer.echo(f"… (+{len(diff) - 80} more diff lines)", err=True)
    if not _ask_tty("Overwrite? [y/N] "):
        _die("aborted — not overwriting (pass --force to skip this check)", code="aborted")


@gdocs_app.command("push")
def gdocs_push(
    markdown_file: Path = typer.Argument(..., help="Markdown file, or '-' for stdin"),
    target: str = typer.Argument(
        None, help="existing doc id or URL to update in place; omit to create a new doc"
    ),
    title: str = typer.Option(
        None, "--title", "-t", help="title for a NEW doc (default: file stem; ignored on update)"
    ),
    folder: str = typer.Option(
        None, "--folder", "-f", help="folder for a NEW doc: id, /folders/ URL, or a name to find-or-create"
    ),
    force: bool = typer.Option(False, "--force", help="skip the overwrite diff/confirm when updating"),
) -> None:
    """Markdown → Google Doc; prints ID (stdout) and URL (stderr). No TARGET creates a new doc; a
    TARGET (id or URL) updates that doc in place (same URL/sharing) after showing a diff to confirm
    (--force skips). An update re-imports the whole body, so re-apply shading with `shade-table`."""
    md = sys.stdin.read() if str(markdown_file) == "-" else markdown_file.read_text()
    if target:
        _confirm_overwrite(target, md, force)
        try:
            doc = gdrive_mod.push_markdown(md, "", update_id=target)
        except gdrive_mod.DriveError as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(1) from None
        typer.echo(doc)
        typer.echo(f"updated: https://docs.google.com/document/d/{doc}/edit", err=True)
        return
    doc_title = title or (markdown_file.stem if str(markdown_file) != "-" else "Untitled")
    try:
        new_id = gdrive_mod.push_markdown(md, doc_title, folder_id=_resolve_folder(folder))
    except gdrive_mod.DriveError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from None
    typer.echo(new_id)
    typer.echo(f"created: https://docs.google.com/document/d/{new_id}/edit", err=True)


@gdocs_app.command("comments")
def gdocs_comments(
    doc: str = typer.Argument(..., help="Google Doc ID or URL"),
    show_all: bool = typer.Option(False, "--all", help="include resolved comments"),
    as_json: bool = typer.Option(False, "--json", help="emit JSON instead of pretty text"),
) -> None:
    """Read review comments + replies off a Doc. Resolved are hidden unless --all."""
    try:
        comments = gdrive_mod.list_comments(doc, include_resolved=show_all)
    except gdrive_mod.DriveError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from None
    if as_json:
        typer.echo(json.dumps(comments, default=str))
        return
    if not comments:
        _no_results("no comments", False)
    for c in comments:
        flag = " (resolved)" if c["resolved"] else ""
        head = f"{c['author']} · {c['created_time']}{flag}  [{c['id']}]"
        typer.echo(head)
        if c["quoted_text"]:
            typer.echo(f"  > {c['quoted_text']}")
        typer.echo(f"  {c['content']}")
        for r in c["replies"]:
            typer.echo(f"    ↳ {r['author']} · {r['created_time']}: {r['content']}")
        typer.echo("")


@gdocs_app.command("reply")
def gdocs_reply(
    doc: str = typer.Argument(..., help="Google Doc ID or URL"),
    comment_id: str = typer.Argument(..., help="comment ID (the [bracketed] id from `ko gdocs comments`)"),
    text: str = typer.Argument(None, help="reply text (omit to read stdin)"),
) -> None:
    """Reply to a comment thread on a Doc (needs the read+write grant)."""
    body = text if text is not None else sys.stdin.read()
    try:
        r = gdrive_mod.reply_comment(doc, comment_id, body)
    except gdrive_mod.DriveError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from None
    typer.echo(f"replied to {comment_id} ({r['id']})", err=True)


@gdocs_app.command("shade-table")
def gdocs_shade_table(
    doc: str = typer.Argument(..., help="Google Doc ID or URL"),
    rows: str = typer.Option(None, "--rows", help="comma-sep 0-based rows to shade (-1=last), e.g. 0 = header"),
    cols: str = typer.Option(None, "--cols", help="comma-sep 0-based columns to shade (-1=last)"),
    color: str = typer.Option("#EFEFEF", "--color", help="hex background (default a light grey)"),
    table: int = typer.Option(0, "--table", help="which table if the doc has several (0 = first)"),
    all_tables: bool = typer.Option(
        False, "--all-tables", help="apply to every table in the doc (e.g. shade all headers at once)"
    ),
) -> None:
    """Background-shade table rows/columns — the bit Markdown can't do. Defaults to the header row
    (row 0); `--cols -1` adds a totals column. `--all-tables` does every table at once (the common
    'shade all headers' shortcut). Re-run after a `push` (a fresh push has no shading)."""
    def _parse(s: str | None) -> list[int] | None:
        if not s:
            return None
        try:
            return [int(x) for x in s.split(",")]
        except ValueError:
            _die(f"bad index list {s!r} — want comma-separated integers like '0,-1'")

    rows_l, cols_l = _parse(rows), _parse(cols)
    if rows_l is None and cols_l is None:
        rows_l = [0]  # convenience default: shade the header row
    try:
        n = gdocs_mod.shade_table(
            doc, rows=rows_l, cols=cols_l, color=color, table_index=(None if all_tables else table)
        )
    except gdocs_mod.DocsError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from None
    typer.echo(f"shaded {n} range(s)", err=True)


@gdocs_app.command("auth")
def gdocs_auth(
    out: bool = typer.Option(False, "--logout", help="remove the cached token and exit"),
    readonly: bool = typer.Option(False, "--readonly", help="grant read-only scope"),
) -> None:
    """Google OAuth (shared token with gsheets/cal). Default grants read+write."""
    _google_auth(out, readonly)


@gdocs_app.command("accounts")
def gdocs_accounts() -> None:
    """List Google accounts with a cached token; `*` marks the active one."""
    _google_accounts()


# --- cal helpers ---


def _fmt_events(events) -> None:
    """Print events grouped by day, human-readable, in the local zone."""
    from datetime import datetime

    zone = gcal_mod.tz()
    last_day = None
    for ev in events:
        if ev.all_day:
            day, when = ev.start, "all day"
        else:
            dt = datetime.fromisoformat(ev.start).astimezone(zone)
            day, when = dt.date().isoformat(), dt.strftime("%H:%M")
        if day != last_day:
            header = datetime.strptime(day, "%Y-%m-%d").strftime("%a %d %b")
            typer.echo(f"\n{header}")
            last_day = day
        loc = f"  @ {ev.location}" if ev.location else ""
        typer.echo(f"  {when:>7}  {ev.summary}  [{ev.calendar_name}]{loc}")


def _emit_events(events, as_json: bool) -> bool:
    """JSON or grouped text. Returns False if there were no events (and not JSON)."""
    if as_json:
        typer.echo(json.dumps([asdict(e) for e in events], default=str))
        return True
    if not events:
        return False
    _fmt_events(events)
    return True


# --- cal commands ---


@cal_app.callback(invoke_without_command=True)
def _cal_main(
    ctx: typer.Context,
    account: str = typer.Option(
        None, "--account", "-a",
        help="Google account to use (else KO_GOOGLE_ACCOUNT / [google] account / 'default')",
    ),
    days: int = typer.Option(7, "--days", "-d", help="days of agenda (default 7)"),
    today: bool = typer.Option(False, "--today", help="just today"),
    calendar: list[str] = typer.Option(
        None, "--calendar", "-c", help="limit to these calendar(s) by name or id; repeatable"
    ),
    as_json: bool = typer.Option(False, "--json", help="emit JSON"),
) -> None:
    """Agenda across your calendars (bare `ko cal`). Subcommands: day / add / cals / auth."""
    _set_account(account)
    if ctx.invoked_subcommand is not None:
        return
    n = 1 if today else days
    try:
        ids = gcal_mod.resolve_calendar_ids(calendar) if calendar else None
        events = gcal_mod.list_events(days=n, calendar_ids=ids)
    except gcal_mod.CalError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from None
    if not _emit_events(events, as_json):
        typer.echo(f"nothing in the next {n} day(s)", err=True)


@cal_app.command("day")
def cal_day(
    date_str: str = typer.Argument("today", help="YYYY-MM-DD, 'today', or 'tomorrow'"),
    calendar: list[str] = typer.Option(
        None, "--calendar", "-c", help="limit to these calendar(s) by name or id; repeatable"
    ),
    as_json: bool = typer.Option(False, "--json", help="emit JSON"),
) -> None:
    """All events for a single day."""
    from datetime import datetime, timedelta

    try:
        _, val = gcal_mod.parse_when(date_str)
        d = val.date() if isinstance(val, datetime) else val
        start = datetime(d.year, d.month, d.day, tzinfo=gcal_mod.tz())
        ids = gcal_mod.resolve_calendar_ids(calendar) if calendar else None
        events = gcal_mod.list_events(
            time_min=start, time_max=start + timedelta(days=1), calendar_ids=ids
        )
    except (gcal_mod.CalError, ValueError) as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from None
    if not _emit_events(events, as_json):
        typer.echo(f"nothing on {date_str}", err=True)


@cal_app.command("add")
def cal_add(
    title: str = typer.Argument(..., help="event title"),
    when: str = typer.Argument(
        ..., help="'YYYY-MM-DDTHH:MM' (timed) or 'YYYY-MM-DD'/'today'/'tomorrow' (all-day)"
    ),
    end: str = typer.Option(None, "--end", help="end time/date (timed default: +--minutes)"),
    minutes: int = typer.Option(60, "--minutes", "-m", help="duration for a timed event with no --end"),
    cal: str = typer.Option("primary", "--cal", help="calendar id (default: primary)"),
) -> None:
    """Create an event. Timed if WHEN has a time (T), else all-day. Prints the event id."""
    try:
        ev = gcal_mod.create_event(title, when, end=end, calendar_id=cal, minutes=minutes)
    except (gcal_mod.CalError, ValueError) as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from None
    typer.echo(f"created: {ev.summary}  {ev.start}  [{ev.calendar_id}]", err=True)
    typer.echo(ev.id)


@cal_app.command("find")
def cal_find(
    text: str = typer.Argument(..., help="match event titles (case-insensitive substring)"),
    days: int = typer.Option(60, "--days", "-d", help="how far to look (default 60)"),
    past: bool = typer.Option(
        False, "--past", "-p", help="search the past, not the future ('when was my last X')"
    ),
    as_json: bool = typer.Option(False, "--json", help="emit JSON"),
) -> None:
    """Find events whose title matches TEXT. Forward by default; `--past` for 'when was my last X'."""
    try:
        events = gcal_mod.search_events(text, days=days, past=past)
    except gcal_mod.CalError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from None
    if not _emit_events(events, as_json):
        typer.echo(f"no {'past' if past else 'upcoming'} '{text}' within {days} day(s)", err=True)


@cal_app.command("cals")
def cal_cals(as_json: bool = typer.Option(False, "--json", help="emit JSON")) -> None:
    """List calendars (TSV: id, name, role, primary)."""
    try:
        cals = gcal_mod.list_calendars()
    except gcal_mod.CalError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from None
    if as_json:
        typer.echo(json.dumps([asdict(c) for c in cals]))
        return
    for c in cals:
        typer.echo(f"{c.id}\t{c.name}\t{c.role}\t{'primary' if c.primary else ''}")


@cal_app.command("auth")
def cal_auth(
    out: bool = typer.Option(False, "--logout", help="remove the cached token and exit"),
    readonly: bool = typer.Option(False, "--readonly", help="grant read-only scope"),
) -> None:
    """Google OAuth (shared token with gsheets/gdocs). Default grants read+write."""
    _google_auth(out, readonly)


@cal_app.command("accounts")
def cal_accounts() -> None:
    """List Google accounts with a cached token; `*` marks the active one."""
    _google_accounts()


# --- gmail helpers ---


def _short_from(raw: str) -> str:
    """'Name <email>' -> Name (or the email if unnamed)."""
    raw = raw.strip()
    if "<" in raw:
        name = raw.split("<", 1)[0].strip().strip('"')
        return name or raw.split("<", 1)[1].rstrip(">")
    return raw


def _emit_messages(msgs, as_json: bool) -> None:
    if as_json:
        typer.echo(json.dumps([asdict(m) for m in msgs], default=str))
        return
    if not msgs:
        typer.echo("no messages", err=True)
        return
    for m in msgs:
        mark = "●" if m.unread else " "
        typer.echo(f"{mark} {m.id}  {m.date}  {_short_from(m.from_)}  —  {m.subject}")
        if m.snippet:
            typer.echo(f"    {m.snippet[:160]}")


# --- gmail commands ---


@gmail_app.callback(invoke_without_command=True)
def _gmail_main(
    ctx: typer.Context,
    account: str = typer.Option(
        None, "--account", "-a", help="Google account (else env / config / 'default')"
    ),
    n: int = typer.Option(10, "-n", "--max", help="how many messages (default 10)"),
    unread: bool = typer.Option(False, "--unread", help="only unread"),
    as_json: bool = typer.Option(False, "--json", help="emit JSON"),
) -> None:
    """Recent inbox messages (bare `ko gmail`). Subcommands: search / from / view."""
    _set_account(account)
    if ctx.invoked_subcommand is not None:
        return
    try:
        msgs = gmail_mod.recent(n=n, unread=unread)
    except gmail_mod.GmailError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from None
    _emit_messages(msgs, as_json)


@gmail_app.command("search")
def gmail_search(
    query: list[str] = typer.Argument(..., help="Gmail query, e.g. from:alice newer_than:7d"),
    n: int = typer.Option(10, "-n", "--max", help="how many (default 10)"),
    unread: bool = typer.Option(False, "--unread", help="only unread"),
    as_json: bool = typer.Option(False, "--json", help="emit JSON"),
) -> None:
    """Search with Gmail's own query syntax (passed verbatim): `ko gmail search is:unread newer_than:2d`."""
    q = " ".join(query) + (" is:unread" if unread else "")
    try:
        msgs = gmail_mod.search(q, n=n)
    except gmail_mod.GmailError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from None
    _emit_messages(msgs, as_json)


@gmail_app.command("from")
def gmail_from(
    who: str = typer.Argument(..., help="sender name or email"),
    n: int = typer.Option(10, "-n", "--max", help="how many (default 10)"),
    unread: bool = typer.Option(False, "--unread", help="only unread"),
    as_json: bool = typer.Option(False, "--json", help="emit JSON"),
) -> None:
    """Recent mail from a person (shortcut for `search from:<who>`)."""
    try:
        msgs = gmail_mod.from_sender(who, n=n, unread=unread)
    except gmail_mod.GmailError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from None
    _emit_messages(msgs, as_json)


@gmail_app.command("view")
def gmail_view(
    msg_id: str = typer.Argument(..., help="message id (from a list/search row)"),
    full: bool = typer.Option(
        False, "--full", help="print the whole body (default: first ~1500 chars)"
    ),
) -> None:
    """Read one message: headers + plain-text body."""
    try:
        meta, body = gmail_mod.get_message(msg_id)
    except gmail_mod.GmailError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from None
    typer.echo(f"From:    {meta.from_}")
    typer.echo(f"Date:    {meta.date}")
    typer.echo(f"Subject: {meta.subject}\n")
    typer.echo(body if full else body[:1500])
    if not full and len(body) > 1500:
        typer.echo(f"\n… {len(body) - 1500} more chars — pass --full", err=True)


@gmail_app.command("thread")
def gmail_thread(
    thread_id: str = typer.Argument(..., help="thread id (a message's thread_id from a list row)"),
    full: bool = typer.Option(
        False, "--full", help="print each whole body (default: first ~800 chars per message)"
    ),
) -> None:
    """Read a whole conversation: every message in the thread, oldest first."""
    try:
        msgs = gmail_mod.get_thread(thread_id)
    except gmail_mod.GmailError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from None
    if not msgs:
        typer.echo("no messages", err=True)
        return
    cap = 800
    for i, (meta, body) in enumerate(msgs):
        if i:
            typer.echo("\n" + "─" * 40 + "\n")
        typer.echo(f"From:    {meta.from_}")
        typer.echo(f"Date:    {meta.date}")
        typer.echo(f"Subject: {meta.subject}\n")
        typer.echo(body if full else body[:cap])
        if not full and len(body) > cap:
            typer.echo(f"\n… {len(body) - cap} more chars — pass --full", err=True)


@gmail_app.command("auth")
def gmail_auth(
    out: bool = typer.Option(False, "--logout", help="remove the cached token and exit"),
    readonly: bool = typer.Option(False, "--readonly", help="grant read-only scope"),
) -> None:
    """Google OAuth (shared token with gsheets/gdocs/cal). Default grants read+write."""
    _google_auth(out, readonly)


@gmail_app.command("accounts")
def gmail_accounts() -> None:
    """List Google accounts with a cached token; `*` marks the active one."""
    _google_accounts()
