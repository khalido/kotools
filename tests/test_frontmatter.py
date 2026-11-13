"""frontmatter.split: the one front-matter parser for `ko pdf`, `ko publish` and `ko prompt`."""

from __future__ import annotations

from ko import frontmatter, prompt


def test_parse_front_matter():
    meta, body = frontmatter.split(
        '---\ntitle: My Page\ndesc: "quoted"\ntags: a, b\n---\n\n# Hi\n'
    )
    assert meta == {"title": "My Page", "desc": "quoted", "tags": "a, b"}
    assert body.startswith("# Hi")
    meta, body = frontmatter.split("# no front matter\n")
    assert meta == {} and body.startswith("# no")


def test_parse_frontmatter_and_body():
    meta, body = frontmatter.split("---\nname: x\ndescription: a brief\n---\n# Title\nbody\n")
    assert meta == {"name": "x", "description": "a brief"}
    assert body == "# Title\nbody\n"


def test_parse_no_frontmatter_passthrough():
    meta, body = frontmatter.split("# Just markdown\nno meta")
    assert meta == {} and body == "# Just markdown\nno meta"


def test_front_matter_one_parser_for_pdf_publish_prompt():
    meta, body = frontmatter.split("---\r\nTitle: A\r\ntags:\r\n  - a\r\n  - b\r\n---\r\n\r\nbody")
    assert meta == {"title": "A", "tags": "a, b"} and body == "body"  # CRLF, list tags, key case
    assert frontmatter.split("﻿---\ndate: 2026-11-09\n---\nx")[0] == {"date": "2026-11-09"}
    assert frontmatter.split("---\ntitle: A\nno closing fence") == ({}, "---\ntitle: A\nno closing fence")


def test_bad_yaml_drops_the_block_keeps_the_body_and_names_the_line(capsys):
    assert frontmatter.split("---\nname: x\ntitle: Phase 2: costs\n---\n# Hi\n", "notes.md") == ({}, "# Hi\n")
    err = capsys.readouterr().err
    assert err.startswith("skipped front matter: notes.md line 3") and 'title: "Phase 2: costs"' in err
    frontmatter.split("---\ntitle: Phase 2: costs\n---\nb", warn=False)
    assert capsys.readouterr().err == ""


def test_values_stay_as_written():
    """YAML 1.1's safe_load gave yes→True, 1.10→1.1, 12:30→750, 0123→83, on→true."""
    meta, _ = frontmatter.split("---\ntitle: yes\nversion: 1.10\ntime: 12:30\ncode: 0123\non: x\n---\nb")
    assert meta == {"title": "yes", "version": "1.10", "time": "12:30", "code": "0123", "on": "x"}


def test_unquoted_hash_drops_the_block_instead_of_cutting_the_value(capsys):
    assert frontmatter.split("---\ntitle: Notes #3\n---\nb", "x.md") == ({}, "b")
    assert 'x.md line 2: " #"' in capsys.readouterr().err
    assert frontmatter.split('---\ntitle: "Notes #3"\n# a comment\n---\nb')[0] == {"title": "Notes #3"}


def test_two_horizontal_rules_are_not_front_matter():
    text = "---\n# hr doc\n\ntext\n\n---\nmore"
    assert frontmatter.split(text) == ({}, text)


def test_a_broken_user_prompt_keeps_its_body(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(prompt.dirs, "config_dir", lambda: tmp_path)
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "bad.md").write_text("---\ntitle: Phase 2: costs\n---\nbody")
    assert "bad" in prompt.names() and capsys.readouterr().err == ""  # completion stays quiet
    assert prompt.get_prompt("bad").body == "body"
    assert "prompts/bad.md line 2" in capsys.readouterr().err


def test_pdf_renders_past_bad_front_matter(tmp_path):
    from typer.testing import CliRunner

    from ko.cli import app

    (tmp_path / "bad.md").write_text("---\ntitle: Phase 2: costs\n---\n# Hi\n")
    r = CliRunner().invoke(app, ["pdf", str(tmp_path / "bad.md"), "--brand", ""])
    assert r.exit_code == 0, r.output
    assert "skipped front matter: bad.md line 2" in r.stderr and (tmp_path / "bad.pdf").exists()


def test_hash_inside_a_block_value_is_kept(capsys):
    """A `|` or `>` value's lines are indented; their " #" is text, not a cut-off comment."""
    text = "---\ntitle: Report\ndescription: |\n  note: see issue #42\nsummary: >\n  x: y #z\n---\nbody\n"
    meta, body = frontmatter.split(text, "r.md")
    assert meta == {"title": "Report", "description": "note: see issue #42\n", "summary": "x: y #z"}
    assert body == "body\n" and capsys.readouterr().err == ""


def test_nested_values_skip_the_block(capsys):
    """A mapping under a key used to reach `ko pdf`'s author meta as "{'name': 'Ko'}"."""
    for text in ("---\nauthor:\n  name: Ko\n---\nbody\n", "---\ntags:\n  - [a, b]\n---\nbody\n"):
        assert frontmatter.split(text, "n.md") == ({}, "body\n")
        assert "n.md" in capsys.readouterr().err
    assert frontmatter.split("---\ntags:\n  - a\n  - b\n---\nbody\n")[0] == {"tags": "a, b"}

