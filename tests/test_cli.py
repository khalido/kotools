"""Offline tests for cli.py's shared agent-friendliness helpers.

These encode the contract agents rely on: under --json, stdout stays valid JSON (or empty)
and human-readable notes/errors go to stderr.
"""

from __future__ import annotations

import json

import pytest
import typer

from ko import cli, cli_ai


def test_die_plain_text_to_stderr(capsys):
    with pytest.raises(typer.Exit) as exc:
        cli._die("boom")
    assert exc.value.exit_code == 1
    out = capsys.readouterr()
    assert out.out == ""  # nothing on stdout — a downstream `| jq` sees clean input
    assert out.err.strip() == "boom"


def test_die_json_error_object_to_stderr(capsys):
    with pytest.raises(typer.Exit):
        cli._die("nope", as_json=True, code="auth")
    out = capsys.readouterr()
    assert out.out == ""
    assert json.loads(out.err) == {"error": "nope", "code": "auth"}


def test_tsv_cell_sanitizes_tabs_and_newlines():
    # a multi-line / tabbed sheet cell must not split the TSV row
    assert cli._tsv_cell("line1\nline2") == "line1\\nline2"
    assert cli._tsv_cell("a\tb") == "a\\tb"
    assert cli._tsv_cell("win\r\nrow") == "win\\nrow"  # CRLF normalized
    assert cli._tsv_cell("plain") == "plain"
    assert cli._tsv_cell(None) == ""
    assert cli._tsv_cell(0) == "0"  # falsy-but-real value preserved
    # the sanitized cell contains no raw tab/newline, so `\t`.join stays one line
    row = "\t".join(cli._tsv_cell(c) for c in ["x\ty", "a\nb", "z"])
    assert "\n" not in row and row.count("\t") == 2


def test_route_trailing_help_becomes_flag():
    assert cli._route(["help"]) == ["--help"]
    assert cli._route(["exa", "search", "help"]) == ["exa", "search", "--help"]
    assert cli._route(["exa", "search", "--help"]) == ["exa", "search", "--help"]  # already a flag


def test_route_url_to_fetch():
    assert cli._route(["https://x.io/a"]) == ["fetch", "https://x.io/a"]
    assert cli._route(["http://x.io"]) == ["fetch", "http://x.io"]


def test_route_file_to_doc(tmp_path):
    f = tmp_path / "paper.pdf"
    f.write_text("x")
    assert cli._route([str(f)]) == ["doc", str(f)]


def test_route_x_and_tt_shortcuts():
    assert cli._route(["x", "ai"]) == ["x", "list", "ai"]
    assert cli._route(["x", "search", "rust"]) == ["x", "search", "rust"]  # real subcommand
    assert cli._route(["tt", "Shopping"]) == ["tt", "items", "Shopping"]
    assert cli._route(["tt", "lists"]) == ["tt", "lists"]


def test_route_publish_up_default():
    assert cli._route(["publish"]) == ["publish", "up"]
    assert cli._route(["publish", "./site"]) == ["publish", "up", "./site"]
    assert cli._route(["publish", "list"]) == ["publish", "list"]  # subcommand left alone
    assert cli._route(["publish", "--help"]) == ["publish", "--help"]


def test_route_known_command_and_flags_untouched():
    assert cli._route(["exa", "search", "rust"]) == ["exa", "search", "rust"]
    assert cli._route(["--version"]) == ["--version"]
    assert cli._route([]) == []


def test_cmd_label_never_captures_arg_values():
    # the privacy contract: only a group + a *validated* subcommand, never an arg value
    assert cli._cmd_label(["exa", "search", "rust"]) == "exa search"
    assert cli._cmd_label(["exa", "notacmd", "rust"]) == "exa"  # bogus sub not echoed
    assert cli._cmd_label(["fetch", "https://secret.example/x"]) == "fetch"  # url not captured
    assert cli._cmd_label(["x", "ai"]) == "x"  # list name not captured
    assert cli._cmd_label(["doctor"]) == "doctor"
    assert cli._cmd_label([]) == "(root)"
    assert cli._cmd_label(["--json"]) == "(root)"  # flags stripped


def test_redact_server_masks_secrets():
    # `ko mcp servers --json` prints env-expanded config; auth-bearing values must be masked
    cfg = {
        "url": "https://api.example.com/mcp",
        "headers": {"Authorization": "Bearer sk-live-abc123", "X-Trace": "keep-me"},
        "env": {"API_KEY": "secret-xyz", "REGION": "us"},
    }
    red = cli_ai._redact_server(cfg)
    assert red["headers"]["Authorization"] == "***"
    assert red["headers"]["X-Trace"] == "keep-me"  # non-secret header untouched
    assert red["env"]["API_KEY"] == "***"
    assert red["env"]["REGION"] == "us"
    assert red["url"] == cfg["url"]  # url passes through
    assert cfg["headers"]["Authorization"] == "Bearer sk-live-abc123"  # original not mutated


def test_die_usage_code_exits_2(capsys):
    # AGENTS.md: usage errors are exit 2, runtime errors exit 1
    with pytest.raises(typer.Exit) as exc:
        cli._die("bad flag", code="usage")
    assert exc.value.exit_code == 2


def test_die_explicit_exit_code_wins(capsys):
    with pytest.raises(typer.Exit) as exc:
        cli._die("boom", code="usage", exit_code=1)
    assert exc.value.exit_code == 1


def test_no_results_plain_note_to_stderr_exit0(capsys):
    with pytest.raises(typer.Exit) as exc:
        cli._no_results("No results for 'x'.", as_json=False)
    assert exc.value.exit_code == 0  # empty is not an error
    out = capsys.readouterr()
    assert out.out == ""  # no stdout in text mode
    assert out.err.strip() == "No results for 'x'."


def test_fmt_day_humanizes_and_drops_time():
    assert cli._fmt_day("2018-06-22T03:54:39.000Z") == "22 Jun 2018"
    assert cli._fmt_day("2016-02-22") == "22 Feb 2016"
    assert cli._fmt_day(None) == ""
    assert cli._fmt_day("garbage") == "garbage"[:10]  # falls back to the date slice


def test_no_results_emits_empty_array_under_json(capsys):
    with pytest.raises(typer.Exit) as exc:
        cli._no_results("No results for 'x'.", as_json=True)
    assert exc.value.exit_code == 0
    out = capsys.readouterr()
    assert json.loads(out.out) == []  # valid empty JSON keeps the pipe alive
    assert out.err.strip() == "No results for 'x'."  # note still on stderr
