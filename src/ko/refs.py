"""Manage ~/code/refs — third-party repos cloned read-only for ideas and patterns.

The refs DIR is the source of truth for pull/list (they just glob it). The repo
LIST only matters for bootstrap: `BAKED_REPOS` below is the opinionated default
(travels with ko), and `~/.config/ko/refs.txt` (one URL per line, ko-owned, safe
to append, dotfile-synced) holds machine-local additions — `ko refs add` clones
AND appends there, so `ko refs setup` on a new machine restores everything.
The folder's CLAUDE.md carries per-repo takeaways; never a clone list.

Pull logic ported from dotfiles/gitupdateall.py: parallel `git pull --ff-only
--prune`, worktrees sharing a `--git-common-dir` grouped serially (lock races),
"updated" = HEAD actually moved (a fetched tag alone is not an update).
"""

from __future__ import annotations

import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from ko import config
from ko.dirs import config_dir

DEFAULT_JOBS = 8

# (name, url, note) — the opinionated default set, cloned by `ko refs setup`.
BAKED_REPOS: list[tuple[str, str, str]] = [
    ("autoresearch", "https://github.com/karpathy/autoresearch", "Karpathy's automated research framework — one file, one metric, autonomous loop"),
    ("pi-autoresearch", "https://github.com/davebcn87/pi-autoresearch", "autoresearch harness for pi-agent — JSONL experiment log, living md doc, isBetter()"),
    ("pi", "https://github.com/earendil-works/pi", "pi agent"),
    ("tiptap", "https://github.com/ueberdosis/tiptap", "official Tiptap monorepo — source of truth for editor wiring"),
    ("tiptap-docs", "https://github.com/ueberdosis/tiptap-docs", "Tiptap docs source — check before guessing behaviour"),
    ("Edra", "https://github.com/Tsuzat/Edra", "Svelte Tiptap editor"),
    ("shadcn-svelte", "https://github.com/huntabyte/shadcn-svelte", "shadcn ported to Svelte"),
    ("sveltekit-mcp-starter", "https://github.com/axel-rock/sveltekit-mcp-starter", "SvelteKit MCP starter"),
    ("pydantic-ai", "https://github.com/pydantic/pydantic-ai", "pydantic-ai framework source"),
    ("pydantic-deepagents", "https://github.com/vstorm-co/pydantic-deepagents", "deep-agents patterns on pydantic-ai"),
    ("hermes-agent", "https://github.com/NousResearch/hermes-agent", "Nous agent harness"),
    ("llm", "https://github.com/simonw/llm", "simonw's llm CLI — CLI + plugin design ideas"),
    ("ai", "https://github.com/vercel/ai", "Vercel AI SDK"),
    ("chat", "https://github.com/vercel/chat", "Vercel chat SDK"),
    ("eve", "https://github.com/vercel/eve", "Vercel eve"),
    ("openclaw", "https://github.com/openclaw/openclaw", "openclaw"),
    ("gogcli", "https://github.com/steipete/gogcli", "gogcli"),
    ("grammY", "https://github.com/grammyjs/grammY", "Telegram bot framework"),
    ("MiroFish", "https://github.com/666ghj/MiroFish", "MiroFish"),
    ("sharehtml", "https://github.com/jonesphillip/sharehtml", "sharehtml"),
    ("toad", "https://github.com/batrachianai/toad", "toad"),
]

CLAUDE_TEMPLATE = """\
# ~/code/refs

Reference repos cloned from other people for ideas and patterns. Not my own projects —
managed by `ko refs` (pull/add/list).

**Rules:**
- Read-only — never modify ref code
- `ko refs pull` (or `git pull`) before a deep dive — repos may have moved
- Don't copy patterns wholesale — extract what's relevant to our context
- After a deep dive, add takeaways as sub-bullets under the repo's entry below

**How to explore (for agents):**
- Survey cheap first: use fast/cheap subagents (Explore agents, haiku-tier models) to map
  a repo — layout, where the interesting code lives, which files embody the pattern
  you're after. Bring only those few files into main context for the careful read.
- You're here for ideas, not to build these repos: skip CI configs, lockfiles, and
  generated code; read tests only when the tests are the best documentation of behaviour.
- Prefer the repo's own docs/ and examples/ as the entry point over grepping cold.
- Record what you learned: takeaways go under the repo entry so the next session starts
  from them instead of re-reading the repo.

## Repos

{repo_bullets}
"""


@dataclass
class Result:
    repo: str
    ok: bool
    change: str | None = None  # "old -> new" when HEAD moved
    error: str = ""


def refs_dir() -> Path:
    """The refs directory: KO_REFS_DIR → `[refs] dir` in config.toml → ~/code/refs."""
    return Path(
        config.setting("KO_REFS_DIR", "refs", "dir", str(Path.home() / "code" / "refs"))
    ).expanduser()


# --- the machine-local extras list (~/.config/ko/refs.txt) ---


def extras_file() -> Path:
    return config_dir() / "refs.txt"


def extra_repos() -> list[str]:
    """Machine-local repo URLs from refs.txt (one per line; #-comments and blanks skipped)."""
    try:
        lines = extras_file().read_text().splitlines()
    except OSError:
        return []
    return [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("#")]


def remember_extra(url: str) -> bool:
    """Append a URL to refs.txt (created on first use) so `setup` restores it on any
    machine. Returns False if it was already listed."""
    if url in extra_repos():
        return False
    f = extras_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    header = "" if f.exists() else "# machine-local ko refs — `ko refs add <url>` appends here; setup clones these\n"
    with f.open("a") as fh:
        fh.write(f"{header}{url}\n")
    return True


