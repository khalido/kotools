"""Structured local logging via **loguru** — one wide event per ko command, sync-ready.

loguru so adding a sync is trivial: a PostHog (or any) sink is just another `logger.add(sink)` —
no file-parsing. On by default to `~/.local/state/ko/logs/ko.jsonl` as JSON lines (loguru
`serialize`), with weekly rotation + a month's retention. The default stderr sink is removed so ko's
own output stays clean. Following PostHog's best practices: one wide event per command, scalar fields
only via `logger.bind` — **args/secrets are never logged**. Best-effort: logging never breaks a command.
"""

from __future__ import annotations

import json
from pathlib import Path

from loguru import logger

from ko import config, dirs

# loguru's `logger` is a process-global singleton; re-export it so ko modules can log to the same
# sink (`from ko.logs import logger`).
__all__ = ["logger", "command_event", "log_event", "recent", "enabled", "setup"]

_configured_dir: str | None = None
_sink_id: int | None = None


def enabled() -> bool:
    return config.get("logs", "enabled", True) is not False


def log_dir() -> Path:
    return dirs.state_dir() / "logs"


def _version() -> str:
    try:
        from importlib.metadata import version

        return version("kotools")
    except Exception:
        return "?"


def setup() -> None:
    """Point loguru at the current log dir (reconfigures only if the dir changed — e.g. across tests).
    Drops loguru's default stderr sink so ko's stdout/stderr stay clean."""
    global _configured_dir, _sink_id
    d = str(log_dir())
    if _configured_dir == d:
        return
    logger.remove()  # default stderr sink + any prior ko sink
    _sink_id = None
    _configured_dir = d
    if not enabled():
        return
    Path(d).mkdir(parents=True, exist_ok=True)
    logger.configure(extra={"ko_version": _version()})
    level = (config.get("logs", "level", "info") or "info").upper()
    _sink_id = logger.add(
        d + "/ko.jsonl",
        serialize=True,
        level=level,
        enqueue=False,  # CLI logs once and exits — synchronous write, no flush race
        rotation="1 week",
        retention="1 month",
        format="{message}",
    )


def log_event(event: str, level: str = "info", **fields) -> None:
    """Append one structured event (scalar fields via bind). Best-effort — never raises."""
    try:
        if not enabled():
            return
        setup()
        if _sink_id is None:
            return
        logger.bind(**{k: v for k, v in fields.items() if v is not None}).log(level.upper(), event)
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
    """Recent events from the loguru JSONL files, newest first, normalized to flat dicts."""
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
                rec = json.loads(line).get("record", {})
            except json.JSONDecodeError:
                continue
            level = rec.get("level", {}).get("name", "").lower()
            if errors_only and level != "error":
                continue
            extra = rec.get("extra", {})
            out.append(
                {
                    "ts": rec.get("time", {}).get("repr", "")[:23],
                    "level": level,
                    "event": rec.get("message", ""),
                    "cmd": extra.get("cmd") or rec.get("message", ""),
                    "duration_ms": extra.get("duration_ms"),
                    "exit_code": extra.get("exit_code"),
                    "error": extra.get("error"),
                    "ko_version": extra.get("ko_version"),
                }
            )
            if len(out) >= n:
                return out
    return out


def _reset() -> None:
    """Tests only: drop sinks + forget config so the next setup() reconfigures."""
    global _configured_dir, _sink_id
    logger.remove()
    _configured_dir, _sink_id = None, None
