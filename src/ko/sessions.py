"""Agent session persistence — one flat JSON file per session.

pydantic-ai has no session store; message-history-as-JSON is the intended
mechanism (`ModelMessagesTypeAdapter`). We dump `all_messages()` (the full
trace, including tool calls/returns) after each turn so sessions can be listed,
resumed (`message_history=`), and later summarised/tagged.

Stored in `state_dir()/sessions/` — NOT config: these are generated, identity-tied
data (like a local db), not hand-edited config, so they live with tokens/caches,
not the dotfile-synced config dir. Flat files, no DB: listable, grepable, portable.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from pydantic_ai.messages import ModelMessagesTypeAdapter

from ko.dirs import state_dir


def sessions_dir() -> Path:
    d = state_dir() / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id() -> str:
    """Time-sortable session id, e.g. 20260622T101500-ab12cd."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:6]


def _title(messages: list[dict]) -> str:
    for m in messages:
        if m.get("kind") == "request":
            for p in m.get("parts", []):
                if p.get("part_kind") == "user-prompt" and isinstance(p.get("content"), str):
                    return p["content"].strip()[:80]
    return "untitled"


def _model(messages: list[dict]) -> str:
    for m in reversed(messages):
        if m.get("kind") == "response" and m.get("model_name"):
            return m["model_name"]
    return "unknown"


def save(session_id: str, agent: str, messages: list) -> Path:
    """Write/overwrite a session file: metadata + full message history."""
    path = sessions_dir() / f"{session_id}.json"
    msgs = json.loads(ModelMessagesTypeAdapter.dump_json(messages))
    created = _now()
    if path.exists():  # preserve original created_at across turns
        try:
            created = json.loads(path.read_text()).get("created_at", created)
        except (OSError, ValueError):
            pass
    payload = json.dumps(
        {
            "id": session_id,
            "agent": agent,
            "model": _model(msgs),
            "title": _title(msgs),
            "created_at": created,
            "updated_at": _now(),
            "tags": [],
            "messages": msgs,
        },
        indent=2,
    )
    # atomic: this is rewritten every REPL turn — a crash mid-write must not corrupt the file
    tmp = path.with_suffix(".tmp")
    tmp.write_text(payload)
    os.replace(tmp, path)
    return path


def load(session_id: str) -> list:
    """Load a session's messages, ready to pass as `message_history=` to resume."""
    data = json.loads((sessions_dir() / f"{session_id}.json").read_text())
    return ModelMessagesTypeAdapter.validate_python(data["messages"])


def listing() -> list[dict]:
    """Session metadata (no message bodies), newest first."""
    out: list[dict] = []
    for f in sorted(sessions_dir().glob("*.json"), reverse=True):
        try:
            d = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        out.append({k: d.get(k) for k in ("id", "agent", "model", "title", "updated_at", "tags")})
    return out
