"""Google cluster CLI commands: gsheets, gdocs, drive, cal, gmail."""

from __future__ import annotations

import functools
import json
import os
import re
import sys
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

import typer
from googleapiclient.errors import HttpError

from . import config, google_auth
from ._cli_shared import (
    _die,
    _emit_json,
    _no_results,
    _reject_group_flags,
    _tsv_cell,
    app,
    lazy,
)

gcal_mod = lazy("gcal")
gdocs_mod = lazy("gdocs")
gdrive_mod = lazy("gdrive")
gmail_mod = lazy("gmail")
gsheets_mod = lazy("gsheets")


def _http_error_msg(e: HttpError) -> str:
    """Clean human-readable message from a Google HttpError.
    Uses the API's own reason string (e.g. 'Unable to parse range: BadTab!A1')
    instead of the full repr which leaks API URLs."""
    status = e.resp.status
    reason = e.reason or str(e.content[:120].decode("utf-8", "replace"))
    return f"[{status}] {reason}"


@contextmanager
def _google_errors(as_json: bool = False):
    """One net for every Google failure mode: a domain error (403/404), a stray HttpError
    (400 bad range, 429 quota, 5xx), AuthError (no usable token — carries its `fix`), or a
    network blip. Turns each into a clean `_die` line instead of a traceback. Every command in
    the Google groups runs inside it (`_GoogleTyper`), so none can forget."""
    try:
        yield
    except google_auth.AuthError as e:
        _die(e.msg, as_json=as_json, code="auth", fix=e.fix)
    except google_auth.GoogleError as e:
        _die(str(e), as_json=as_json)
    except HttpError as e:
        _die(_http_error_msg(e), as_json=as_json)
    except google_auth.NETWORK_ERRORS as e:
        _die(f"network error talking to Google: {e}", as_json=as_json, code="network")


class _GoogleTyper(typer.Typer):
    """A Typer whose commands and callbacks all run inside `_google_errors` (honouring the
    command's own `--json`)."""

    @staticmethod
    def _netted(fn):
        @functools.wraps(fn)
        def run(*args, **kwargs):
            with _google_errors(bool(kwargs.get("as_json"))):
                return fn(*args, **kwargs)

        return run

    def command(self, *args, **kwargs):
        register = super().command(*args, **kwargs)
        return lambda fn: register(self._netted(fn))

    def callback(self, *args, **kwargs):
        register = super().callback(*args, **kwargs)
        return lambda fn: register(self._netted(fn))


# --- sub-apps ---

gsheets_app = _GoogleTyper(
    help=(
        "Read & write Google Sheets (account from `ko google`). Reads: info / tabs / get / find. "
        "Writes (refuse to clobber non-empty cells unless --overwrite): set / put / "
        "header / add-tab / new / clear. Tab & bulk ops: dup-tab / delete-tab / "
        "insert-rows / insert-cols / replace. Print: pdf."
    ),
    no_args_is_help=True,
)
app.add_typer(gsheets_app, name="gsheets")
app.add_typer(gsheets_app, name="gs", hidden=True)  # shortcut alias

gdocs_app = _GoogleTyper(
    help=(
        "Read & write Google Docs as Markdown (account from `ko google`). "
        "Read: get (Doc→Markdown) / info / tabs / comments. Write: push (Markdown→new or existing "
        "Doc) / replace / append / add-tab / reply / shade-table. Per-client docs use one tab each — "
        "`tabs` lists them; get/replace/append take `--tab`."
    ),
    no_args_is_help=True,
)
app.add_typer(gdocs_app, name="gdocs")
app.add_typer(gdocs_app, name="gd", hidden=True)  # shortcut alias

cal_app = _GoogleTyper(
    help=(
        "Google Calendar across all your accounts: `ko cal` = next 7 days merged (`-a` for one); "
        "also `day`, `find`, `cals`, `add` (lands where `cal use` says)."
    ),
)
app.add_typer(cal_app, name="cal")

gmail_app = _GoogleTyper(
    help=(
        "Gmail, thread-first (account from `ko google`; every command names it on stderr). Bare "
        "`ko gmail` = recent inbox threads; also `search` (Gmail query syntax), `from <who>`, "
        "`thread <id>` (read one), `view <msg id>`, `attach <msg id>`. `draft reply <thread>` "
        "answers a thread. Sending is opt-in (`draft send`)."
    ),
)
app.add_typer(gmail_app, name="gmail")

gmail_draft_app = _GoogleTyper(
    help=(
        "Compose Gmail drafts: new / reply / list / show / update / send. Drafts are the default workflow; "
        "`draft send` is opt-in (`\\[gmail] allow_send = true` / KO_GMAIL_SEND=1). "
        "Write bodies in Markdown via `--md <file>`: headings, **bold**/*italic*, bulleted + "
        "numbered lists, [links](url), GFM tables, ~~strikethrough~~, `code` all render as "
        "formatted mail (no underline — Markdown has none). Plain `--body` stays plain text."
    ),
    no_args_is_help=True,
)
gmail_app.add_typer(gmail_draft_app, name="draft")

drive_app = _GoogleTyper(
    help="Mirror a Drive folder locally (`ls`, `pull`) and push edits back in place (`push`); never deletes, refuses both-sides-changed.",
    no_args_is_help=True,
)
app.add_typer(drive_app, name="drive")


# --- ko google: the one way to sign in, pick and name accounts (one token per account) ---

google_app = _GoogleTyper(
    help=(
        "Google accounts, shared by gsheets/gdocs/drive/cal/gmail. `auth <name>` signs one in "
        "under a short tag (`home`, `sfl`); `use <name>` makes it the default; `-a <name>` on any "
        "Google command picks one for that call."
    ),
    no_args_is_help=True,
)
app.add_typer(google_app, name="google")

_ACCOUNT_OPT = typer.Option(
    None, "--account", "-a", help="Google account tag (default: `ko google use`; `ko google accounts` lists them)"
)
_NAME_ARG = typer.Argument(None, help="account tag (default: the active account)")


def _set_account(account: str | None) -> None:
    if account:
        os.environ["KO_GOOGLE_ACCOUNT"] = account


def _account(name: str | None) -> str:
    if name is None:
        return google_auth.active_account()
    return google_auth.check_name(name)


def account_lines(lookup: bool = True) -> list[str]:
    """The `accounts` listing, gh-auth-status style: one line per account (`*` = active) with
    the signed-in address, then what its grant can do. Shared with `ko doctor`, which passes
    `lookup=False` offline (no API call for an address the token file lacks)."""
    active = google_auth.active_account()
    lines = []
    for name in sorted(set(google_auth.list_accounts()) | {active}):
        st = google_auth.account_status(name, lookup=lookup)
        mark = "*" if name == active else " "
        if st.kind == "none":
            lines.append(f"{mark} {name}  (no token — `ko google auth {name}`)")
            continue
        who = st.email or f"(address unknown — token stale? re-run `ko google auth {name}`)"
        tag = "  (service account)" if st.kind == "service-account" else ""
        lines.append(f"{mark} {name}  {who}{tag}")
        if st.kind == "service-account":
            lines.append("    read+write on whatever is shared with that address")
        else:
            lines.append(f"    {google_auth.describe_scopes(st.scopes)}")
        lines.append(f"    {st.path}")
    return lines


def account_banner() -> str:
    """`sfl · khalidomar@syntechfibres.com` — which account a command is reading/writing.
    Local files only, no API call."""
    name = google_auth.active_account()
    email = google_auth.account_status(name, lookup=False).email
    return f"{name} · {email}" if email else name


@google_app.command("auth")
def google_auth_cmd(
    name: str = _NAME_ARG,
    readonly: bool = typer.Option(False, "--readonly", help="grant read-only scope (default read+write)"),
    sa_key: Path = typer.Option(
        None, "--service-account", exists=True, dir_okay=False,
        help="register a service-account key JSON under this name instead (no browser)",
    ),
) -> None:
    """Sign an account in (browser consent) under a short tag: `ko google auth sfl`. Re-run to re-consent."""
    acct = _account(name)
    first = not google_auth.list_accounts()
    if sa_key is not None:
        shadowed = google_auth.token_file(acct).exists()
        email = google_auth.register_service_account(sa_key, acct)
        if shadowed:
            typer.echo(
                f"note: '{acct}' also has an OAuth token; the service account now takes "
                f"precedence for it. `ko google logout {acct}` clears both.",
                err=True,
            )
        typer.echo(
            f"Registered service account for '{acct}': {email}\n"
            f"Share the Sheets/Docs/Drive files you want it to reach with that address, then "
            f"use `-a {acct}`. (Gmail and personal Calendars need domain-wide delegation — an "
            f"SA can't read them by being shared.)\n"
            f"Key copied to {google_auth.sa_file(acct)} — delete your downloaded copy."
        )
    elif google_auth.sa_file(acct).exists():
        typer.echo(
            f"'{acct}' is a service-account profile — no browser auth needed. "
            f"`ko google logout {acct}` unregisters it."
        )
        return
    else:
        google_auth.authenticate(readonly=readonly, account=acct)
        st = google_auth.account_status(acct)
        typer.echo(f"Signed in: {acct}  {st.email or '(address unknown)'}")
        typer.echo(f"    {google_auth.describe_scopes(st.scopes)}")
        typer.echo(f"    {st.path}")
    if first and google_auth.active_account() != acct:
        google_auth.use_account(acct)
        typer.echo(f"'{acct}' is your first account — now the default.", err=True)


