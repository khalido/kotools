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


def get(section: str, key: str, default=None):
    """A non-secret config value, e.g. get('publish', 'domain')."""
    return (_data().get(section) or {}).get(key, default)


def setting(env_var: str, section: str, key: str, default=None):
    """A non-secret setting with the standard resolution chain: env var wins,
    then config.toml `[section] key`, then the baked default."""
    return os.environ.get(env_var) or get(section, key) or default


def setting_source(env_var: str, section: str, key: str) -> str:
    """Where a setting resolves from: 'env', 'config', or 'default'."""
    if os.environ.get(env_var):
        return "env"
    if get(section, key):
        return "config"
    return "default"
