"""Offline tests for ko.refs — local git only, no network. Clone/pull against
file:// origins created in tmp_path; anything needing the real network is absent."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ko import refs


def _run(cwd: Path, *args: str) -> None:
    subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True)


def _make_origin(tmp_path: Path, name: str = "origin-repo") -> Path:
    """A local git repo with one commit, usable as a clone origin."""
    src = tmp_path / name
    src.mkdir()
    _run(src, "git", "init", "-q", "-b", "main")
    _run(src, "git", "config", "user.email", "t@t")
    _run(src, "git", "config", "user.name", "t")
    (src / "f.txt").write_text("one")
    _run(src, "git", "add", ".")
    _run(src, "git", "commit", "-qm", "c1")
    return src


# --- pure helpers ---


@pytest.mark.parametrize(
    "url, name",
    [
        ("https://github.com/simonw/llm", "llm"),
        ("https://github.com/simonw/llm.git", "llm"),
        ("https://github.com/ueberdosis/tiptap-docs/", "tiptap-docs"),
        ("git@github.com:pydantic/pydantic-ai.git", "pydantic-ai"),
    ],
)
def test_repo_name(url: str, name: str) -> None:
    assert refs.repo_name(url) == name


def test_repo_name_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        refs.repo_name("")


def test_find_repos_splits_git_from_plain_dirs(tmp_path: Path) -> None:
    (tmp_path / "a-repo" / ".git").mkdir(parents=True)
    (tmp_path / "plain").mkdir()
    repos, skipped = refs.find_repos(tmp_path)
    assert repos == ["a-repo"] and skipped == ["plain"]


def test_refs_dir_env_override(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("KO_REFS_DIR", str(tmp_path / "elsewhere"))
    assert refs.refs_dir() == tmp_path / "elsewhere"


# --- extras list (refs.txt) ---


def test_remember_extra_appends_and_dedupes(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("KO_CONFIG_DIR", str(tmp_path))
    url = "https://github.com/owner/thing"
    assert refs.remember_extra(url) is True
    assert refs.remember_extra(url) is False  # already listed
    assert refs.extra_repos() == [url]
    text = refs.extras_file().read_text()
    assert text.startswith("#")  # header comment written once


# --- clone + pull round-trip against a local origin ---


def test_clone_and_pull_detects_head_movement(tmp_path: Path) -> None:
    origin = _make_origin(tmp_path)
    base = tmp_path / "refs"
    base.mkdir()

    res = refs.clone(base, str(origin))
    assert res.ok and (base / origin.name / "f.txt").exists()

    # nothing new -> up to date, no change reported
    (up_to_date,) = list(refs.pull_all(base, [origin.name]))
    assert up_to_date.ok and up_to_date.change is None

    # new commit on origin -> pull reports old -> new
    (origin / "f.txt").write_text("two")
    _run(origin, "git", "add", ".")
    _run(origin, "git", "commit", "-qm", "c2")
    (moved,) = list(refs.pull_all(base, [origin.name]))
    assert moved.ok and moved.change and " -> " in moved.change


def test_pull_reports_failure_with_git_stderr(tmp_path: Path) -> None:
    origin = _make_origin(tmp_path)
    base = tmp_path / "refs"
    base.mkdir()
    refs.clone(base, str(origin))
    # sabotage: remove the origin so pull fails
    import shutil

    shutil.rmtree(origin)
    (failed,) = list(refs.pull_all(base, [origin.name]))
    assert not failed.ok and failed.error


# --- CLAUDE.md: template written once, never overwritten; add appends a stub ---


def test_write_claude_md_never_overwrites(tmp_path: Path) -> None:
    assert refs.write_claude_md(tmp_path) is True
    text = refs.claude_md(tmp_path).read_text()
    assert "Read-only" in text and "- `llm/`" in text  # rules + baked bullets present
    refs.claude_md(tmp_path).write_text("MY ACCUMULATED TAKEAWAYS")
    assert refs.write_claude_md(tmp_path) is False
    assert refs.claude_md(tmp_path).read_text() == "MY ACCUMULATED TAKEAWAYS"


def test_append_claude_entry_stub(tmp_path: Path) -> None:
    refs.claude_md(tmp_path).write_text("# refs\n\n## Repos\n")
    refs.append_claude_entry(tmp_path, "https://github.com/owner/thing")
    text = refs.claude_md(tmp_path).read_text()
    assert "- `thing/` — [owner/thing](https://github.com/owner/thing) — (no deep dive yet)" in text
