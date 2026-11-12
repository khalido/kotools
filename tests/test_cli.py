"""Offline tests for cli.py's shared agent-friendliness helpers.

These encode the contract agents rely on: under --json, stdout stays valid JSON (or empty)
and human-readable notes/errors go to stderr.
"""

from __future__ import annotations

import json

import pytest
import typer

from ko import _cli_shared, cli, cli_ai


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
    assert cli._route(["exa", "help"]) == ["exa", "--help"]  # group level
    # nested groups too — matching only args[-2] against top-level names missed these
    assert cli._route(["gmail", "draft", "help"]) == ["gmail", "draft", "--help"]
    assert cli._route(["agent", "sessions", "help"]) == ["agent", "sessions", "--help"]
    assert cli._route(["exa", "search", "--help"]) == [
        "exa",
        "search",
        "--help",
    ]  # already a flag


def test_route_trailing_help_after_leaf_stays_literal():
    """After a leaf command `help` is an ordinary argument, so it can be searched for.
    The rewrite used to fire at any depth, which made the word unreachable at every
    spelling — quoting didn't help, since _route sees the same argv either way."""
    assert cli._route(["hn", "search", "help"]) == ["hn", "search", "help"]
    assert cli._route(["exa", "search", "help"]) == ["exa", "search", "help"]
    # a query word that happens to name a group must not resurrect the rewrite: the
    # check matches the whole prefix against real group paths, not just the last token
    assert cli._route(["hn", "search", "agent", "help"]) == [
        "hn",
        "search",
        "agent",
        "help",
    ]


def test_group_paths_covers_nested_and_root():
    paths = cli._group_paths()
    assert () in paths  # bare `ko help`
    assert ("gmail",) in paths and ("gmail", "draft") in paths
    assert ("agent", "sessions") in paths
    assert ("doctor",) not in paths  # a leaf command, not a group


def test_reject_group_flags_is_live(capsys):
    """A bare-group flag passed before a subcommand must be a loud usage error.

    Guards the *mechanism*, not just the policy: the first version of this check compared
    `ctx.get_parameter_source(...)` against `click.core.ParameterSource.COMMANDLINE`, but
    Typer vendors its own click, so that enum member is a different class object and `==`
    was silently always False — a dead check that read as correct. Comparing by name is
    what makes it fire, and this test fails if anyone reverts to identity comparison.
    """

    class _Ctx:
        command_path = "ko gmail"
        invoked_subcommand = "search"

        def get_parameter_source(self, name):
            # mimics typer._click's enum: right name, foreign class
            return type("ParameterSource", (), {"name": "COMMANDLINE"})()

    with pytest.raises(typer.Exit) as exc:
        _cli_shared._reject_group_flags(_Ctx(), as_json="--json")
    assert exc.value.exit_code == 2  # usage
    err = capsys.readouterr().err
    assert json.loads(err)["code"] == "usage"  # --json asked for -> JSON error object


def test_reject_group_flags_raises_on_unknown_param():
    """A typo'd/renamed param name must be loud, not a silently dead guard: click's
    get_parameter_source returns None for a name it doesn't know, which would otherwise
    read as 'not passed on the command line' and restore the accept-and-ignore bug."""

    class _Ctx:
        command_path = "ko gmail"
        invoked_subcommand = "search"

        def get_parameter_source(self, name):
            return None  # click doesn't know this parameter

    with pytest.raises(RuntimeError, match="no parameter"):
        _cli_shared._reject_group_flags(_Ctx(), typoed="--json")


def test_reject_group_flags_allows_defaults():
    """Flags left at their default must pass through untouched."""

    class _Ctx:
        command_path = "ko cal"
        invoked_subcommand = "find"

        def get_parameter_source(self, name):
            return type("ParameterSource", (), {"name": "DEFAULT"})()

    _cli_shared._reject_group_flags(_Ctx(), as_json="--json", days="--days")  # no raise


def test_route_url_to_fetch():
    assert cli._route(["https://x.io/a"]) == ["fetch", "https://x.io/a"]
    assert cli._route(["http://x.io"]) == ["fetch", "http://x.io"]