@google_app.command("logout")
def google_logout(name: str = _NAME_ARG) -> None:
    """Remove an account's token (or service-account key)."""
    acct = _account(name)
    removed = google_auth.logout(acct)
    typer.echo(f"Signed out of '{acct}'." if removed else f"'{acct}' wasn't signed in.")


@google_app.command("use")
def google_use(name: str = typer.Argument(..., help="account tag to make the default")) -> None:
    """Make an account the default on this machine until you switch again."""
    google_auth.use_account(name)
    email = google_auth.account_status(name).email
    typer.echo(f"default Google account: {name}" + (f" · {email}" if email else ""))
    if os.environ.get("KO_GOOGLE_ACCOUNT"):
        typer.echo("note: KO_GOOGLE_ACCOUNT is set in this shell and still wins", err=True)


@google_app.command("accounts")
def google_accounts(
    as_json: bool = typer.Option(False, "--json", help="emit JSON: [{name, active, kind, email, scopes, path}]"),
) -> None:
    """Who's signed in: each account's address and what its grant can do; `*` marks the active one."""
    if as_json:
        active = google_auth.active_account()
        rows = []
        for name in google_auth.list_accounts():
            st = google_auth.account_status(name)
            rows.append({"name": name, "active": name == active, "kind": st.kind, "email": st.email,
                         "scopes": st.scopes, "path": str(st.path) if st.path else None})
        typer.echo(json.dumps(rows))
        return
    for line in account_lines():
        typer.echo(line)


@google_app.command("rename")
def google_rename(
    old: str = typer.Argument(..., help="current tag"),
    new: str = typer.Argument(..., help="new tag"),
) -> None:
    """Rename an account's tag, e.g. `ko google rename work kd`. Keeps its token."""
    google_auth.rename_account(old, new)
    typer.echo(f"renamed '{old}' → '{new}'")
    if config.get("google", "account") == old:
        typer.echo(
            f"note: config.toml still says [google] account = \"{old}\" — "
            f"this machine now uses '{new}'; edit config.toml for other machines",
            err=True,
        )


# --- gsheets helpers ---

_SHEET_HELP = "Google Sheet ID, URL, or a learned name (`ko gsheets alias` lists them)"


def _resolve_sid(ref: str, as_json: bool = False) -> str:
    """A sheet id / URL / learned name -> spreadsheet id, dying cleanly (not with a traceback) on
    an unknown or ambiguous name."""
    try:
        return gsheets_mod.resolve_sheet(ref)
    except gsheets_mod.SheetsError as e:
        _die(str(e), as_json=as_json)


def _emit_rows(rows: list[list], as_json: bool) -> None:
    if as_json:
        typer.echo(json.dumps(rows, default=str))
        return
    # Default: TSV — survives commas, pipes into cut/awk/mlr cleanly. Cells are sanitized so a
    # multi-line sheet cell (common) doesn't split one logical row across two output lines.
    for row in rows:
        typer.echo("\t".join(_tsv_cell(c) for c in row))


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
    account: str = _ACCOUNT_OPT,
) -> None:
    """Pick the Google account for this command (one token covers Sheets/Docs/Calendar)."""
    _set_account(account)


@gsheets_app.command("info")
def gsheets_info(
    spreadsheet_id: str = typer.Argument(..., help=_SHEET_HELP),
    as_json: bool = typer.Option(
        False, "--json", help="emit JSON instead of plain text"
    ),
) -> None:
    """Show a sheet's title and tab names."""
    info = gsheets_mod.get_info(_resolve_sid(spreadsheet_id, as_json))
    if as_json:
        _emit_json(info)
        return
    typer.echo(info.title)
    for t in info.tabs:
        typer.echo(f"  {t}")


@gsheets_app.command("tabs")
def gsheets_tabs(
    spreadsheet_id: str = typer.Argument(..., help=_SHEET_HELP),
    as_json: bool = typer.Option(False, "--json", help="emit a JSON array of tab names"),
) -> None:
    """List tab names, one per line (machine-friendly)."""
    info = gsheets_mod.get_info(_resolve_sid(spreadsheet_id, as_json))
    if as_json:
        typer.echo(json.dumps(info.tabs))
        return
    for t in info.tabs:
        typer.echo(t)


