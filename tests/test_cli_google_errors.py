"""Error output of the Google commands: every failure exits 1 with one clean stderr line (or a
`{error, code}` object under --json). Written before the per-command `except` blocks were
dropped in favour of `_GoogleTyper`'s net, so it pins that the output didn't change."""

from __future__ import annotations

import json

import httplib2
import pytest
from googleapiclient.errors import HttpError
from typer.testing import CliRunner

from ko import cli_google, google_auth
from ko.cli import app
from ko.gcal import CalError
from ko.gdocs import DocsError
from ko.gdrive import DriveError
from ko.gsheets import SheetsError

_TABS = [cli_google.gdocs_mod.TabInfo(0, "Acme", "t.0", 0)]

# (argv, stdin, the module function that fails, the error class it raises)
_CASES = [
    (["gsheets", "info", "S"], None, ("gsheets_mod", "resolve_sheet"), SheetsError),
    (["gsheets", "tabs", "S"], None, ("gsheets_mod", "resolve_sheet"), SheetsError),
    (["gsheets", "get", "S", "T!A1"], None, ("gsheets_mod", "get_range"), SheetsError),
    (["gsheets", "set", "S", "A1", "x"], None, ("gsheets_mod", "write_values"), SheetsError),
    (["gsheets", "put", "S"], '{"A1": 1}', ("gsheets_mod", "write_ranges"), SheetsError),
    (["gsheets", "find", "S", "x"], None, ("gsheets_mod", "find_cells"), SheetsError),
    (["gsheets", "header", "S", "T!A1:B1"], None, ("gsheets_mod", "format_header"), SheetsError),
    (["gsheets", "add-tab", "S", "T"], None, ("gsheets_mod", "add_tab"), SheetsError),
    (["gsheets", "dup-tab", "S", "T"], None, ("gsheets_mod", "duplicate_tab"), SheetsError),
    (["gsheets", "delete-tab", "S", "T", "-y"], None, ("gsheets_mod", "get_info"), SheetsError),
    (["gsheets", "delete-tab", "S", "T", "-y"], None, ("gsheets_mod", "delete_tab"), SheetsError),
    (["gsheets", "pdf", "S", "T"], None, ("gsheets_mod", "export_pdf"), SheetsError),
    (["gsheets", "insert-rows", "S", "T", "2"], None, ("gsheets_mod", "insert_dimension"), SheetsError),
    (["gsheets", "insert-cols", "S", "T", "B"], None, ("gsheets_mod", "insert_dimension"), SheetsError),
    (["gsheets", "replace", "S", "a", "b", "--all-tabs"], None, ("gsheets_mod", "find_replace"), SheetsError),
    (["gsheets", "new", "T"], None, ("gsheets_mod", "create_spreadsheet"), SheetsError),
    (["gsheets", "clear", "S", "T!A1"], None, ("gsheets_mod", "clear_range"), SheetsError),
    (["gdocs", "tabs", "D"], None, ("gdocs_mod", "list_tabs"), DocsError),
    (["gdocs", "get", "D"], None, ("gdrive_mod", "export_markdown"), DriveError),
    (["gdocs", "get", "D", "--tab", "Acme"], None, ("gdocs_mod", "get_tab_text"), DocsError),
    (["gdocs", "info", "D"], None, ("gdocs_mod", "get_info"), DocsError),
    (["gdocs", "append", "D", "hi", "--tab", "Acme"], None, ("gdocs_mod", "append_text"), DocsError),
    (["gdocs", "append", "D", "hi"], None, ("gdocs_mod", "append_text"), DocsError),
    (["gdocs", "replace", "D", "a", "b", "--tab", "Acme"], None, ("gdocs_mod", "resolve_tab"), DocsError),
    (["gdocs", "replace", "D", "a", "b"], None, ("gdocs_mod", "replace_text"), DocsError),
    (["gdocs", "add-tab", "D", "T"], None, ("gdocs_mod", "add_tab"), DocsError),
    (["gdocs", "comments", "D"], None, ("gdrive_mod", "list_comments"), DriveError),
    (["gdocs", "reply", "D", "C", "hi"], None, ("gdrive_mod", "reply_comment"), DriveError),
    (["gdocs", "shade-table", "D"], None, ("gdocs_mod", "shade_table"), DocsError),
    (["cal"], None, ("gcal_mod", "merged"), CalError),
    (["cal", "day", "today"], None, ("gcal_mod", "merged"), CalError),
    (["cal", "find", "x"], None, ("gcal_mod", "merged"), CalError),
    (["cal", "add", "T", "today"], None, ("gcal_mod", "add_target"), CalError),
    (["cal", "use", "work"], None, ("gcal_mod", "set_add_target"), CalError),
]

# commands whose error honours --json
_JSON = {("gsheets", "info"), ("gsheets", "tabs"), ("gsheets", "get"), ("cal", "find"), ("gsheets", "find"), ("gdocs", "tabs"), ("gdocs", "comments"),
         ("cal", "day"), ("cal", "add")}