def test_route_file_to_doc(tmp_path):
    f = tmp_path / "paper.pdf"
    f.write_text("x")
    assert cli._route([str(f)]) == ["doc", str(f)]


def test_route_x_and_tt_shortcuts():
    assert cli._route(["x", "ai"]) == ["x", "list", "ai"]
    assert cli._route(["x", "search", "rust"]) == [
        "x",
        "search",
        "rust",
    ]  # real subcommand
    assert cli._route(["tt", "Shopping"]) == ["tt", "items", "Shopping"]
    assert cli._route(["tt", "lists"]) == ["tt", "lists"]


def test_route_publish_up_default():
    assert cli._route(["publish"]) == ["publish", "up"]
    assert cli._route(["publish", "./site"]) == ["publish", "up", "./site"]
    assert cli._route(["publish", "list"]) == [
        "publish",
        "list",
    ]  # subcommand left alone
    assert cli._route(["publish", "--help"]) == ["publish", "--help"]


def test_route_known_command_and_flags_untouched():
    assert cli._route(["exa", "search", "rust"]) == ["exa", "search", "rust"]
    assert cli._route(["--version"]) == ["--version"]
    assert cli._route([]) == []


def test_cmd_label_never_captures_arg_values():
    # the privacy contract: only a group + a *validated* subcommand, never an arg value
    assert cli._cmd_label(["exa", "search", "rust"]) == "exa search"
    assert cli._cmd_label(["exa", "notacmd", "rust"]) == "exa"  # bogus sub not echoed
    assert (
        cli._cmd_label(["fetch", "https://secret.example/x"]) == "fetch"
    )  # url not captured
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
    assert (
        cfg["headers"]["Authorization"] == "Bearer sk-live-abc123"
    )  # original not mutated


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


def test_die_puts_code_and_message_on_the_log_event(capsys):
    from ko import logs

    logs.reset()
    with pytest.raises(typer.Exit):
        cli._die("no sheet named Budget", code="not_found", fix="ko gsheets ls")
    assert logs.get("error") == "not_found"
    assert logs.get("error_msg") == "no sheet named Budget"
    assert logs.get("fix") == "ko gsheets ls"
    logs.reset()
    with pytest.raises(typer.Exit):
        cli._die("something broke")
    assert logs.get("error") == "error"  # default code
    logs.reset()


def test_crash_site_names_the_innermost_ko_frame():
    try:
        cli._cmd_label(None)  # TypeError raised inside cli.py
    except TypeError as e:
        site = cli._crash_site(e)
    assert site.startswith("cli.py:") and site.endswith("in _cmd_label")


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


def test_publish_list_json_mixes_dataclasses_and_cf_rows(monkeypatch, tmp_path, capsys):
    """`ko publish list --json` must emit valid JSON when the registry has rows —
    regression: dataclass rows were double-asdict'ed through _emit_json."""
    monkeypatch.setenv("KO_STATE_DIR", str(tmp_path))
    from ko import publish

    folder = tmp_path / "site"
    folder.mkdir()
    publish._record(folder, "site", "https://site.example.com")
    monkeypatch.setattr(publish, "account_workers", lambda: ["site", "elsewhere"])
    monkeypatch.setattr(
        publish, "worker_domains", lambda: {"elsewhere": "elsewhere.example.com"}
    )
    cli_ai.publish_list(cf=True, as_json=True)
    out = capsys.readouterr()
    rows = json.loads(out.out)
    assert [r["name"] for r in rows] == ["site", "elsewhere"]
    assert (
        rows[1]["url"] == "https://elsewhere.example.com"
    )  # cf-only row, domain resolved


