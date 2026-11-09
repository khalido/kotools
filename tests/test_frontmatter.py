"""frontmatter.split: the one front-matter parser for `ko pdf`, `ko publish` and `ko prompt`."""

from __future__ import annotations

import pytest

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


def test_front_matter_bad_yaml_stops_with_the_file_and_line():
    with pytest.raises(frontmatter.FrontMatterError, match=r'notes\.md line 3: .*title: "Phase 2: costs"'):
        frontmatter.split("---\nname: x\ntitle: Phase 2: costs\n---\nbody", "notes.md")


def test_values_stay_as_written():
    """YAML 1.1's safe_load gave yes→True, 1.10→1.1, 12:30→750, 0123→83, on→true."""
    meta, _ = frontmatter.split("---\ntitle: yes\nversion: 1.10\ntime: 12:30\ncode: 0123\non: x\n---\nb")
    assert meta == {"title": "yes", "version": "1.10", "time": "12:30", "code": "0123", "on": "x"}


def test_unquoted_hash_stops_instead_of_cutting_the_value():
    with pytest.raises(frontmatter.FrontMatterError, match=r'x\.md line 2: " #"'):
        frontmatter.split("---\ntitle: Notes #3\n---\nb", "x.md")
    assert frontmatter.split('---\ntitle: "Notes #3"\n# a comment\n---\nb')[0] == {"title": "Notes #3"}


def test_two_horizontal_rules_are_not_front_matter():
    text = "---\n# hr doc\n\ntext\n\n---\nmore"
    assert frontmatter.split(text) == ({}, text)


def test_one_broken_user_prompt_stops_only_its_own_lookup(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(prompt.dirs, "config_dir", lambda: tmp_path)
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "bad.md").write_text("---\ntitle: Phase 2: costs\n---\nbody")
    assert prompt.get_prompt("decide").name == "decide"  # others still resolve
    assert "bad" not in prompt.names() and capsys.readouterr().err == ""  # completion stays quiet
    prompt.list_prompts()
    err = capsys.readouterr().err
    assert err.startswith("skipped:") and "prompts/bad.md line 2" in err
    with pytest.raises(frontmatter.FrontMatterError, match="bad.md line 2"):
        prompt.get_prompt("bad")
