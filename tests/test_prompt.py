"""Offline tests for ko.prompt — frontmatter parsing, packaged briefs, user override."""

from __future__ import annotations

import pytest

from ko import prompt


def test_packaged_briefs_present():
    names = prompt.names()
    assert "sveltekit-app" in names and "ko-publish-site" in names
    p = prompt.get_prompt("sveltekit-app")
    assert p.source == "packaged" and p.description and p.body.startswith("#")


def test_get_unknown_raises():
    with pytest.raises(KeyError):
        prompt.get_prompt("does-not-exist")


def test_short_tag_resolves_by_prefix_then_substring():
    assert prompt.get_prompt("research").name == "research-papers"  # prefix
    assert prompt.get_prompt("publish").name == "ko-publish-site"  # substring
    assert prompt.get_prompt("exa").name == "exa"  # exact still wins


def test_ambiguous_tag_takes_first_and_notes_others(capsys):
    p = prompt.get_prompt("sveltekit")  # matches -app and -embedded-ai
    assert p.name == "sveltekit-app"
    err = capsys.readouterr().err
    assert "2 briefs match" in err and "sveltekit-embedded-ai" in err


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


def test_a_broken_brief_warns_only_when_it_is_the_one_used(monkeypatch, tmp_path, capsys):
    """`ko sum` resolves the summarize brief; another user brief's bad front matter stays quiet."""
    monkeypatch.setattr(prompt.dirs, "config_dir", lambda: tmp_path)
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "bad.md").write_text("---\ntitle: Phase 2: costs\n---\nbody")
    (tmp_path / "prompts" / "good.md").write_text("---\ndescription: fine\n---\ngood body")
    assert prompt.get_prompt("good").body == "good body"
    assert capsys.readouterr().err == ""
    prompt.get_prompt("bad")
    assert "prompts/bad.md line 2" in capsys.readouterr().err