def _http_error() -> HttpError:
    return HttpError(httplib2.Response({"status": 400}), b'{"error": {"message": "bad range"}}')


def _run(monkeypatch, argv, stdin, target, exc, as_json=False):
    monkeypatch.setattr(cli_google.gsheets_mod, "resolve_sheet", lambda ref: "SID")
    monkeypatch.setattr(cli_google.gdocs_mod, "list_tabs", lambda doc: _TABS)
    monkeypatch.setattr(cli_google.gsheets_mod, "get_info", lambda sid: cli_google.gsheets_mod.SheetInfo(sid, "Book", ["T"]))
    monkeypatch.setattr(cli_google.gcal_mod, "calendar_accounts", lambda: ["work"])

    def boom(*a, **k):
        raise exc

    monkeypatch.setattr(getattr(cli_google, target[0]), target[1], boom)
    if as_json:
        argv = [*argv, "--json"]
    return CliRunner().invoke(app, argv, input=stdin)


def _stderr_error(r) -> str:
    """The error line: stderr minus cal's `# cal: <accounts>` banner."""
    return "".join(ln for ln in r.stderr.splitlines(keepends=True) if not ln.startswith("# cal:"))


@pytest.mark.parametrize(("argv", "stdin", "target", "cls"), _CASES, ids=lambda v: " ".join(v) if isinstance(v, list) else None)
def test_domain_error_is_one_clean_line(monkeypatch, argv, stdin, target, cls):
    r = _run(monkeypatch, argv, stdin, target, cls("boom"))
    assert r.exit_code == 1, r.output
    assert _stderr_error(r) == "boom\n"
    assert r.stdout == ""


@pytest.mark.parametrize(("argv", "stdin", "target", "cls"), _CASES, ids=lambda v: " ".join(v) if isinstance(v, list) else None)
def test_http_error_is_one_clean_line(monkeypatch, argv, stdin, target, cls):
    r = _run(monkeypatch, argv, stdin, target, _http_error())
    assert r.exit_code == 1, r.output
    assert _stderr_error(r) == "[400] bad range\n"


_JSON_CASES = [c for c in _CASES if tuple(c[0][:2]) in _JSON or c[0] == ["cal"]]


@pytest.mark.parametrize(("argv", "stdin", "target", "cls"), _JSON_CASES)
def test_json_error_shape(monkeypatch, argv, stdin, target, cls):
    r = _run(monkeypatch, argv, stdin, target, cls("boom"), as_json=True)
    assert r.exit_code == 1, r.output
    assert json.loads(_stderr_error(r)) == {"error": "boom", "code": "error"}
    r = _run(monkeypatch, argv, stdin, target, _http_error(), as_json=True)
    assert r.exit_code == 1, r.output
    assert json.loads(_stderr_error(r)) == {"error": "[400] bad range", "code": "error"}
    r = _run(monkeypatch, argv, stdin, target, google_auth.AuthError("token expired", fix="ko google auth"), as_json=True)
    assert r.exit_code == 1, r.output
    assert json.loads(_stderr_error(r)) == {"error": "token expired", "code": "auth", "fix": "ko google auth"}


@pytest.mark.parametrize(("argv", "stdin", "target", "cls"), _CASES, ids=lambda v: " ".join(v) if isinstance(v, list) else None)
def test_auth_error_carries_its_fix(monkeypatch, argv, stdin, target, cls):
    err = google_auth.AuthError("token expired", fix="ko google auth")
    r = _run(monkeypatch, argv, stdin, target, err)
    assert r.exit_code == 1, r.output
    assert _stderr_error(r) == "token expired — run `ko google auth`\n"


@pytest.mark.parametrize(
    ("argv", "stdin", "target"),
    [
        (["gsheets", "set", "S", "A1", "x"], None, ("gsheets_mod", "write_values")),
        (["gsheets", "header", "S", "T!A1:B1"], None, ("gsheets_mod", "format_header")),
        (["cal", "add", "T", "today"], None, ("gcal_mod", "add_target")),
        (["cal", "day", "today"], None, ("gcal_mod", "merged")),
    ],
)
def test_value_error_stays_a_clean_line(monkeypatch, argv, stdin, target):
    """The net doesn't catch ValueError (bad input), so these commands keep their own."""
    r = _run(monkeypatch, argv, stdin, target, ValueError("bad input"))
    assert r.exit_code == 1, r.output
    assert _stderr_error(r) == "bad input\n"


def test_put_rejects_non_object_json(monkeypatch):
    r = CliRunner().invoke(app, ["gsheets", "put", "S"], input="[1, 2]")
    assert r.exit_code == 1
    assert r.stderr.startswith("`put` expects a JSON object")
    r = CliRunner().invoke(app, ["gsheets", "put", "S"], input="{nope")
    assert r.exit_code == 1 and "Expecting property name" in r.stderr
