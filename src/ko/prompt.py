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
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

from ko import dirs, frontmatter


@dataclass
class Prompt:
    name: str
    description: str
    body: str
    source: str  # "packaged" (ships with ko) or "user" (~/.config/ko/prompts)
    text: str = field(default="", repr=False)  # the file as read, to re-check its front matter
    path: str = field(default="", repr=False)  # where it came from, for that note


def _make(name_fallback: str, text: str, source: str, path: str, warn: bool) -> Prompt:
    meta, body = frontmatter.split(text, path, warn=warn)
    return Prompt(
        name=meta.get("name") or name_fallback,
        description=meta.get("description", ""),
        body=body,
        source=source,
        text=text,
        path=path,
    )


def _user_dir() -> Path:
    return dirs.config_dir() / "prompts"


def list_prompts(*, warn: bool = True) -> list[Prompt]:
    """All briefs, sorted by name. Packaged first, then user briefs overlaid (user wins by
    name). A brief whose front matter doesn't parse keeps its body under its file name, with a
    note on stderr (`warn=False` for shell completion)."""
    sources = []
    packaged = resources.files("ko") / "prompts"
    if packaged.is_dir():
        sources += [(e.name[:-3], e, "packaged", f"ko/prompts/{e.name}") for e in packaged.iterdir() if e.name.endswith(".md")]
    user = _user_dir()
    if user.is_dir():
        sources += [(p.stem, p, "user", str(p)) for p in sorted(user.glob("*.md"))]
    prompts: dict[str, Prompt] = {}
    for stem, file, source, label in sources:
        p = _make(stem, file.read_text(encoding="utf-8"), source, label, warn)
        prompts[p.name] = p  # a user brief overrides a packaged one by name
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
    found = _match(name, list_prompts(warn=False))
    if found:  # only the brief in use warns about its front matter, not every file on every run
        frontmatter.split(found.text, found.path)
    return found


def _match(name: str, ordered: list[Prompt]) -> Prompt | None:
    if exact := next((p for p in ordered if p.name == name), None):
        return exact
    lowered = name.lower()
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
