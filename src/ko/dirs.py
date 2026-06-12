"""ko's directories — XDG-style, same on macOS and Linux.

Three dirs, three jobs (pattern: yt-dlp/httpie/llm; researched 2026-06-13):

- config: user-editable, dotfile-sync THIS one (config.toml, agents/, skills/)
- state:  tokens + caches tied to identity, never synced (google token, x ids, ko.db)
- cache:  disposable, safe to nuke

Each has an env override. `~/.config` deliberately also on macOS — dev CLIs
and dotfile tooling expect it there, `~/Library` is for GUI apps.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def config_dir() -> Path:
    return Path(os.environ.get("KO_CONFIG_DIR") or Path.home() / ".config" / "ko")


def state_dir() -> Path:
    d = Path(os.environ.get("KO_STATE_DIR") or Path.home() / ".local" / "state" / "ko")
    d.mkdir(parents=True, exist_ok=True)
    return d


def cache_dir() -> Path:
    d = Path(os.environ.get("KO_CACHE_DIR") or Path.home() / ".cache" / "ko")
    d.mkdir(parents=True, exist_ok=True)
    return d


def state_file(name: str) -> Path:
    """Path for a state file, migrating it from the old all-in-config layout once."""
    new = state_dir() / name
    old = config_dir() / name
    if old.is_file() and not new.exists():
        shutil.move(old, new)
    return new