@gsheets_app.command("get")
def gsheets_get(
    spreadsheet_id: str = typer.Argument(..., help=_SHEET_HELP),
    range_name: str = typer.Argument(
        None,
        help="A1 range ('Jobs!A4:T10'), a bare tab name ('Cars' = that tab's whole used range), or "
        "omit entirely for the first tab's used range",
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
    """Fetch an A1 range, a whole tab (bare tab name), or — with no range — the first tab's used
    range. TSV by default; --json for a 2D array. Rows are padded to a rectangle, so a short row
    never shifts columns under `cut -f` / JSON indexing."""
    if raw and formula:
        typer.echo("--raw and --formula are mutually exclusive", err=True)
        raise typer.Exit(2)
    render = "UNFORMATTED_VALUE" if raw else "FORMULA" if formula else "FORMATTED_VALUE"
    sid = _resolve_sid(spreadsheet_id, as_json)
    if range_name is None:  # no range -> the first tab's used range (one metadata call)
        api_range = gsheets_mod.quote_tab(gsheets_mod.first_tab(sid))
    elif "!" in range_name:  # a full 'Tab!A1:Z9' — the caller owns any quoting
        api_range = range_name
    else:  # a bare tab name -> that tab's used range, quoted if it needs it
        api_range = gsheets_mod.quote_tab(range_name)
    rows = gsheets_mod.get_range(sid, api_range, value_render=render)
    _emit_rows(gsheets_mod.pad_rectangular(rows), as_json)


@gsheets_app.command("set")
def gsheets_set(
    spreadsheet_id: str = typer.Argument(..., help=_SHEET_HELP),
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
            _resolve_sid(spreadsheet_id),
            anchor,
            _parse_cells(text),
            raw=raw,
            overwrite=overwrite,
        )
    except (gsheets_mod.SheetsError, ValueError, json.JSONDecodeError) as e:
        _die(str(e))
    except HttpError as e:
        _die(_http_error_msg(e))
    typer.echo(f"{n} cell(s) updated", err=True)


@gsheets_app.command("put")
def gsheets_put(
    spreadsheet_id: str = typer.Argument(..., help=_SHEET_HELP),
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
            raise ValueError('`put` expects a JSON object: {"anchor": value, ...}')  # noqa: TRY004 — user input, caught with the JSON errors
        data = [(anchor, _norm_block(val)) for anchor, val in obj.items()]
        n = gsheets_mod.write_ranges(
            _resolve_sid(spreadsheet_id), data, raw=raw, overwrite=overwrite
        )
    except (gsheets_mod.SheetsError, ValueError, json.JSONDecodeError) as e:
        _die(str(e))
    except HttpError as e:
        _die(_http_error_msg(e))
    typer.echo(f"{n} cell(s) updated", err=True)


@gsheets_app.command("find")
def gsheets_find(
    spreadsheet_id: str = typer.Argument(..., help=_SHEET_HELP),
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
            _resolve_sid(spreadsheet_id, as_json), text, formula=formula, tabs=tab or None
        )
    except gsheets_mod.SheetsError as e:
        _die(str(e), as_json=as_json)
    except HttpError as e:
        _die(_http_error_msg(e), as_json=as_json)
    if not hits:
        _no_results("no matches", as_json)
    if as_json:
        typer.echo(json.dumps([{"tab": t, "ref": ref, "cell": cell} for t, ref, cell in hits]))
        return
    for t, ref, cell in hits:
        typer.echo(f"{t}\t{ref}\t{_tsv_cell(cell)}")


@gsheets_app.command("alias")
def gsheets_alias(
    name: str = typer.Argument(None, help="alias to set (omit both args to list known aliases)"),
    target: str = typer.Argument(None, help="sheet id or URL the alias points at"),
    as_json: bool = typer.Option(False, "--json", help="emit JSON (list mode)"),
) -> None:
    """List learned sheet aliases, or pin one by hand.

    No Drive scope means ko can't browse Drive by name — so it LEARNS a sheet's own title -> id
    whenever a command fetches sheet metadata (`info`, `tabs`, or a `get` without an explicit
    range). Then `ko gsheets get Cars` works instead of pasting the id. Bare `alias` lists what
    ko knows (TSV: alias, id, last_used, newest first); `alias <name> <id-or-url>` pins a custom
    name — pins outrank learned titles and learning never overwrites them."""
    if name and target:
        try:
            sid = gsheets_mod.set_alias(name, target)
        except gsheets_mod.SheetsAliasError as e:
            _die(str(e), as_json=as_json, code="usage")
        typer.echo(f"{name} -> {sid}", err=True)
        return
    if name and not target:
        _die("`alias <name> <id-or-url>` needs both, or pass neither to list", code="usage")
    aliases = gsheets_mod.load_aliases()
    if not aliases:
        _no_results(
            "no aliases yet — ko learns a sheet's title from `info`/`tabs` or a range-less `get`",
            as_json,
        )
    rows = sorted(aliases.items(), key=lambda kv: kv[1].get("last_used", ""), reverse=True)
    if as_json:
        typer.echo(
            json.dumps(
                [{"alias": a, "id": r["id"], "last_used": r.get("last_used", "")} for a, r in rows]
            )
        )
        return
    for a, r in rows:
        typer.echo(f"{a}\t{r['id']}\t{r.get('last_used', '')}")


@gsheets_app.command("header")
def gsheets_header(
    spreadsheet_id: str = typer.Argument(..., help=_SHEET_HELP),
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
            _resolve_sid(spreadsheet_id),
            range_name,
            bold=not no_bold,
            bg=None if no_fill else gsheets_mod.DEFAULT_HEADER_BG,
            freeze=freeze,
        )
    except (gsheets_mod.SheetsError, ValueError) as e:
        _die(str(e))
    except HttpError as e:
        _die(_http_error_msg(e))
    typer.echo("header formatted", err=True)


@gsheets_app.command("add-tab")
def gsheets_add_tab(
    spreadsheet_id: str = typer.Argument(..., help=_SHEET_HELP),
    title: str = typer.Argument(..., help="new tab name"),
) -> None:
    """Add a tab; prints its sheetId (gid)."""
    try:
        gid = gsheets_mod.add_tab(_resolve_sid(spreadsheet_id), title)
    except gsheets_mod.SheetsError as e:
        _die(str(e))
    except HttpError as e:
        _die(_http_error_msg(e))
    typer.echo(str(gid))


@gsheets_app.command("dup-tab")
def gsheets_dup_tab(
    spreadsheet_id: str = typer.Argument(..., help=_SHEET_HELP),
    source: str = typer.Argument(..., help="tab to duplicate (by name)"),
    new_name: str = typer.Argument(
        None, help="name for the copy (default: 'Copy of <tab>'); must not already exist"
    ),
) -> None:
    """Duplicate a tab — formatting, formulas and all — right after the source; prints the new gid.

    The invoice move: dup last month's tab, then `set`/`replace` the values."""
    try:
        gid = gsheets_mod.duplicate_tab(_resolve_sid(spreadsheet_id), source, new_name)
    except gsheets_mod.SheetsError as e:
        _die(str(e))
    except HttpError as e:
        _die(_http_error_msg(e))
    typer.echo(f"duplicated {source!r} -> {new_name or f'Copy of {source}'!r}", err=True)
    typer.echo(str(gid))


@gsheets_app.command("delete-tab")
def gsheets_delete_tab(
    spreadsheet_id: str = typer.Argument(..., help=_SHEET_HELP),
    title: str = typer.Argument(..., help="tab to delete (by name)"),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="skip the confirm (required when not a TTY)"
    ),
) -> None:
    """Delete a tab by name. Destructive — checks the tab exists, then confirms first."""
    sid = _resolve_sid(spreadsheet_id)
    try:
        info = gsheets_mod.get_info(sid)
    except gsheets_mod.SheetsError as e:
        _die(str(e))
    except HttpError as e:
        _die(_http_error_msg(e))
    if title not in info.tabs:
        _die(f"no tab named {title!r} in {info.title!r} — tabs: {', '.join(info.tabs)}")
    if not yes:
        if sys.stdin.isatty():
            typer.confirm(f"delete tab {title!r} from {info.title!r}?", abort=True)
        else:
            typer.echo("refusing to delete without a TTY — pass --yes", err=True)
            raise typer.Exit(2)
    try:
        gsheets_mod.delete_tab(sid, title)
    except gsheets_mod.SheetsError as e:
        _die(str(e))
    except HttpError as e:
        _die(_http_error_msg(e))
    typer.echo(f"deleted tab {title!r} from {info.title!r}", err=True)


@gsheets_app.command("pdf")
def gsheets_pdf(
    spreadsheet_id: str = typer.Argument(..., help=_SHEET_HELP),
    tab: str = typer.Argument(None, help="tab to export (default: the whole spreadsheet)"),
    out: Path = typer.Option(
        None, "-o", "--out", help="output file (default: <tab or sheet title>.pdf in cwd)"
    ),
    size: str = typer.Option("A4", "--size", help="paper size (A4, letter, legal, A3, …)"),
    landscape: bool = typer.Option(False, "--landscape", help="landscape instead of portrait"),
    gridlines: bool = typer.Option(False, "--gridlines", help="include gridlines"),
) -> None:
    """Print a tab (or the whole sheet) to PDF — A4 portrait, fit-to-width by default.

    The invoice move: `ko gsheets pdf <sheet> kodev-008` → kodev-008.pdf, ready to send.
    Prints the saved path (stdout)."""
    sid = _resolve_sid(spreadsheet_id)
    try:
        pdf = gsheets_mod.export_pdf(
            sid, tab, size=size, landscape=landscape, gridlines=gridlines
        )
        name = tab or gsheets_mod.get_info(sid).title.strip()
    except gsheets_mod.SheetsError as e:
        _die(str(e))
    except HttpError as e:
        _die(_http_error_msg(e))
    path = out or Path(f"{name.replace(chr(47), chr(45))}.pdf")
    path.write_bytes(pdf)
    typer.echo(f"{len(pdf) / 1024:.0f} KB, {size} {'landscape' if landscape else 'portrait'}", err=True)
    typer.echo(str(path))


@gsheets_app.command("insert-rows")
def gsheets_insert_rows(
    spreadsheet_id: str = typer.Argument(..., help=_SHEET_HELP),
    tab: str = typer.Argument(..., help="tab to insert into (by name)"),
    row: int = typer.Argument(..., help="1-based row number to insert before (as shown in the sheet)"),
    n: int = typer.Option(1, "--n", "-n", help="how many rows to insert"),
    no_inherit: bool = typer.Option(
        False, "--no-inherit", help="blank rows instead of inheriting formatting from the row above"
    ),
) -> None:
    """Insert blank rows before ROW. Formatting/formulas inherit from the row above by default.

    The line-items move: insert mid-table and the layout (and =SUM ranges) stretch."""
    if row < 1:
        _die("row is 1-based — the first row is 1", code="usage")
    try:
        gsheets_mod.insert_dimension(
            _resolve_sid(spreadsheet_id),
            tab,
            dimension="ROWS",
            start_index=row - 1,
            n=n,
            inherit_from_before=False if no_inherit else None,
        )
    except gsheets_mod.SheetsError as e:
        _die(str(e))
    except HttpError as e:
        _die(_http_error_msg(e))
    typer.echo(f"inserted {n} row(s) before row {row} in {tab!r}", err=True)


@gsheets_app.command("insert-cols")
def gsheets_insert_cols(
    spreadsheet_id: str = typer.Argument(..., help=_SHEET_HELP),
    tab: str = typer.Argument(..., help="tab to insert into (by name)"),
    col: str = typer.Argument(..., help="column letter (or 1-based number) to insert before"),
    n: int = typer.Option(1, "--n", "-n", help="how many columns to insert"),
    no_inherit: bool = typer.Option(
        False, "--no-inherit", help="blank columns instead of inheriting formatting from the left"
    ),
) -> None:
    """Insert blank columns before COL. Formatting inherits from the column to the left by default."""
    if col.isdigit():
        col_n = int(col)
    elif col.isalpha():
        col_n = gsheets_mod.col_num(col)
    else:
        _die(f"bad column {col!r} — want a letter like C or a 1-based number", code="usage")
    if col_n < 1:
        _die("column is 1-based — the first column is 1 (A)", code="usage")
    try:
        gsheets_mod.insert_dimension(
            _resolve_sid(spreadsheet_id),
            tab,
            dimension="COLUMNS",
            start_index=col_n - 1,
            n=n,
            inherit_from_before=False if no_inherit else None,
        )
    except gsheets_mod.SheetsError as e:
        _die(str(e))
    except HttpError as e:
        _die(_http_error_msg(e))
    typer.echo(f"inserted {n} column(s) before {col.upper()} in {tab!r}", err=True)


@gsheets_app.command("replace")
def gsheets_replace(
    spreadsheet_id: str = typer.Argument(..., help=_SHEET_HELP),
    find: str = typer.Argument(..., help="text to find (substring within a cell)"),
    replacement: str = typer.Argument(..., help="replacement text"),
    tab: str = typer.Option(None, "--tab", "-t", help="limit to one tab (by name)"),
    all_tabs: bool = typer.Option(
        False, "--all-tabs", help="search every tab (required when no --tab — deliberate friction)"
    ),
    match_case: bool = typer.Option(False, "--match-case", help="case-sensitive match"),
    in_formulas: bool = typer.Option(
        False, "--in-formulas", help="also replace inside formulas (default: values only)"
    ),
) -> None:
    """Find & replace across a tab. Prints how many occurrences changed.

    Pairs with dup-tab: duplicate an invoice, then swap the old id/dates for new ones."""
    if not tab and not all_tabs:
        _die("pick a --tab, or pass --all-tabs to touch the whole spreadsheet", code="usage")
    try:
        n = gsheets_mod.find_replace(
            _resolve_sid(spreadsheet_id),
            find,
            replacement,
            tab=tab,
            match_case=match_case,
            include_formulas=in_formulas,
        )
    except gsheets_mod.SheetsError as e:
        _die(str(e))
    except HttpError as e:
        _die(_http_error_msg(e))
    typer.echo(str(n))


@gsheets_app.command("new")
def gsheets_new(
    title: str = typer.Argument(..., help="title for the new spreadsheet"),
) -> None:
    """Create a new spreadsheet; prints its ID (stdout) and URL (stderr)."""
    try:
        new_id = gsheets_mod.create_spreadsheet(title)
    except gsheets_mod.SheetsError as e:
        _die(str(e))
    except HttpError as e:
        _die(_http_error_msg(e))
    typer.echo(new_id)
    typer.echo(f"https://docs.google.com/spreadsheets/d/{new_id}/edit", err=True)


@gsheets_app.command("clear")
def gsheets_clear(
    spreadsheet_id: str = typer.Argument(..., help=_SHEET_HELP),
    range_name: str = typer.Argument(..., help="range to clear (values only; formatting survives)"),
) -> None:
    """Clear the values in a range. Formatting, notes, and validation survive."""
    try:
        cleared = gsheets_mod.clear_range(_resolve_sid(spreadsheet_id), range_name)
    except gsheets_mod.SheetsError as e:
        _die(str(e))
    except HttpError as e:
        _die(_http_error_msg(e))
    typer.echo(f"cleared {cleared}", err=True)


# --- gdocs commands ---


@gdocs_app.callback()
def _gdocs_account(
    account: str = _ACCOUNT_OPT,
) -> None:
    """Pick the Google account for this command (one token covers Sheets/Docs/Calendar)."""
    _set_account(account)


def _guard_multi_tab(doc: str, extra: str = "") -> None:
    """Refuse a whole-doc write on a MULTI-TAB doc (exit 2). A bare `replaceAllText` with no
    tabsCriteria rewrites EVERY tab — on a per-client doc that silently clobbers all clients. A
    single-tab (or no-tabs) doc passes through untouched, so the common case keeps no friction.
    `extra` names the escape hatch(es) in the message (e.g. ' or --all-tabs')."""
    tabs = gdocs_mod.list_tabs(doc)
    if len(tabs) > 1:
        _die(f"this doc has {len(tabs)} tabs — pass --tab <title|id>{extra}", code="usage")


def _guard_comment_orphans(doc: str, find: str) -> None:
    """Before a tab-scoped replace: if the text being replaced is (or contains) an open comment's
    quoted anchor, that thread would lose its anchor. List the hits and refuse (exit 1) — the
    caller passes --force to proceed. Comments are a whole-doc list (the API doesn't scope them by
    tab), so this can over-warn on a same-worded quote in another tab; --force is the escape hatch."""
    try:
        comments = gdrive_mod.list_comments(doc)
    except gdrive_mod.DriveError:
        return  # can't read comments (e.g. doc not made by ko) → don't block the edit
    hits = gdocs_mod.find_orphan_comments(comments, find)
    if not hits:
        return
    typer.echo(
        f"refusing: replacing {find!r} would orphan {len(hits)} comment anchor(s) — pass --force to proceed:",
        err=True,
    )
    for c in hits:
        quote = c.get("quoted_text", "")[:60]
        typer.echo(f"  {c.get('author', '?')}: > {quote}", err=True)
    raise typer.Exit(1)


@gdocs_app.command("tabs")
def gdocs_tabs(
    doc: str = typer.Argument(..., help="Google Doc ID or URL"),
    as_json: bool = typer.Option(False, "--json", help="emit a JSON array of {index,title,tab_id,nesting_level}"),
) -> None:
    """List a doc's tabs (TSV: index, title, tabId; sub-tabs indented). Per-client shared docs
    keep one tab per client — feed a tabId or title to get/replace/append `--tab`."""
    try:
        tabs = gdocs_mod.list_tabs(doc)
    except gdocs_mod.DocsError as e:
        _die(str(e), as_json=as_json)
    if as_json:
        _emit_json(tabs)
        return
    for t in tabs:
        typer.echo(f"{t.index}\t{'  ' * t.nesting_level}{t.title}\t{t.tab_id}")


@gdocs_app.command("get")
def gdocs_get(
    doc: str = typer.Argument(..., help="Google Doc ID or URL"),
    out: Path = typer.Option(None, "--out", "-o", help="write to a file instead of stdout"),
    tab: str = typer.Option(
        None, "--tab", help="read just one tab (title, case-insensitive, or a raw tabId) — see `tabs`"
    ),
) -> None:
    """Read a Doc as Markdown (tables included) — pipe it to an LLM/agent, or save with -o.
    `--tab` reads one tab as markdown-ish plain text (Drive export can't scope to a tab, so it's
    walked from the Docs API — headings/bullets kept, tables noted)."""
    try:
        md = gdocs_mod.get_tab_text(doc, tab) if tab else gdrive_mod.export_markdown(doc)
    except (gdrive_mod.DriveError, gdocs_mod.DocsError) as e:
        _die(str(e))
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
        _die(str(e))
    typer.echo(info.title)
    typer.echo(info.id, err=True)


@gdocs_app.command("append")
def gdocs_append(
    doc: str = typer.Argument(..., help="Google Doc ID or URL"),
    text: str = typer.Argument(None, help="text to append (omit to read stdin)"),
    tab: str = typer.Option(
        None, "--tab", help="append to the end of one tab (title or tabId); else the whole doc"
    ),
) -> None:
    """Append text to the end of a doc (or of one tab with --tab). On a multi-tab doc `--tab` is
    required (a bare append would land in the first tab only — surprising)."""
    body = text if text is not None else sys.stdin.read()
    try:
        if tab is None:
            _guard_multi_tab(doc)  # multi-tab: force an explicit --tab (bare append hits tab 0)
        n = gdocs_mod.append_text(doc, body, tab=tab)
    except gdocs_mod.DocsError as e:
        _die(str(e))
    typer.echo(f"appended {n} char(s)", err=True)


@gdocs_app.command("replace")
def gdocs_replace(
    doc: str = typer.Argument(..., help="Google Doc ID or URL"),
    find: str = typer.Argument(..., help="text to find"),
    replace: str = typer.Argument(..., help="replacement text"),
    match_case: bool = typer.Option(False, "--match-case", help="case-sensitive match"),
    tab: str = typer.Option(
        None, "--tab", help="scope the replace to one tab (title or tabId); else the whole doc"
    ),
    all_tabs: bool = typer.Option(
        False, "--all-tabs", help="rewrite EVERY tab (required on a multi-tab doc when no --tab)"
    ),
    force: bool = typer.Option(
        False, "--force", help="with --tab: replace even if it would orphan a comment's anchor"
    ),
) -> None:
    """Replace every occurrence of FIND with REPLACE. `--tab` scopes it to one tab; a bare replace
    hits the whole doc. On a MULTI-TAB doc a bare replace refuses (it would rewrite every tab —
    e.g. every client in a per-client doc) — pass `--tab` or `--all-tabs`. With --tab, first
    checks open comments and refuses if the replaced text is a comment's quoted anchor (would
    orphan the thread) — `--force` overrides."""
    try:
        if tab is not None:
            tab_id = gdocs_mod.resolve_tab(doc, tab)
            if not force:
                _guard_comment_orphans(doc, find)
            n = gdocs_mod.replace_text(doc, find, replace, match_case=match_case, tab_ids=[tab_id])
        else:
            if not all_tabs:
                _guard_multi_tab(doc, extra=" or --all-tabs")  # bare replace clobbers all tabs
            n = gdocs_mod.replace_text(doc, find, replace, match_case=match_case)
    except gdocs_mod.DocsError as e:
        _die(str(e))
    typer.echo(f"replaced {n} occurrence(s)", err=True)


@gdocs_app.command("add-tab")
def gdocs_add_tab(
    doc: str = typer.Argument(..., help="Google Doc ID or URL"),
    title: str = typer.Argument(..., help="title for the new tab"),
) -> None:
    """Add a tab to a Doc (e.g. a new client/topic in a shared doc); prints its tabId (stdout).
    Then `append --tab <title>` to fill it. Creates a top-level tab at the end."""
    try:
        tab_id = gdocs_mod.add_tab(doc, title)
    except gdocs_mod.DocsError as e:
        _die(str(e))
    typer.echo(tab_id)
    typer.echo(f"added tab {title!r}", err=True)


_DRIVE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{20,}$")


def _resolve_folder(folder: str | None) -> str | None:
    """A `--folder` value → a folder ID. A `/folders/` URL or a bare ID passes through; anything
    else is treated as a name and find-or-created (so `--folder Proposals` just works)."""
    if not folder:
        return None
    if "/folders/" in folder or _DRIVE_ID_RE.match(folder):
        return folder
    return gdrive_mod.ensure_folder(folder)


def _ask_tty(prompt: str, flag: str = "--force") -> bool:
    """Yes/no from the controlling terminal — works even when stdin is piped Markdown. No TTY →
    exit 2 naming `flag` (AGENTS.md: non-TTY callers never hang); a typed "n" → False."""
    try:
        with open("/dev/tty", "r+") as tty:
            tty.write(prompt)
            tty.flush()
            return tty.readline().strip().lower() in ("y", "yes")
    except OSError:
        _die(f"no TTY for confirmation — pass {flag}", code="usage")


def _confirm_overwrite(target: str, new_md: str, force: bool) -> None:
    """Guard an in-place doc overwrite: pull the current doc, show its title + a diff vs the new
    Markdown, and confirm — unless --force. Aborts if declined or there's no TTY. The title is the
    real wrong-id catch (you notice 'Overwrite "Budget 2026"?' when you meant the proposal)."""
    try:
        current = gdrive_mod.export_markdown(target)  # first: a Sheet/Slides id 400s here
        title = gdocs_mod.get_info(target).title
    except HttpError as e:
        if e.resp.status == 400:  # Drive won't export it as markdown: a Sheet/Slides/file id
            _die(f"{target} isn't a Google Doc — `gdocs push` only updates Docs")
        raise
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
    """Markdown → Google Doc: prints the id (stdout) + URL (stderr); with TARGET, re-imports into that
    Doc in place after a diff + confirm. Example: `ko gdocs push proposal.md -f Proposals`."""
    if str(markdown_file) == "-":
        md = sys.stdin.read()
    elif not markdown_file.is_file():
        _die(f"no such file: {markdown_file}", code="usage")
    else:
        md = markdown_file.read_text()
    if not md.strip():
        _die("nothing to push — the Markdown is empty", code="usage")
    if target:
        _confirm_overwrite(target, md, force)
        doc = gdrive_mod.push_markdown(md, "", update_id=target)
        typer.echo(doc)
        typer.echo(f"updated: https://docs.google.com/document/d/{doc}/edit", err=True)
        return
    doc_title = title or (markdown_file.stem if str(markdown_file) != "-" else "Untitled")
    new_id = gdrive_mod.push_markdown(md, doc_title, folder_id=_resolve_folder(folder))
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
        _die(str(e), as_json=as_json)
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
    comment_id: str = typer.Argument(..., help="comment ID (the \\[bracketed] id from `ko gdocs comments`)"),
    text: str = typer.Argument(None, help="reply text (omit to read stdin)"),
) -> None:
    """Reply to a comment thread on a Doc (needs the read+write grant)."""
    body = text if text is not None else sys.stdin.read()
    try:
        r = gdrive_mod.reply_comment(doc, comment_id, body)
    except gdrive_mod.DriveError as e:
        _die(str(e))
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
        _die(str(e))
    typer.echo(f"shaded {n} range(s)", err=True)


# --- cal helpers ---


def _fmt_events(events, window_start: str | None = None) -> None:
    """Print events grouped by day, human-readable, in the local zone.

    `window_start` (YYYY-MM-DD) clamps the grouping date for all-day events that started
    before the requested window — so a multi-day event returned by the API shows under the
    queried day rather than its original start date."""
    from datetime import datetime

    zone = gcal_mod.tz()
    last_day = None
    multi = len({ev.account for ev in events}) > 1
    for ev in events:
        if ev.all_day:
            # Clamp: an ongoing all-day event whose start precedes the window boundary should
            # appear under the first day the user asked about, not the day it originally started.
            raw_day = ev.start
            day = max(raw_day, window_start) if window_start and raw_day < window_start else raw_day
            when = "all day"
        else:
            dt = datetime.fromisoformat(ev.start).astimezone(zone)
            day, when = dt.date().isoformat(), dt.strftime("%H:%M")
        if day != last_day:
            header = datetime.strptime(day, "%Y-%m-%d").strftime("%a %d %b")
            typer.echo(f"\n{header}")
            last_day = day
        loc = f"  @ {ev.location.replace(chr(10), ', ').replace(chr(13), '')}" if ev.location else ""
        where = f"{ev.account} · {ev.calendar_name}" if multi else ev.calendar_name
        typer.echo(f"  {when:>7}  {ev.summary}  [{where}]{loc}")


def _cal_accounts() -> list[str]:
    """Accounts a cal read covers: the one `-a`/KO_GOOGLE_ACCOUNT names, else all signed in.
    Prints them on stderr — which calendars you're looking at is never a guess."""
    one = os.environ.get("KO_GOOGLE_ACCOUNT")
    accts = [one] if one else (gcal_mod.calendar_accounts() or [google_auth.active_account()])
    typer.echo(f"# cal: {', '.join(accts)}", err=True)
    return accts


def _merged_events(fetch, calendar: list[str] | None, as_json: bool):
    """Events across `_cal_accounts()`; per-account failures go to stderr, not the floor."""
    try:
        events, warnings = gcal_mod.merged(fetch, _cal_accounts(), calendar)
    except (gcal_mod.CalError, ValueError) as e:
        _die(str(e), as_json=as_json)
    for w in warnings:
        typer.echo(f"warning: skipped {w}", err=True)
    return events


def _emit_events(events, as_json: bool, window_start: str | None = None) -> bool:
    """JSON or grouped text. Returns False if there were no events (and not JSON).

    `window_start` (YYYY-MM-DD) is forwarded to `_fmt_events` to clamp all-day event headers
    for bounded views like `--today` / `cal day`."""
    if as_json:
        _emit_json(events)
        return True
    if not events:
        return False
    _fmt_events(events, window_start=window_start)
    return True


# --- cal commands ---


@cal_app.callback(invoke_without_command=True)
def _cal_main(
    ctx: typer.Context,
    account: str = _ACCOUNT_OPT,
    days: int = typer.Option(7, "--days", "-d", help="days of agenda (default 7)"),
    today: bool = typer.Option(False, "--today", help="just today"),
    calendar: list[str] = typer.Option(
        None, "--calendar", "-c", help="limit to these calendar(s) by name or id; repeatable"
    ),
    as_json: bool = typer.Option(False, "--json", help="emit JSON (see shape below)"),
) -> None:
    """Agenda across every signed-in account's calendars, merged (bare `ko cal`; `-a` for one).

    `--json` emits an array of events; timed `start`/`end` are ISO8601 in the configured tz
    (with offset), all-day events use bare `YYYY-MM-DD` dates with `all_day: true` and an
    inclusive `end` (a single-day event has end == start)."""
    _set_account(account)
    if ctx.invoked_subcommand is not None:
        _reject_group_flags(
            ctx, days="--days", today="--today", calendar="--calendar", as_json="--json"
        )
        return
    from datetime import datetime as _dt
    n = 1 if today else days
    # For bounded views, clamp all-day event headers to today so ongoing multi-day events
    # don't appear under their original start date header (e.g. a Jul 5–12 event shows
    # under "today" when you run `ko cal --today`, not under "Sun 05 Jul").
    win_start = _dt.now(gcal_mod.tz()).date().isoformat()
    events = _merged_events(
        lambda a, ids: gcal_mod.list_events(days=n, calendar_ids=ids, account=a), calendar, as_json
    )
    if not _emit_events(events, as_json, window_start=win_start):
        typer.echo(f"nothing in the next {n} day(s)", err=True)


@cal_app.command("day")
def cal_day(
    date_str: str = typer.Argument("today", help="YYYY-MM-DD, 'today', or 'tomorrow'"),
    calendar: list[str] = typer.Option(
        None, "--calendar", "-c", help="limit to these calendar(s) by name or id; repeatable"
    ),
    as_json: bool = typer.Option(
        False, "--json", help="emit JSON (timed start/end in the configured tz w/ offset; all-day = bare dates + all_day)"
    ),
) -> None:
    """All events for a single day."""
    from datetime import datetime, timedelta

    try:
        _, val = gcal_mod.parse_when(date_str)
    except ValueError as e:
        _die(str(e), as_json=as_json)
    d = val.date() if isinstance(val, datetime) else val
    start = datetime(d.year, d.month, d.day, tzinfo=gcal_mod.tz())
    events = _merged_events(
        lambda a, ids: gcal_mod.list_events(
            time_min=start, time_max=start + timedelta(days=1), calendar_ids=ids, account=a
        ),
        calendar,
        as_json,
    )
    if not _emit_events(events, as_json, window_start=d.isoformat()):
        typer.echo(f"nothing on {date_str}", err=True)


@cal_app.command("add")
def cal_add(
    title: str = typer.Argument(..., help="event title"),
    when: str = typer.Argument(
        ..., help="'YYYY-MM-DDTHH:MM' (timed) or 'YYYY-MM-DD'/'today'/'tomorrow' (all-day)"
    ),
    end: str = typer.Option(None, "--end", help="end time/date (timed default: +--minutes)"),
    minutes: int = typer.Option(60, "--minutes", "-m", help="duration for a timed event with no --end"),
    cal: str = typer.Option(None, "--cal", help="calendar name or id (default: the `ko cal use` target)"),
    as_json: bool = typer.Option(False, "--json", help="emit the created event as JSON"),
) -> None:
    """Create an event in the `ko cal use` calendar (`-a`/`--cal` override). Timed if WHEN has a
    time (T), else all-day. Prints the event id."""
    try:
        acct, cal_id = gcal_mod.add_target(os.environ.get("KO_GOOGLE_ACCOUNT"))
        if cal:
            cal_id = gcal_mod.resolve_calendar_ids([cal], gcal_mod.list_calendars(acct))[0]
        ev = gcal_mod.create_event(title, when, end=end, calendar_id=cal_id, minutes=minutes, account=acct)
    except (gcal_mod.CalError, ValueError) as e:
        _die(str(e), as_json=as_json)
    typer.echo(f"created: {ev.summary}  {ev.start}  [{acct} · {ev.calendar_id}]", err=True)
    typer.echo(json.dumps(asdict(ev)) if as_json else ev.id)


@cal_app.command("use")
def cal_use(
    account: str = typer.Argument(None, help="account tag new events go to (omit to show the current target)"),
    calendar: str = typer.Argument(None, help="calendar name or id in that account (default: primary)"),
) -> None:
    """Where `cal add` puts new events — set apart from `ko google use`, so switching mail
    accounts doesn't move your events."""
    if account is not None:
        try:
            gcal_mod.set_add_target(account, calendar)
        except gcal_mod.CalError as e:
            _die(str(e))
    acct, cal_id = gcal_mod.add_target()
    typer.echo(f"new events → {acct} · {calendar or cal_id}")


@cal_app.command("find")
def cal_find(
    text: str = typer.Argument(..., help="match event titles (case-insensitive substring)"),
    days: int = typer.Option(60, "--days", "-d", help="how far to look (default 60)"),
    past: bool = typer.Option(
        False, "--past", "-p", help="search the past, not the future ('when was my last X')"
    ),
    calendar: list[str] = typer.Option(
        None, "--calendar", "-c", help="limit to these calendar(s) by name or id; repeatable"
    ),
    as_json: bool = typer.Option(
        False, "--json", help="emit JSON (timed start/end in the configured tz w/ offset; all-day = bare dates + all_day)"
    ),
) -> None:
    """Find events whose title matches TEXT. Forward by default; `--past` for 'when was my last X'."""
    events = _merged_events(
        lambda a, ids: gcal_mod.search_events(text, days=days, past=past, calendar_ids=ids, account=a),
        calendar,
        as_json,
    )
    if past:
        events.reverse()  # merged() sorts ascending; "when was my last X" wants most recent first
    if not _emit_events(events, as_json):
        typer.echo(f"no {'past' if past else 'upcoming'} '{text}' within {days} day(s)", err=True)


@cal_app.command("cals")
def cal_cals(as_json: bool = typer.Option(False, "--json", help="emit JSON")) -> None:
    """List calendars across accounts (TSV: account, id, name, role, primary)."""
    rows = []
    for acct in _cal_accounts():
        try:
            rows += [(acct, c) for c in gcal_mod.list_calendars(acct)]
        except (google_auth.GoogleError, google_auth.AuthError, HttpError, *google_auth.NETWORK_ERRORS) as e:
            typer.echo(f"warning: skipped {acct}: {e}", err=True)
    if as_json:
        typer.echo(json.dumps([{"account": a, **asdict(c)} for a, c in rows]))
        return
    for a, c in rows:
        typer.echo(f"{a}\t{c.id}\t{c.name}\t{c.role}\t{'primary' if c.primary else ''}")


# --- gmail helpers ---


def _short_from(raw: str) -> str:
    """'Name <email>' -> Name (or the email if unnamed)."""
    raw = raw.strip()
    if "<" in raw:
        name = raw.split("<", 1)[0].strip().strip('"')
        return name or raw.split("<", 1)[1].rstrip(">")
    return raw


def _who(raw: str, me: str | None) -> str:
    return "me" if me and gmail_mod.email_of(raw) == me.lower() else _short_from(raw)


def _message_json(meta, body: str) -> dict:
    """One message for --json: every header field, the whole body, and the account it came from."""
    return {"account": google_auth.active_account(), **asdict(meta), "body": body}


def _emit_threads(threads, as_json: bool) -> None:
    """One row per thread: unread mark, thread id, latest date, who (count), subject, then the
    latest snippet. `--json` rows carry the account too."""
    acct = google_auth.active_account()
    if as_json:
        typer.echo(json.dumps([{"account": acct, **asdict(t)} for t in threads], default=str))
        return
    if not threads:
        typer.echo("no threads", err=True)
        return
    me = google_auth.account_status(acct, lookup=False).email
    for t in threads:
        mark = "●" if t.unread else " "
        who = ", ".join(_who(p, me) for p in t.people) or "?"
        count = f" ({t.count})" if t.count > 1 else ""
        typer.echo(f"{mark} {t.id}  {t.date}  {who}{count}  —  {t.subject}")
        if t.snippet:
            typer.echo(f"    {t.snippet[:160].rstrip()}")


def _resolve_body(body: str | None, body_file: Path | None, md: Path | None) -> tuple[str, str | None]:
    """Exactly one of --body / --body-file / --md -> (plain_text, html_or_None). `--md` renders
    HTML for a multipart/alternative message; the others stay plain text/plain."""
    given = [x for x in (body, body_file, md) if x is not None]
    if len(given) != 1:
        _die("pass exactly one of --body / --body-file / --md", code="usage")
    if body is not None:
        return body, None
    src = body_file if body_file is not None else md
    try:
        text = src.read_text()
    except OSError as e:
        _die(f"can't read {src}: {e}")
    if md is not None:
        return text, gmail_mod.md_to_html(text, base_dir=md.parent)
    return text, None


# --- gmail commands ---


@gmail_app.callback(invoke_without_command=True)
def _gmail_main(
    ctx: typer.Context,
    account: str = _ACCOUNT_OPT,
    n: int = typer.Option(10, "-n", "--n", "--max", help="how many threads (default 10)"),
    unread: bool = typer.Option(False, "--unread", help="only threads with unread mail"),
    as_json: bool = typer.Option(False, "--json", help="emit JSON"),
) -> None:
    """Recent inbox threads (bare `ko gmail`).

    `--json` adds `labels` (union of Gmail labelIds) and `list_unsubscribe` (empty on human
    mail) per thread — newsletter-vs-human triage data."""
    _set_account(account)
    typer.echo(f"# gmail: {account_banner()}", err=True)
    if ctx.invoked_subcommand is not None:
        _reject_group_flags(ctx, n="-n", unread="--unread", as_json="--json")
        return
    threads = gmail_mod.recent_threads(n=n, unread=unread)
    _emit_threads(threads, as_json)


@gmail_app.command("search")
def gmail_search(
    query: list[str] = typer.Argument(..., help="Gmail query, e.g. from:alice newer_than:7d"),
    n: int = typer.Option(10, "-n", "--n", "--max", help="how many (default 10)"),
    unread: bool = typer.Option(False, "--unread", help="only unread"),
    as_json: bool = typer.Option(False, "--json", help="emit JSON"),
) -> None:
    """Threads matching Gmail's own query syntax (passed verbatim): `ko gmail search is:unread newer_than:2d`."""
    q = " ".join(query) + (" is:unread" if unread else "")
    threads = gmail_mod.search_threads(q, n=n)
    _emit_threads(threads, as_json)


@gmail_app.command("from")
def gmail_from(
    who: str = typer.Argument(..., help="sender name or email"),
    n: int = typer.Option(10, "-n", "--n", "--max", help="how many (default 10)"),
    unread: bool = typer.Option(False, "--unread", help="only unread"),
    as_json: bool = typer.Option(False, "--json", help="emit JSON"),
) -> None:
    """Recent threads with mail from a person (shortcut for `search from:<who>`)."""
    q = f"from:{who}" + (" is:unread" if unread else "")
    threads = gmail_mod.search_threads(q, n=n)
    _emit_threads(threads, as_json)


@gmail_app.command("view")
def gmail_view(
    msg_id: str = typer.Argument(..., help="message id (from `ko gmail thread`)"),
    full: bool = typer.Option(
        False, "--full", help="print the whole body (default: first ~1500 chars)"
    ),
    as_json: bool = typer.Option(False, "--json", help="emit JSON (headers + whole body + account)"),
) -> None:
    """Read one message: headers + plain-text body."""
    meta, body = gmail_mod.get_message(msg_id)
    if as_json:
        typer.echo(json.dumps(_message_json(meta, body), default=str))
        return
    typer.echo(f"From:    {meta.from_}")
    typer.echo(f"Date:    {meta.date}")
    if meta.cc:
        typer.echo(f"Cc:      {meta.cc}")
    typer.echo(f"Subject: {meta.subject}\n")
    typer.echo(body if full else body[:1500])
    if not full and len(body) > 1500:
        typer.echo(f"\n… {len(body) - 1500} more chars — pass --full", err=True)


@gmail_app.command("thread")
def gmail_thread(
    thread_id: str = typer.Argument(..., help="thread id from a list row (or any message id in it)"),
    full: bool = typer.Option(
        False, "--full", help="print each whole body (default: first ~800 chars per message)"
    ),
    as_json: bool = typer.Option(False, "--json", help="emit a JSON array of messages (whole bodies)"),
) -> None:
    """Read a whole conversation, oldest first; each message shows its id (for `view`, `attach`, `draft reply --msg`)."""
    msgs = gmail_mod.get_thread(thread_id)
    if as_json:
        typer.echo(json.dumps([_message_json(m, b) for m, b in msgs], default=str))
        return
    if not msgs:
        typer.echo("no messages", err=True)
        return
    cap = 800
    for i, (meta, body) in enumerate(msgs):
        if i:
            typer.echo("\n" + "─" * 40 + "\n")
        typer.echo(f"Id:      {meta.id}")
        typer.echo(f"From:    {meta.from_}")
        typer.echo(f"Date:    {meta.date}")
        if meta.cc:
            typer.echo(f"Cc:      {meta.cc}")
        typer.echo(f"Subject: {meta.subject}\n")
        typer.echo(body if full else body[:cap])
        if not full and len(body) > cap:
            typer.echo(f"\n… {len(body) - cap} more chars — pass --full", err=True)


@gmail_app.command("attach")
def gmail_attach(
    msg_id: str = typer.Argument(..., help="message id (from `ko gmail thread`)"),
    n: int = typer.Argument(
        None, help="which attachment to download (1-based; omit in download mode = all)"
    ),
    out: Path = typer.Option(None, "-o", "--out", help="output dir for downloads (default: cwd)"),
    as_json: bool = typer.Option(False, "--json", help="emit the attachment list as JSON"),
) -> None:
    """List a message's attachments, or download them.

    Bare `ko gmail attach <id>` lists (TSV: index, filename, size, mimeType; `--json` for the
    array agents parse). Pass N or -o DIR to download: the Nth attachment, or all if N is
    omitted, to DIR (default cwd). Never overwrites — appends -1, -2, …; prints saved paths.
    Downloads compose with `ko doc <file>` (PDF/Office/image → text)."""
    download = n is not None or out is not None
    if download:
        paths = gmail_mod.download_attachments(msg_id, index=n, out_dir=out)
        for p in paths:
            typer.echo(str(p))
        typer.echo(f"saved {len(paths)} file(s)", err=True)
        return
    atts = gmail_mod.list_attachments(msg_id)
    if not atts:
        _no_results("no attachments", as_json)
    if as_json:
        _emit_json(atts)
        return
    for a in atts:
        typer.echo(f"{a.index}\t{_tsv_cell(a.filename)}\t{a.size}\t{a.mime_type}")


@gmail_draft_app.command("new")
def gmail_draft_new(
    to: str = typer.Option(..., "--to", help="recipient(s), comma-separated"),
    subject: str = typer.Option(..., "--subject", "-s", help="subject line"),
    body: str = typer.Option(None, "--body", help="body text (plain text/plain)"),
    body_file: Path = typer.Option(None, "--body-file", help="read the plain-text body from a file"),
    md: Path = typer.Option(
        None, "--md", help="Markdown file → formatted mail (headings, lists, links, GFM tables, local images inline)"
    ),
    cc: str = typer.Option(None, "--cc", help="cc recipient(s), comma-separated"),
    attach: list[Path] = typer.Option(
        None, "--attach", "-a", exists=True, dir_okay=False,
        help="attach a file (repeatable); keep the total under ~10 MB",
    ),
) -> None:
    """Create a new draft. Prints the draft id (stable across updates). Nothing is sent."""
    text, html_body = _resolve_body(body, body_file, md)
    draft_id = gmail_mod.create_draft(
        to, subject, text, cc=cc or "", html_body=html_body, attachments=attach or None
    )
    typer.echo(draft_id)
    typer.echo(f"draft created — review/send in Gmail, or `ko gmail draft send {draft_id}`", err=True)


@gmail_draft_app.command("reply")
def gmail_draft_reply(
    thread: str = typer.Argument(..., help="thread id to reply in (or any message id in it)"),
    msg: str = typer.Option(
        None, "--msg", help="answer this message in the thread (default: the latest)"
    ),
    body: str = typer.Option(None, "--body", help="reply text (plain text/plain)"),
    body_file: Path = typer.Option(None, "--body-file", help="read the plain-text reply from a file"),
    md: Path = typer.Option(None, "--md", help="Markdown reply → formatted mail (headings, lists, links, GFM tables, local images inline)"),
    all_: bool = typer.Option(False, "--all", help="reply-all (cc the original To+Cc, minus you)"),
    cc: str = typer.Option(
        None, "--cc", help="extra cc recipient(s), comma-separated — added on top of --all's list"
    ),
    attach: list[Path] = typer.Option(
        None, "--attach", "-a", exists=True, dir_okay=False,
        help="attach a file (repeatable); keep the total under ~10 MB",
    ),
    subject: str = typer.Option(
        None, "--subject", "-s", help="override the 'Re: …' subject (a different subject drops threading)"
    ),
) -> None:
    """Draft a reply in a thread, to its latest message (`--msg` picks another); replying to your
    own message goes to its recipients. Prints the draft id. Nothing is sent."""
    text, html_body = _resolve_body(body, body_file, md)
    draft_id, r = gmail_mod.create_reply_draft(
        thread,
        text,
        msg_id=msg,
        html_body=html_body,
        reply_all=all_,
        subject_override=subject,
        extra_cc=cc or "",
        attachments=attach or None,
    )
    typer.echo(draft_id)
    typer.echo(f"reply draft to {r.to} — `ko gmail draft send {draft_id}` to send", err=True)


@gmail_draft_app.command("list")
def gmail_draft_list(
    n: int = typer.Option(10, "-n", "--n", "--max", help="how many (default 10)"),
    as_json: bool = typer.Option(False, "--json", help="emit JSON"),
) -> None:
    """List drafts (TSV: draft id, to, subject, cc)."""
    drafts = gmail_mod.list_drafts(n=n)
    if not drafts:
        _no_results("no drafts", as_json)
    if as_json:
        _emit_json(drafts)
        return
    for d in drafts:
        typer.echo(f"{d.id}\t{_tsv_cell(d.to)}\t{_tsv_cell(d.subject)}\t{_tsv_cell(d.cc)}")


@gmail_draft_app.command("show")
def gmail_draft_show(
    draft_id: str = typer.Argument(..., help="draft id (from `ko gmail draft list`)"),
    as_json: bool = typer.Option(False, "--json", help="emit JSON (headers, body, attachment names)"),
) -> None:
    """Show a draft: headers + plain-text body. Lists attached files (so you can confirm an
    `--attach` stuck) when the draft has any."""
    _id, meta, body, atts = gmail_mod.get_draft(draft_id)
    if as_json:
        typer.echo(json.dumps({"draft_id": _id, **_message_json(meta, body), "attachments": atts}, default=str))
        return
    typer.echo(f"To:      {meta.to}")
    if meta.cc:
        typer.echo(f"Cc:      {meta.cc}")
    if meta.bcc:
        typer.echo(f"Bcc:     {meta.bcc}")
    typer.echo(f"Subject: {meta.subject}")
    if atts:
        typer.echo(f"Attachments: {', '.join(atts)} ({len(atts)})")
    typer.echo("")
    typer.echo(body)


@gmail_draft_app.command("update")
def gmail_draft_update(
    draft_id: str = typer.Argument(..., help="draft id to update"),
    body: str = typer.Option(None, "--body", help="new body text (plain text/plain)"),
    body_file: Path = typer.Option(None, "--body-file", help="read the new plain-text body from a file"),
    md: Path = typer.Option(None, "--md", help="new Markdown body → formatted mail (headings, lists, links, GFM tables, local images inline)"),
    subject: str = typer.Option(None, "--subject", "-s", help="new subject (else keep the existing)"),
    to: str = typer.Option(None, "--to", help="new To recipient(s) (else keep the existing)"),
    cc: str = typer.Option(
        None, "--cc", help="new Cc list, comma-separated — replaces the existing ('' clears it)"
    ),
    force: bool = typer.Option(
        False, "--force", help="update even if the draft has attachments (they WILL be dropped)"
    ),
) -> None:
    """Replace a draft's body (and optionally subject). To/cc/subject/threadId are preserved for
    anything you don't override (the API replaces the whole message). Refuses if the draft has
    attachments — an update drops them — unless --force. Draft id is unchanged."""
    text, html_body = _resolve_body(body, body_file, md)
    gmail_mod.update_draft(
        draft_id,
        text,
        html_body=html_body,
        subject_override=subject,
        to_override=to,
        cc_override=cc,
        force=force,
    )
    typer.echo(f"updated draft {draft_id}", err=True)


@gmail_draft_app.command("send")
def gmail_draft_send(
    draft_id: str = typer.Argument(..., help="draft id to send"),
    yes: bool = typer.Option(False, "--yes", "-y", help="skip the confirm (required when not a TTY)"),
) -> None:
    """Send a draft. OPT-IN: refuses unless `\\[gmail] allow_send = true` in config.toml or
    KO_GMAIL_SEND=1 — drafting is the default; a human sends from Gmail. Confirms first (shows
    to/subject/attachment count). Also refuses if the body says 'attached' but nothing is attached
    (--yes overrides). No scheduled-send API — create the draft, schedule it in Gmail's UI."""
    if not gmail_mod.send_allowed():
        _die(
            "sending is disabled — drafts are the default workflow. Enable with "
            "`[gmail] allow_send = true` in config.toml or KO_GMAIL_SEND=1."
        )
    info = gmail_mod.draft_summary(draft_id)
    if info["mentions_attachment"] and info["attachments"] == 0 and not yes:
        _die(
            "body says 'attached' but the draft has no attachments — attach or pass --yes",
            code="usage",
        )
    if not yes:
        prompt = (
            f"send draft to {info['to']}"
            + (f" cc {info['cc']}" if info["cc"] else "")
            + f" — {info['subject']!r} ({info['attachments']} attachment(s))?"
        )
        if sys.stdin.isatty():
            typer.confirm(prompt, abort=True)
        else:
            typer.echo("refusing to send without a TTY — pass --yes", err=True)
            raise typer.Exit(2)
    sent_id = gmail_mod.send_draft(draft_id)
    typer.echo(sent_id)
    typer.echo("sent", err=True)


# --- drive commands ---


@drive_app.callback()
def _drive_account(
    account: str = _ACCOUNT_OPT,
) -> None:
    """Pick the Google account for this command (one token covers Sheets/Docs/Drive/Calendar)."""
    _set_account(account)


def _drive_file_row(f: gdrive_mod.DriveFile) -> dict:
    return {
        "path": f.rel.as_posix(),
        "id": f.id,
        "mime": f.mime,
        "size": f.size,
        "modified": f.modified.isoformat(),
    }


@drive_app.command("ls")
def drive_ls(
    folder: str = typer.Argument(..., help="Drive folder URL or id"),
    as_json: bool = typer.Option(False, "--json", help="JSON list instead of TSV"),
) -> None:
    """List every file under a folder, recursively. TSV: path, size, modified, id."""
    tree = gdrive_mod.walk(folder)
    for f in tree.unsupported:
        typer.echo(f"skipped {f.rel.as_posix()} ({f.mime.rsplit('.', 1)[-1]}: no export)", err=True)
    if not tree.files:
        _no_results("folder is empty", as_json)
    if as_json:
        typer.echo(json.dumps([_drive_file_row(f) for f in tree.files], indent=2))
        return
    for f in tree.files:
        size = "-" if f.size is None else str(f.size)
        typer.echo(f"{f.rel.as_posix()}\t{size}\t{f.modified:%Y-%m-%d}\t{f.id}")


def _pull_target(target: str, dest: Path | None) -> tuple[str, Path]:
    """`pull <dir>` re-syncs a dir that has a manifest; otherwise target is a folder URL/id and
    dest defaults to a slug of the folder's name in the cwd."""
    p = Path(target)
    if dest is None and (p / gdrive_mod.MANIFEST).exists():
        return gdrive_mod.Manifest.load(p).folder, p
    from .publish import slugify

    return target, dest or Path(slugify(gdrive_mod.folder_name(target)))


def _emit_sync(rows: list[tuple[str, str]], summary: str, as_json: bool, payload: dict) -> None:
    if as_json:
        typer.echo(json.dumps(payload, indent=2))
    else:
        for action, path in rows:
            typer.echo(f"{action}\t{path}")
    typer.echo(summary, err=True)


@drive_app.command("pull")
def drive_pull(
    target: str = typer.Argument(..., help="Drive folder URL/id, or a local dir pulled before"),
    dest: Path = typer.Argument(None, help="local dir (default: ./<folder-name-slug>)"),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="report only; download nothing"),
    force: bool = typer.Option(False, "--force", help="on a both-sides-changed file, Drive wins"),
    as_json: bool = typer.Option(False, "--json", help="JSON result instead of TSV"),
) -> None:
    """Sync a folder down (never deletes; Docs/Sheets/Slides arrive as docx/xlsx/pptx). Example: `ko drive pull <url> data/acme`, later `ko drive pull data/acme`."""
    folder, dest = _pull_target(target, dest)
    res = gdrive_mod.pull(folder, dest, dry_run=dry_run, force=force)
    rows = (
        [("added", f.rel.as_posix()) for f in res.added]
        + [("updated", f.rel.as_posix()) for f in res.updated]
        + [("conflict", f.rel.as_posix()) for f in res.conflicts]
        + [("local-edit", f.rel.as_posix()) for f in res.local_edits]
        + [("local-only", p.as_posix()) for p in res.local_only]
        + [("duplicate", f.rel.as_posix()) for f in res.duplicates]
        + [("unsupported", f.rel.as_posix()) for f in res.unsupported]
    )
    verb = "would download" if dry_run else "downloaded"
    summary = (
        f"{verb} {len(res.added)} new + {len(res.updated)} changed, {len(res.skipped)} unchanged"
        f" → {dest}"
    )
    if res.conflicts:
        summary += f"; {len(res.conflicts)} conflict(s) left alone — --force lets Drive win"
    payload = {
        "dest": str(dest),
        **{k: [f.rel.as_posix() for f in getattr(res, k)] for k in ("added", "updated", "skipped", "conflicts", "local_edits", "duplicates", "unsupported")},
        "local_only": [p.as_posix() for p in res.local_only],
    }
    _emit_sync(rows, summary, as_json, payload)
    if res.conflicts:
        raise typer.Exit(1)