def test_count_flags_accept_both_n_forms():
    """Agents guess either `-n` or `--n`; every count flag takes both (gmail keeps --max too).
    `cal --days` stays a window, not a count."""
    import typer as _typer

    from ko.cli import app

    root = _typer.main.get_command(app)

    def opts_for(path: list[str]) -> set[str]:
        cmd = root
        for part in path:
            cmd = cmd.commands[part]
        return {o for p in cmd.params for o in p.opts}

    for path in (["hn", "top"], ["papers", "search"], ["logs"], ["exa", "search"]):
        o = opts_for(path)
        assert {"-n", "--n"} <= o, (path, o)
    gmail = opts_for(["gmail"])
    assert {"-n", "--n", "--max"} <= gmail  # --max kept as a back-compat alias
    assert {"-d", "--days"} <= opts_for(["cal"])  # a window, deliberately not -n


def test_bare_tt_and_todo_alias_dispatch_to_lists():
    """House style: a bare group does the useful thing. `ko tt` / `ko todo` -> lists;
    `ko todo Shopping` -> items Shopping."""
    assert cli._route(["tt"]) == ["tt", "lists"]
    assert cli._route(["todo"]) == ["todo", "lists"]
    assert cli._route(["todo", "Shopping"]) == ["todo", "items", "Shopping"]
    assert cli._route(["tt", "lists"]) == ["tt", "lists"]  # explicit subcommand untouched
    assert cli._route(["tt", "--help"]) == ["tt", "--help"]


def test_web_convention_aliases_share_one_callback():
    """`ko websearch`/`ko webfetch` are the ecosystem spelling (Claude Code's WebSearch/
    WebFetch, the API's web_search/web_fetch) for commands whose primary names are
    vendor/verb-first. Aliases, not copies — one implementation, two spellings."""
    by_name = {c.name: c.callback for c in cli.app.registered_commands}
    assert by_name["websearch"] is not None
    assert by_name["webfetch"] is by_name["fetch"]
    # exa search stays the primary spelling inside the group, sharing websearch's callback
    exa_search = {c.name: c.callback for c in cli.cli_web.exa_app.registered_commands}["search"]
    assert by_name["websearch"] is exa_search


def test_route_leaves_the_web_aliases_alone(tmp_path):
    """A file named `webfetch` in cwd must not shadow the command (known names win)."""
    assert cli._route(["webfetch", "https://x.com"]) == ["webfetch", "https://x.com"]
    assert cli._route(["websearch", "some query"]) == ["websearch", "some query"]


def test_startup_skips_heavy_imports():
    """Every `ko` command imports ko.cli. HTTP clients, Google's auth transport and the arxiv
    SDK load on first use, not here (~80 ms per command). A module-level import in a module
    whose constant feeds a flag default brings them back; this names it."""
    import subprocess
    import sys

    heavy = ["httpx2", "requests", "httplib2", "arxiv", "googleapiclient.discovery",
             "google.auth.transport.requests", "google_auth_oauthlib", "pydantic_ai"]
    code = f"import sys, ko.cli; print(' '.join(m for m in {heavy!r} if m in sys.modules))"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == ""


def test_main_missing_key_is_one_clean_line(monkeypatch, tmp_path, capsys):
    """An unset key that escapes a command's own net still exits 1 with one line, no
    traceback (AGENTS.md), and `--json` gets `{error, code: config}`."""
    import json

    from ko import cli, config

    monkeypatch.setenv("KO_STATE_DIR", str(tmp_path))

    def boom():
        raise config.MissingKeyError("EXA_API_KEY is not set")

    monkeypatch.setattr(cli, "app", boom)
    monkeypatch.setattr(cli, "_route", lambda argv: argv)  # it reads the real app's groups
    monkeypatch.setattr(cli, "_cmd_label", lambda argv: "exa search")
    for argv, check in (
        (["ko", "exa", "search", "x"], lambda err: err.strip() == "EXA_API_KEY is not set"),
        (["ko", "exa", "search", "x", "--json"],
         lambda err: json.loads(err) == {"error": "EXA_API_KEY is not set", "code": "config"}),
    ):
        monkeypatch.setattr("sys.argv", argv)
        with pytest.raises(SystemExit) as exc:
            cli.main()
        assert exc.value.code == 1
        captured = capsys.readouterr()
        assert captured.out == ""  # stdout stays clean for a pipe
        assert check(captured.err)
