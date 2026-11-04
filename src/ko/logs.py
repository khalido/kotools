"""Structured local logging via **loguru** — one wide event per ko command, sync-ready.

On by default to `~/.local/state/ko/logs/ko.jsonl` as JSON lines (loguru `serialize`), weekly
rotation + a month's retention; the default stderr sink is removed so ko's own output stays clean.
A PostHog (or any) sink would be one more `logger.add(sink)`.

Wide events (PostHog's logging best practices): code anywhere in a command calls `add()` / `inc()`
to attach scalar fields — model, tokens, cost, session, error message — and `cli.main()` writes
them all as ONE `command` event when the command ends, crash or not. Each field is added by the
code that owns the number (cost in `llm.run_cost`, errors in `_die`), never recomputed here.

The event carries the routed argv and error messages (local-only; no CLI flag takes a secret, and
query-string keys / bearer tokens are redacted). A sync off this machine must still drop or scrub
`argv` and `error_msg` first. Strings are cut at
`MAX_STR` with a visible `…`. Best-effort: logging never breaks a command.
"""

from __future__ import annotations

import json
import os
import platform
import re
from pathlib import Path

from loguru import logger

from ko import config, dirs

# loguru's `logger` is a process-global singleton; re-export it so ko modules can log to the same
# sink (`from ko.logs import logger`).
__all__ = ["add", "caller", "command_event", "enabled", "get", "inc", "log_event", "logger", "recent", "reset", "setup"]

MAX_STR = 200  # longer strings are cut, ending in "…" so the cut shows
# Credentials that ride inside strings: a query-string key (googleapiclient sends `?key=`, so a
# raw HttpError carries it; signed URLs in argv) or a bearer token in an error message.
_SECRET_RE = re.compile(
    r"(?i)([?&](?:api_?key|key|token|access_token|auth|sig|signature|x-amz-signature)=)[^&\s'\"]+"
    r"|(bearer\s+)[\w.~+/=-]+"
)

_configured_dir: str | None = None
_sink_id: int | None = None
_fields: dict[str, str | int | float | bool] = {}  # the current command's wide-event fields


def _scalar(v):
    """Scalars pass; anything else becomes its str, credentials redacted, cut with a visible `…`."""
    if v is None or isinstance(v, bool | int | float):
        return v
    s = _SECRET_RE.sub(lambda m: (m[1] or m[2]) + "[redacted]", v if isinstance(v, str) else str(v))
    return s if len(s) <= MAX_STR else s[: MAX_STR - 1] + "…"


def reset() -> None:
    """Start a fresh event — cli.main() calls this before each command."""
    _fields.clear()


def add(**fields) -> None:
    """Set fields on the current command's event (last write wins; None is skipped). Never raises."""
    try:
        for k, v in fields.items():
            if v is not None:
                _fields[k] = _scalar(v)
    except Exception:
        pass


def inc(**amounts: float) -> None:
    """Add numbers onto the current event's fields (tokens, cost across several LLM runs)."""
    try:
        for k, n in amounts.items():
            if n is not None:
                _fields[k] = _fields.get(k, 0) + n
    except Exception:
        pass


def get(key: str):
    return _fields.get(key)


def caller() -> dict:
    """Who ran this command: `caller` = the AI agent (its `AI_AGENT` env var, else Claude Code's
    own) or None for a person, its session id, and whether stdout is a terminal or a pipe."""
    agent = os.environ.get("AI_AGENT") or ("claude-code" if os.environ.get("CLAUDECODE") else None)
    try:
        tty = os.isatty(1)
    except OSError:
        tty = False
    return {"caller": agent, "caller_session": os.environ.get("CLAUDE_CODE_SESSION_ID"), "tty": tty}


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
    logger.configure(extra={"ko_version": _version(), "host": platform.node()})
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


def command_event(cmd: str, duration_ms: int, exit_code: int | None, level: str | None = None) -> None:
    """The wide event per command: label, outcome, and every field `add()`/`inc()` collected.
    Level defaults from the exit code: 0 info, 2 (usage) warning, anything else error."""
    if level is None:
        level = "info" if exit_code in (0, None) else "warning" if exit_code == 2 else "error"
    fields = {k: v for k, v in _fields.items() if k not in ("event", "level")}
    log_event("command", level, **(fields | {"cmd": cmd, "duration_ms": duration_ms, "exit_code": exit_code}))


def recent(n: int = 20, errors_only: bool = False) -> list[dict]:
    """Recent events from the loguru JSONL files, newest first, flattened: ts, level, event, then
    every field the event carried. `errors_only` = failed commands (warning or error)."""
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
            if errors_only and level not in ("error", "warning"):
                continue
            extra = rec.get("extra", {})
            out.append(
                {
                    "ts": rec.get("time", {}).get("repr", "")[:23],
                    "level": level,
                    "event": rec.get("message", ""),
                    **extra,
                    "cmd": extra.get("cmd") or rec.get("message", ""),
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
    _fields.clear()
