"""ko prompt — a personal library of "kickoff brief" markdown files.

Each brief is one .md with light frontmatter (`name`, `description`) and a body of
opinionated, get-up-to-speed-fast notes for building a kind of thing — my stack, the
hard parts, which docs/skills to load. It's a meta-layer on top of generic llms.txt:
not "read Svelte's docs" but "here's how I wire Svelte + the chart lib I picked + the
gotcha that bites every time."

Briefs ship with the package (`src/ko/prompts/`) and can be added or overridden per
machine in `~/.config/ko/prompts/*.md` — a user file with the same name wins. Pull one
by name to load it into an agent or copy-paste it: `ko prompt sveltekit-app`.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from ko import dirs, frontmatter


@dataclass
class Prompt:
    name: str
    description: str
    body: str
    source: str  # "packaged" (ships with ko) or "user" (~/.config/ko/prompts)


def _make(name_fallback: str, text: str, source: str, path: str) -> Prompt:
    meta, body = frontmatter.split(text, path)
    return Prompt(
        name=meta.get("name") or name_fallback,
        description=meta.get("description", ""),
        body=body,
        source=source,
    )


def _user_dir() -> Path:
    return dirs.config_dir() / "prompts"


def _scan() -> tuple[dict[str, Prompt], dict[str, frontmatter.FrontMatterError]]:
    """Every brief by name, plus the files whose front matter doesn't parse (by file stem).
    Packaged first, then user briefs overlaid (user wins by name). A broken file stops only
    a command that asks for it, so one bad brief can't take down `ko sum` or completion."""
    prompts: dict[str, Prompt] = {}
    broken: dict[str, frontmatter.FrontMatterError] = {}
    sources = []
    packaged = resources.files("ko") / "prompts"
    if packaged.is_dir():
        sources += [(e.name[:-3], e, "packaged", f"ko/prompts/{e.name}") for e in packaged.iterdir() if e.name.endswith(".md")]
    user = _user_dir()
    if user.is_dir():
        sources += [(p.stem, p, "user", str(p)) for p in sorted(user.glob("*.md"))]
    for stem, file, source, label in sources:
        try:
            p = _make(stem, file.read_text(encoding="utf-8"), source, label)
        except frontmatter.FrontMatterError as e:
            broken[stem] = e
            continue
        prompts[p.name] = p  # a user brief overrides a packaged one by name
        broken.pop(p.name, None)
    return prompts, broken


def list_prompts(*, warn: bool = True) -> list[Prompt]:
    """All briefs, sorted by name. A brief whose front matter doesn't parse is left out with
    a note on stderr (`warn=False` for shell completion)."""
    prompts, broken = _scan()
    if warn:
        for e in broken.values():
            print(f"skipped: {e}", file=sys.stderr)
    return sorted(prompts.values(), key=lambda p: p.name)


def names() -> list[str]:
    return [p.name for p in list_prompts(warn=False)]


def resolve(name: str) -> Prompt | None:
    """Find a brief by exact name, then unique prefix, then substring (case-insensitive).

    Short tags are the point — `ko prompt research` should reach `research-papers` without
    my having to remember the full filename. Same shape as `ticktick.resolve_list`: an
    ambiguous tag notes the alternatives on stderr and takes the first rather than failing,
    because in a personal library of a dozen briefs a near-miss is more useful than an error.
    """
    prompts, broken = _scan()
    if name in prompts:
        return prompts[name]
    if name in broken:
        raise broken[name]
    lowered = name.lower()
    ordered = sorted(prompts.values(), key=lambda p: p.name)
    for match in (
        [p for p in ordered if p.name.lower().startswith(lowered)],
        [p for p in ordered if lowered in p.name.lower()],
    ):
        if match:
            if len(match) > 1:
                others = ", ".join(p.name for p in match[1:])
                print(
                    f"note: {len(match)} briefs match {name!r}; using {match[0].name!r} "
                    f"(others: {others})",
                    file=sys.stderr,
                )
            return match[0]
    return None


def get_prompt(name: str) -> Prompt:
    """One brief by name or short tag. Raises KeyError if nothing matches."""
    found = resolve(name)
    if found is None:
        raise KeyError(name)
    return found
