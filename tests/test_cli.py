"""Offline tests for cli.py's shared agent-friendliness helpers.

These encode the contract agents rely on: under --json, stdout stays valid JSON (or empty)
and human-readable notes/errors go to stderr.
"""

from __future__ import annotations

import json

import pytest
import typer

from ko import cli


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
