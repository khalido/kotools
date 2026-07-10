"""Offline tests for the repo-explorer's read-only file tools (agents/_files.py).

All confined to tmp_path via KO_CODE_DIR; ripgrep-dependent tests skip if rg is absent.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from pydantic_ai import ModelRetry

from ko.agents import _files

needs_rg = pytest.mark.skipif(shutil.which("rg") is None, reason="ripgrep not installed")


@pytest.fixture
def root(monkeypatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("KO_CODE_DIR", str(tmp_path))
    (tmp_path / "proj" / "src").mkdir(parents=True)
    (tmp_path / "proj" / "src" / "main.py").write_text("def hello():\n    return 'world'\n")
    (tmp_path / "proj" / "README.md").write_text("# proj\n")
    return tmp_path


# --- containment: the guard everything routes through ---


def test_dotdot_escape_blocked(root: Path) -> None:
    with pytest.raises(ModelRetry, match="outside"):
        _files.read_file("../somewhere-else")


def test_absolute_path_outside_blocked(root: Path) -> None:
    with pytest.raises(ModelRetry, match="outside"):
        _files.read_file("/etc/hosts")


def test_absolute_path_inside_allowed(root: Path) -> None:
    text = _files.read_file(str(root / "proj" / "README.md"))
    assert "# proj" in text


def test_symlink_escape_blocked(root: Path, tmp_path_factory) -> None:
    outside = tmp_path_factory.mktemp("outside") / "secret.txt"
    outside.write_text("secret")
    (root / "proj" / "sneaky").symlink_to(outside)
    # symlinks are resolved BEFORE the containment check — the escape must fail
    with pytest.raises(ModelRetry, match="outside"):
        _files.read_file("proj/sneaky")


def test_hidden_dirs_blocked(root: Path) -> None:
    (root / "proj" / ".git").mkdir()
    (root / "proj" / ".git" / "config").write_text("x")
    with pytest.raises(ModelRetry, match="hidden"):
        _files.read_file("proj/.git/config")


# --- read_file: caps, binary, miss-with-hint ---


def test_read_file_line_numbers_and_cap(root: Path) -> None:
    (root / "big.txt").write_text("\n".join(f"line{i}" for i in range(500)))
    out = _files.read_file("big.txt", limit=10)
    assert out.startswith("1: line0")
    assert "[+490 more lines — call read_file(path, offset=10)]" in out
    # continuation picks up where the cap left off
    assert _files.read_file("big.txt", offset=10, limit=5).startswith("11: line10")


def test_read_file_binary_placeholder(root: Path) -> None:
    (root / "blob.bin").write_bytes(b"\x00\x01\x02" * 100)
    assert "binary file" in _files.read_file("blob.bin")


def test_read_file_miss_includes_listing(root: Path) -> None:
    with pytest.raises(ModelRetry, match="README.md"):  # the hint lists what IS there
        _files.read_file("proj/nope.py")


# --- list_dir ---


def test_list_dir_dirs_first_hidden_skipped(root: Path) -> None:
    (root / "proj" / ".hidden").mkdir()
    out = _files.list_dir("proj")
    assert out.splitlines()[0] == "src/"
    assert "README.md" in out and ".hidden" not in out


# --- grep / find_files (real ripgrep, local files only) ---


@needs_rg
def test_grep_finds_and_relativizes(root: Path) -> None:
    out = _files.grep("hello", "proj")
    assert "proj/src/main.py:1" in out  # paths relative to the code root


@needs_rg
def test_grep_no_matches(root: Path) -> None:
    assert _files.grep("zzz_nothing_zzz", "proj") == "(no matches)"


@needs_rg
def test_find_files(root: Path) -> None:
    out = _files.find_files("*.py", "proj")
    assert out.strip() == "proj/src/main.py"


# --- refs overview head ---


def test_refs_overview_head_caps_and_points(root: Path, monkeypatch) -> None:
    refs_dir = root / "refs"
    refs_dir.mkdir()
    monkeypatch.setenv("KO_REFS_DIR", str(refs_dir))
    (refs_dir / "CLAUDE.md").write_text("\n".join(f"l{i}" for i in range(200)))
    out = _files.refs_overview_head()
    assert out.startswith("l0")
    assert "[+50 more lines — read_file('refs/CLAUDE.md')]" in out
