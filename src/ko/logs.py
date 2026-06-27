"""Structured local logging — one wide JSON event per ko command, PostHog-shaped.

Follows PostHog's logging best practices: **structured JSONL**, **one rich event per command**
(emitted at completion with duration + exit), **scalar fields only**, and **no secrets** — args
are never logged, so a `-H "Authorization: Bearer …"` or a search query never lands in a log.

Local-only by default at `~/.local/state/ko/logs/<date>.jsonl`; a PostHog sync is opt-in and later
(the event schema is already capture-shaped). On by default — disable with `[logs] enabled = false`
in config.toml. DEBUG is off unless `[logs] level = "debug"`. **Logging never breaks a command**:
every write is best-effort and swallows its own errors.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from ko import config, dirs

LEVELS = ("debug", "info", "warn", "error")


def enabled() -> bool:
    return config.get("logs", "enabled", True) is not False


def _level_ok(level: str) -> bool:
    min_level = config.get("logs", "level", "info")
    try:
        return LEVELS.index(level) >= LEVELS.index(min_level)
    except ValueError:
        return level != "debug"  # unknown min -> everything but debug


def log_dir():
    return dirs.state_dir() / "logs"


def _version() -> str:
    try:
        from importlib.metadata import version

        return version("kotools")
    except Exception:
        return "?"


def log_event(event: str, level: str = "info", **fields) -> None:
    """Append one structured event to today's log file. Best-effort — never raises."""
    if not enabled() or not _level_ok(level):
        return
    try:
        now = datetime.now(timezone.utc)
        record = {
            "ts": now.isoformat(timespec="milliseconds"),
            "level": level,
            "event": event,
            "ko_version": _version(),
            **{k: v for k, v in fields.items() if v is not None},
        }
        d = log_dir()
        d.mkdir(parents=True, exist_ok=True)
        with (d / f"{now:%Y-%m-%d}.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")
    except Exception:
        pass  # logging must never break a command


def command_event(cmd: str, duration_ms: int, exit_code: int, error: str | None = None) -> None:
    """The wide event per command invocation — command label + outcome only, no args/secrets."""
    log_event(
        "command",
        level="error" if exit_code not in (0, None) else "info",
        cmd=cmd,
        duration_ms=duration_ms,
        exit_code=exit_code,
        error=error,
    )


def recent(n: int = 20, errors_only: bool = False) -> list[dict]:
    """The last n events across the most recent day-files, newest first."""
    d = log_dir()
    if not d.exists():
        return []
    out: list[dict] = []
    for path in sorted(d.glob("*.jsonl"), reverse=True):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
            if errors_only and out[-1].get("level") != "error":
                out.pop()
                continue
            if len(out) >= n:
                return out
    return out
