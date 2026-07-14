"""Per-agent markdown memory — a writable workspace each agent maintains itself.

Design (docs/memory.md "v1 first", researched against thinker/deepagents/hermes):
each agent gets `~/.local/state/ko/memory/<agent>/` — `memory.md` is the anchor
(head-injected into instructions every run), and the agent may grow other .md
notes beside it (read on demand; the injection lists them). One shared
`~/.config/ko/memory.md` (user preferences / standing context, dotfile-synced)
is injected into every memory-carrying agent first; v1 keeps it hand-edited.

Guardrails: tools are scoped to the agent's OWN dir, .md files only (this is a
notes space, not a filesystem); `memory.md` can't be wholesale overwritten —
append or uniqueness-guarded edit only (thinker's invariant). The pin marker
(deepagents): lines above `<!-- ko:pin-end -->` always survive injection; the
tail below is recency-truncated (newest kept), so foundations never age out.

Curation lives in the toolset INSTRUCTIONS, not code — thinker ran 8 months of
agent-curated memories this way without bloat.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_ai import ModelRetry
from pydantic_ai.toolsets import FunctionToolset

from ko.dirs import config_dir, state_dir

MEMORY_FILE = "memory.md"
PIN_MARKER = "<!-- ko:pin-end -->"
HEAD_LINES = 200  # max memory lines injected into instructions


def memory_dir(agent: str) -> Path:
    if Path(agent).name != agent or agent.startswith(
        "."
    ):  # factory is public API — keep it honest
        raise ValueError(f"agent name must be a plain name, got {agent!r}")
    d = state_dir() / "memory" / agent
    d.mkdir(parents=True, exist_ok=True)
    return d


def shared_file() -> Path:
    return config_dir() / "memory.md"


def _resolve_note(agent: str, name: str) -> Path:
    """Containment for the workspace: the agent's own memory dir is FLAT — plain
    .md names only (subdir names used to escape into an uncaught FileNotFoundError,
    and wouldn't show in the injection listing anyway)."""
    if Path(name).name != name:
        raise ModelRetry(
            f"'{name}' — use a plain name like 'memory.md' or 'notes.md' (no folders)."
        )
    root = memory_dir(agent).resolve()
    full = (root / name).resolve()
    if not full.is_relative_to(root):
        raise ModelRetry(
            f"'{name}' is outside your memory folder — use plain names like 'memory.md' or 'notes.md'."
        )
    if full.suffix.lower() != ".md":
        raise ModelRetry(
            f"'{name}' — your memory folder holds markdown only; use a .md name."
        )
    return full


def _notes(agent: str) -> list[str]:
    root = memory_dir(agent)
    return sorted(p.name for p in root.glob("*.md"))


def _head(
    text: str,
    budget: int = HEAD_LINES,
    hint: str | None = f"read_note('{MEMORY_FILE}') for all",
) -> str:
    """Pin-aware head: pinned lines always included; the tail keeps its NEWEST lines
    (entries append at the bottom) with a truncation marker pointing at the rest.
    `hint=None` for files no tool can read back (the shared memory)."""
    lines = text.splitlines()
    if len(lines) <= budget:
        return text
    if PIN_MARKER in text:
        cut = next(i for i, ln in enumerate(lines) if PIN_MARKER in ln) + 1
        pinned, tail = lines[:cut], lines[cut:]
    else:
        pinned, tail = [], lines
    keep = max(budget - len(pinned), 0)
    dropped = len(tail) - keep
    if dropped <= 0:  # everything pinned / nothing to drop — no noise marker
        return text
    marker = [f"[…{dropped} older lines truncated{' — ' + hint if hint else ''}]"]
    return "\n".join(pinned + marker + tail[-keep:] if keep else pinned + marker)


def instructions_block(agent: str) -> str:
    """The memory injection for an agent's dynamic instructions: shared memory,
    then the agent's own memory.md head, then a listing of its other notes."""
    parts: list[str] = []
    try:
        shared = shared_file().read_text(errors="replace").strip()
        if shared:
            # hint=None: no tool reads the shared file — a read_note hint would misdirect
            parts.append(
                f"## Shared memory (about Ko — {shared_file()})\n\n{_head(shared, hint=None)}"
            )
    except OSError:
        pass
    own = memory_dir(agent) / MEMORY_FILE
    try:
        text = own.read_text(errors="replace").strip()
    except OSError:
        text = ""
    parts.append(
        f"## Your memory ({MEMORY_FILE})\n\n{_head(text) if text else '(empty — you have not saved anything yet)'}"
    )
    others = [n for n in _notes(agent) if n != MEMORY_FILE]
    if others:
        parts.append("Your other notes (read_note to open): " + ", ".join(others))
    return "\n\n".join(parts)


def memory_toolset(agent: str) -> FunctionToolset:
    """The write-capable workspace toolset for ONE agent — a factory, not a shared
    instance (tools close over the agent's own dir)."""
    ts = FunctionToolset(
        instructions=(
            f"Memory: you own the folder behind memory.md (shown above) and may keep other "
            f".md notes in it. Curation rules — update SLOWLY: save only what will recur; "
            f"bump dates/counts in-place with edit_note; DELETE stale entries; keep "
            f"{MEMORY_FILE} under ~100 lines (put durable foundations above the "
            f"'{PIN_MARKER}' line — they always survive truncation). Topic findings go in "
            f"your own notes, never in the shared memory."
        )
    )

    @ts.tool_plain
    def read_note(name: str = MEMORY_FILE) -> str:
        """Read one of your memory-folder notes in full (default: memory.md)."""
        f = _resolve_note(agent, name)
        if not f.is_file():
            have = ", ".join(_notes(agent)) or "(none yet)"
            raise ModelRetry(f"'{name}' does not exist. Your notes: {have}")
        return f.read_text()

    @ts.tool_plain
    def append_memory(text: str) -> str:
        """Append lines to the BOTTOM of memory.md (newest last — the tail is what
        survives truncation). For changing existing lines use edit_note."""
        f = _resolve_note(agent, MEMORY_FILE)
        with f.open("a") as fh:
            fh.write(
                text.rstrip() + "\n"
            )  # every append ends the file with \n — no separator needed
        return f"appended {len(text.splitlines())} line(s) to {MEMORY_FILE}"

    @ts.tool_plain
    def write_note(name: str, content: str) -> str:
        """Create or overwrite one of your notes (NOT memory.md — that anchor only
        changes via append_memory/edit_note, so it can't be clobbered wholesale)."""
        f = _resolve_note(agent, name)
        if (
            f.name.lower() == MEMORY_FILE
        ):  # .lower(): APFS is case-insensitive — 'MEMORY.MD' IS the anchor
            raise ModelRetry(
                "memory.md can't be overwritten — append_memory or edit_note it."
            )
        f.write_text(content.rstrip() + "\n")
        return f"wrote {len(content.splitlines())} line(s) to {name}"

    @ts.tool_plain
    def edit_note(old_string: str, new_string: str, name: str = MEMORY_FILE) -> str:
        """Replace an exact string in a note (default memory.md). `old_string` must
        occur exactly once — include enough surrounding context to make it unique.
        Use with new_string='' to delete stale entries."""
        f = _resolve_note(agent, name)
        if not f.is_file():
            raise ModelRetry(f"'{name}' does not exist yet.")
        text = f.read_text()
        n = text.count(old_string)
        if n == 0:
            raise ModelRetry(
                f"string not found in {name} — read_note it and retry with the exact text."
            )
        if n > 1:
            raise ModelRetry(
                f"string occurs {n} times in {name} — add surrounding context to make it unique."
            )
        f.write_text(text.replace(old_string, new_string, 1))
        return "edit applied"

    return ts