def repo_name(url: str) -> str:
    """Repo folder name from a git URL: last path segment, .git stripped."""
    name = url.rstrip("/").removesuffix(".git").rsplit("/", 1)[-1]
    if not name or ":" in name:
        raise ValueError(f"cannot derive a repo name from {url!r}")
    return name


# --- git plumbing (ported from gitupdateall.py) ---


def _git(base: Path, repo: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(base / repo), *args], capture_output=True, text=True
    )


def is_git_repo(d: Path) -> bool:
    # .git is a directory for normal clones, a file for worktrees/submodules.
    return (d / ".git").exists()


def find_repos(base: Path) -> tuple[list[str], list[str]]:
    """(git repos, non-git dirs) directly under base, sorted."""
    repos, skipped = [], []
    for d in sorted(p for p in base.iterdir() if p.is_dir()):
        (repos if is_git_repo(d) else skipped).append(d.name)
    return repos, skipped


def _common_dir(base: Path, repo: str) -> str:
    # Worktrees of the same repo share one object/ref store (--git-common-dir);
    # pulling them concurrently races on shared lock files, so group by it.
    raw = _git(base, repo, "rev-parse", "--git-common-dir").stdout.strip() or repo
    return str((base / repo / raw).resolve())


def _group_by_store(base: Path, repos: list[str]) -> list[list[str]]:
    groups: dict[str, list[str]] = {}
    for repo in repos:
        groups.setdefault(_common_dir(base, repo), []).append(repo)
    return list(groups.values())


def describe(base: Path, repo: str) -> str:
    """`git describe --tags --always` — short hash for tagless repos."""
    return _git(base, repo, "describe", "--tags", "--always").stdout.strip() or "?"


def origin_url(base: Path, repo: str) -> str:
    return _git(base, repo, "remote", "get-url", "origin").stdout.strip() or "?"


def pull_one(base: Path, repo: str) -> Result:
    head = _git(base, repo, "rev-parse", "HEAD").stdout.strip()
    label = describe(base, repo)
    proc = _git(base, repo, "pull", "--ff-only", "--prune")
    if proc.returncode != 0:
        lines = (proc.stdout + proc.stderr).strip().splitlines()[:5]
        return Result(repo, False, error="\n".join(f"    {ln}" for ln in lines))
    if _git(base, repo, "rev-parse", "HEAD").stdout.strip() == head:
        return Result(repo, True)  # up to date — a fetched tag alone is not an update
    return Result(repo, True, change=f"{label} -> {describe(base, repo)}")


def pull_all(base: Path, repos: list[str], jobs: int = DEFAULT_JOBS):
    """Pull every repo, worktree-groups serially, groups in parallel. Yields Results
    as each group completes (so the CLI can print progressively)."""

    def pull_group(group: list[str]) -> list[Result]:
        return [pull_one(base, repo) for repo in group]

    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        for group_results in pool.map(pull_group, _group_by_store(base, repos)):
            yield from group_results


def clone(base: Path, url: str) -> Result:
    """Full clone (not shallow — the history is part of what gets browsed)."""
    name = repo_name(url)
    proc = subprocess.run(
        ["git", "clone", url, str(base / name)], capture_output=True, text=True
    )
    if proc.returncode != 0:
        lines = (proc.stdout + proc.stderr).strip().splitlines()[-3:]
        return Result(name, False, error="\n".join(f"    {ln}" for ln in lines))
    return Result(name, True, change="cloned")


# --- the folder's CLAUDE.md (takeaway notes; never overwritten) ---


def claude_md(base: Path) -> Path:
    return base / "CLAUDE.md"


def write_claude_md(base: Path) -> bool:
    """Write the template CLAUDE.md ONLY if none exists (it accumulates takeaways).
    Returns whether a file was written."""
    f = claude_md(base)
    if f.exists():
        return False
    bullets = "\n".join(_bullet(name, url, note) for name, url, note in BAKED_REPOS)
    f.write_text(CLAUDE_TEMPLATE.format(repo_bullets=bullets))
    return True


def _bullet(name: str, url: str, note: str) -> str:
    owner_repo = "/".join(url.rstrip("/").removesuffix(".git").rsplit("/", 2)[-2:])
    return f"- `{name}/` — [{owner_repo}]({url}) — {note}"


def append_claude_entry(base: Path, url: str, note: str = "(no deep dive yet)") -> None:
    """Append a stub bullet for a newly added repo to the folder's CLAUDE.md."""
    f = claude_md(base)
    if not f.exists():
        write_claude_md(base)
    with f.open("a") as fh:
        fh.write(_bullet(repo_name(url), url, note) + "\n")


def sizes(base: Path) -> list[tuple[str, int]]:
    """(repo name, bytes on disk) per clone, biggest first — via `du -sk` (fast,
    counts .git history too, which is most of a clone's weight)."""
    repos, _ = find_repos(base)
    if not repos:
        return []
    proc = subprocess.run(
        ["du", "-sk", *(str(base / r) for r in repos)], capture_output=True, text=True
    )
    out = []
    for line in proc.stdout.splitlines():
        kb, _, path = line.partition("\t")
        if kb.strip().isdigit():
            out.append((Path(path).name, int(kb) * 1024))
    return sorted(out, key=lambda t: t[1], reverse=True)
