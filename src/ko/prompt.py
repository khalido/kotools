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

from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from ko import dirs


@dataclass
class Prompt:
    name: str
    description: str
    body: str
    source: str  # "packaged" (ships with ko) or "user" (~/.config/ko/prompts)


def _parse(text: str) -> tuple[dict[str, str], str]:
    """Split light frontmatter (`--- key: value ... ---`) from the markdown body.
    Only flat `key: value` lines are read — no YAML dependency. Returns (meta, body)."""
    meta: dict[str, str] = {}
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            for line in text[3:end].strip().splitlines():
                if ":" in line:
                    k, _, v = line.partition(":")
                    meta[k.strip()] = v.strip()
            return meta, text[end + 4 :].lstrip("\n")
    return meta, text


def _make(name_fallback: str, text: str, source: str) -> Prompt:
    meta, body = _parse(text)
    return Prompt(
        name=meta.get("name") or name_fallback,
        description=meta.get("description", ""),
        body=body,
        source=source,
    )


def _user_dir() -> Path:
    return dirs.config_dir() / "prompts"


def _all() -> dict[str, Prompt]:
    """Every brief by name. Packaged first, then user briefs overlaid (user wins by name)."""
    prompts: dict[str, Prompt] = {}
    packaged = resources.files("ko") / "prompts"
    if packaged.is_dir():
        for entry in packaged.iterdir():
            if entry.name.endswith(".md"):
                p = _make(entry.name[:-3], entry.read_text(encoding="utf-8"), "packaged")
                prompts[p.name] = p
    user = _user_dir()
    if user.is_dir():
        for path in sorted(user.glob("*.md")):
            p = _make(path.stem, path.read_text(encoding="utf-8"), "user")
            prompts[p.name] = p  # user override
    return prompts


def list_prompts() -> list[Prompt]:
    """All briefs, sorted by name."""
    return sorted(_all().values(), key=lambda p: p.name)


def names() -> list[str]:
    return [p.name for p in list_prompts()]


def get_prompt(name: str) -> Prompt:
    """One brief by name. Raises KeyError if there's no such brief."""
    prompts = _all()
    if name not in prompts:
        raise KeyError(name)
    return prompts[name]