def _push_rows(res: gdrive_mod.PushResult) -> tuple[list[tuple[str, str]], dict]:
    rows = (
        [("create", p.as_posix()) for p in res.created]
        + [("update", p.as_posix()) for p in res.updated]
        + [("conflict", p.as_posix()) for p in res.conflicts]
        + [("remote-edit", p.as_posix()) for p in res.remote_edits]
        + [("native", p.as_posix()) for p in res.native]
    )
    keys = ("created", "updated", "skipped", "conflicts", "remote_edits", "native")
    return rows, {k: [p.as_posix() for p in getattr(res, k)] for k in keys}


@drive_app.command("push")
def drive_push(
    src: Path = typer.Argument(..., help="local dir (pulled before, or a fresh dir + FOLDER)"),
    folder: str = typer.Argument(None, help="Drive folder URL/id (default: the dir's manifest)"),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="show the plan; upload nothing"),
    force: bool = typer.Option(False, "--force", help="on conflicts, local wins (else nothing uploads)"),
    yes: bool = typer.Option(False, "--yes", "-y", help="skip the confirm (required without a TTY)"),
    as_json: bool = typer.Option(False, "--json", help="JSON result instead of TSV"),
) -> None:
    """Upload local changes: new files created, changed ones updated in place (same id + sharing); refuses both-sides-changed unless --force."""
    plan = gdrive_mod.push(src, folder, dry_run=True, force=force)
    rows, payload = _push_rows(plan)
    n = len(plan.created) + len(plan.updated)
    if plan.conflicts:
        _emit_sync(rows, f"{len(plan.conflicts)} conflict(s) — nothing uploaded. Pull first, or --force to make local win", as_json, payload)
        raise typer.Exit(1)
    if dry_run or n == 0:
        _emit_sync(rows, f"would upload {n} file(s) ({len(plan.skipped)} unchanged)" if dry_run else "nothing to upload", as_json, payload)
        return
    if not yes:
        for action, path in rows:
            typer.echo(f"{action}\t{path}", err=True)
        if not _ask_tty(f"Upload {n} file(s) to Drive? [y/N] ", "--yes"):
            _die("aborted — nothing uploaded (pass --yes to skip this check)", code="aborted", as_json=as_json)
    res = gdrive_mod.push(src, folder, force=force)
    rows, payload = _push_rows(res)
    _emit_sync(rows, f"uploaded {len(res.created)} new + {len(res.updated)} changed", as_json, payload)
