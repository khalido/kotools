"""Read-only file tools for the repo-explorer agent — confined to ~/code.

Read-only **by construction**: no write/edit tools exist at all — stronger than
guarding writes (nothing to misuse, nothing in the model's tool list to waste
tokens on). Containment ported from thinker's `resolve_in` (resolve BOTH sides,
then `is_relative_to` — symlinks resolved before the check, the same TOCTTOU
ordering pydantic-ai-harness uses). Search shells out to ripgrep.

Every tool returns small, capped output (a cheap model drowns in dumps):
line-capped reads with continuation pointers, result-capped grep/find,
binary detection, hidden dirs (.git etc.) skipped everywhere.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from pydantic_ai import ModelRetry

from ko import config

MAX_READ_LINES = 400
MAX_GREP_LINES = 100
MAX_LIST_ENTRIES = 200
MAX_FIND_RESULTS = 200
OVERVIEW_HEAD_LINES = 150  # refs/CLAUDE.md head injected into instructions


def code_root() -> Path:
    """The explorable root: KO_CODE_DIR → `[code] dir` in config.toml → ~/code."""
    return Path(
        config.setting("KO_CODE_DIR", "code", "dir", str(Path.home() / "code"))
    ).expanduser()


def _resolve(path: str) -> Path:
    """THE containment guard — every path-taking tool routes through here.

    Accepts paths relative to the code root ('refs/llm/setup.py') or absolute
    ones that land inside it. Both sides are fully resolved (symlinks followed)
    BEFORE the containment check, so a symlink pointing outside can't escape."""
    root = code_root().resolve()
    p = Path(path).expanduser()
    full = (p if p.is_absolute() else root / p).resolve()
    if not full.is_relative_to(root):
        raise ModelRetry(
            f"'{path}' is outside {root} — this agent only reads inside it. "
            f"Use paths relative to that root, e.g. 'everx' or 'refs/pydantic-ai'."
        )
    if any(part.startswith(".") for part in full.relative_to(root).parts):
        raise ModelRetry(
            f"'{path}' is under a hidden directory (.git etc.) — those are off-limits."
        )
    return full


def list_dir(path: str = ".") -> str:
    """Entries in a directory: dirs first with a trailing '/', file sizes shown.
    Hidden entries skipped; capped."""
    d = _resolve(path)
    if not d.is_dir():
        raise ModelRetry(f"'{path}' is not a directory. Try read_file, or list its parent.")
    entries = sorted(
        (p for p in d.iterdir() if not p.name.startswith(".")),
        key=lambda p: (p.is_file(), p.name.lower()),
    )
    lines = [
        f"{e.name}/" if e.is_dir() else f"{e.name}  ({e.stat().st_size:,}B)"
        for e in entries[:MAX_LIST_ENTRIES]
    ]
    if len(entries) > MAX_LIST_ENTRIES:
        lines.append(f"[+{len(entries) - MAX_LIST_ENTRIES} more entries]")
    return "\n".join(lines) or "(empty)"


def read_file(path: str, offset: int = 0, limit: int = MAX_READ_LINES) -> str:
    """A file's text with line numbers (cite as file:line). Line-capped; call again
    with `offset` to continue. Binary files return a placeholder, missing ones a
    listing of what IS there (thinker's self-correction pattern)."""
    f = _resolve(path)
    if not f.is_file():
        parent = f.parent
        hint = list_dir(str(parent.relative_to(code_root().resolve()) or ".")) if parent.is_dir() else "(parent missing too)"
        raise ModelRetry(f"'{path}' does not exist. In its directory:\n{hint}")
    raw = f.read_bytes()
    if b"\x00" in raw[:8192]:
        return f"(binary file, {len(raw):,} bytes — not readable as text)"
    lines = raw.decode("utf-8", errors="replace").splitlines()
    window = lines[offset : offset + limit]
    out = [f"{offset + i + 1}: {ln}" for i, ln in enumerate(window)]
    remaining = len(lines) - offset - len(window)
    if remaining > 0:
        out.append(f"[+{remaining} more lines — call read_file(path, offset={offset + limit})]")
    return "\n".join(out) or "(empty file)"


def grep(pattern: str, path: str = ".", glob: str | None = None) -> str:
    """Regex search file CONTENTS via ripgrep (smart-case, respects .gitignore).
    Returns `file:line: text`, capped — narrow the pattern/path/glob when truncated."""
    d = _resolve(path)
    cmd = ["rg", "-n", "--no-heading", "-S", "--max-columns", "300"]
    if glob:
        cmd += ["-g", glob]
    cmd += [pattern, str(d)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        raise ModelRetry("ripgrep (rg) is not installed on this machine — use find_files + read_file instead.")
    if proc.returncode not in (0, 1):  # 1 = no matches; anything else is a real error
        raise ModelRetry(f"search failed: {proc.stderr.strip()[:200]}")
    root = str(code_root().resolve()) + "/"
    lines = [ln.replace(root, "", 1) for ln in proc.stdout.splitlines()]
    if not lines:
        return "(no matches)"
    out = lines[:MAX_GREP_LINES]
    if len(lines) > MAX_GREP_LINES:
        out.append(f"[+{len(lines) - MAX_GREP_LINES} more matches — narrow the pattern, path, or glob]")
    return "\n".join(out)


def find_files(glob: str, path: str = ".") -> str:
    """Find files by NAME glob (e.g. '*.py', '**/test_*.ts') via `rg --files`
    (respects .gitignore). Capped; paths relative to the code root."""
    d = _resolve(path)
    try:
        proc = subprocess.run(
            ["rg", "--files", "-g", glob, str(d)], capture_output=True, text=True, timeout=30
        )
    except FileNotFoundError:
        raise ModelRetry("ripgrep (rg) is not installed — use list_dir to navigate instead.")
    root = str(code_root().resolve()) + "/"
    lines = [ln.replace(root, "", 1) for ln in proc.stdout.splitlines()]
    if not lines:
        return "(no files match)"
    out = lines[:MAX_FIND_RESULTS]
    if len(lines) > MAX_FIND_RESULTS:
        out.append(f"[+{len(lines) - MAX_FIND_RESULTS} more — narrow the glob or path]")
    return "\n".join(out)


def refs_overview_head() -> str:
    """The first OVERVIEW_HEAD_LINES of refs/CLAUDE.md (the curated map of the
    reference repos + accumulated takeaways), with a pointer to the rest —
    thinker's memory-head pattern. Injected into the repo agent's instructions."""
    from ko import refs

    f = refs.claude_md(refs.refs_dir())
    try:
        lines = f.read_text().splitlines()
    except OSError:
        return "(no refs/CLAUDE.md yet — run `ko refs setup`)"
    head = lines[:OVERVIEW_HEAD_LINES]
    if len(lines) > OVERVIEW_HEAD_LINES:
        rel = f.resolve().relative_to(code_root().resolve())
        head.append(f"[+{len(lines) - OVERVIEW_HEAD_LINES} more lines — read_file('{rel}')]")
    return "\n".join(head)
