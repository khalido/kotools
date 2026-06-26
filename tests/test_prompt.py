"""Offline tests for ko.prompt — frontmatter parsing, packaged briefs, user override."""

from __future__ import annotations

import pytest

from ko import prompt


def test_parse_frontmatter_and_body():
    meta, body = prompt._parse("---\nname: x\ndescription: a brief\n---\n# Title\nbody\n")
    assert meta == {"name": "x", "description": "a brief"}
    assert body == "# Title\nbody\n"


def test_parse_no_frontmatter_passthrough():
    meta, body = prompt._parse("# Just markdown\nno meta")
    assert meta == {} and body == "# Just markdown\nno meta"


def test_packaged_briefs_present():
    names = prompt.names()
    assert "sveltekit-app" in names and "ko-publish-site" in names
    p = prompt.get_prompt("sveltekit-app")
    assert p.source == "packaged" and p.description and p.body.startswith("#")


def test_get_unknown_raises():
    with pytest.raises(KeyError):
        prompt.get_prompt("does-not-exist")


def test_user_dir_overrides_packaged(tmp_path, monkeypatch):
    monkeypatch.setenv("KO_CONFIG_DIR", str(tmp_path))
    pdir = tmp_path / "prompts"
    pdir.mkdir()
    (pdir / "sveltekit-app.md").write_text("---\ndescription: mine\n---\nlocal body\n")
    (pdir / "extra.md").write_text("---\ndescription: local only\n---\nx\n")

    by_name = {p.name: p for p in prompt.list_prompts()}
    assert by_name["sveltekit-app"].source == "user"  # user wins
    assert by_name["sveltekit-app"].body == "local body\n"
    assert by_name["extra"].source == "user"  # local-only brief shows up
    assert "ko-publish-site" in by_name  # packaged still present
