"""User config + secret resolution.

Keys/tokens resolve from the environment first, then a `[keys]` table in
`~/.config/ko/config.toml`. Config keys are injected into the environment once at
startup, so every consumer — including SDKs that read `os.environ` directly
(pydantic-ai, google-genai) — picks them up with no special handling.

Note: config.toml lives in the dotfile-sync-safe config dir. If you don't want
secrets synced, keep them in the environment (which always wins) instead.
"""

from __future__ import annotations

import os
import tomllib
from functools import lru_cache

from ko.dirs import config_dir

_from_config: set[str] = set()


@lru_cache
def _data() -> dict:
    try:
        return tomllib.loads((config_dir() / "config.toml").read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def load_keys_into_env() -> None:
    """Inject config.toml [keys] into os.environ, without overriding real env vars."""
    for name, value in _data().get("keys", {}).items():
        if name not in os.environ:
            os.environ[name] = str(value)
            _from_config.add(name)


def key_source(name: str) -> str | None:
    """Where a key resolves from: 'env', 'config', or None if unset."""
    if name in _from_config:
        return "config"
    if os.environ.get(name):
        return "env"
    return None
