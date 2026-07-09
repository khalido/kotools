"""Agent session persistence — one flat JSON file per session.

pydantic-ai has no session store; message-history-as-JSON is the intended
mechanism (`ModelMessagesTypeAdapter`). We dump `all_messages()` (the full
trace, including tool calls/returns) after each turn so sessions can be listed,
resumed (`message_history=`), and later summarised/tagged.

Stored in `state_dir()/sessions/` — NOT config: these are generated, identity-tied
data (like a local db), not hand-edited config, so they live with tokens/caches,
not the dotfile-synced config dir. Flat files, no DB: listable, grepable, portable.

SQLite index (`state_dir()/ko.db`, table `sessions`)
------------------------------------------------------
The DB is an INDEX over the flat JSON files — deleting `ko.db` loses nothing;
it is always rebuildable by running `ko agent sessions summarize`. It holds
LLM-generated summaries and tags that are too expensive to regenerate on every
`sessions list`. JSON files remain the source of truth for message history.
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
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


def listing(tag: str | None = None, search: str | None = None) -> list[dict]:
    """Session metadata (no message bodies), newest first.

    Merges file metadata with DB summary rows when available. DB fields
    (title, summary, tags) override file-level fields only when a row exists —
    so unsummarised sessions still show their raw title.

    Args:
        tag:    only return sessions whose tags contain this string (exact word match)
        search: case-insensitive substring filter over title + summary
    """
    db_rows = {r["id"]: r for r in list_session_rows()}
    out: list[dict] = []
    for f in sorted(sessions_dir().glob("*.json"), reverse=True):
        try:
            d = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        sid = d.get("id", f.stem)
        row = db_rows.get(sid)
        entry: dict = {k: d.get(k) for k in ("id", "agent", "model", "title", "updated_at", "tags")}
        if row:
            # prefer the LLM-generated title/summary/tags from the DB index
            entry["title"] = row["title"] or entry["title"]
            entry["summary"] = row["summary"]
            entry["tags"] = row["tags"].split(",") if row["tags"] else []
        else:
            entry["summary"] = None
        out.append(entry)

    # filter in-memory so unsummarised sessions appear in list (graceful fallback)
    if tag:
        tag_lower = tag.lower()
        out = [
            e for e in out
            if tag_lower in [t.lower() for t in (e.get("tags") or [])]
        ]
    if search:
        needle = search.lower()
        out = [
            e for e in out
            if needle in (e.get("title") or "").lower()
            or needle in (e.get("summary") or "").lower()
        ]
    return out


# ---------------------------------------------------------------------------
# SQLite index — rebuildable from JSON files; deleting ko.db loses nothing
# ---------------------------------------------------------------------------

_DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id            TEXT PRIMARY KEY,
    agent         TEXT,
    model         TEXT,
    title         TEXT,
    summary       TEXT,
    tags          TEXT,
    created_at    TEXT,
    updated_at    TEXT,
    summarized_at TEXT
);
"""


def _db_path() -> Path:
    return state_dir() / "ko.db"


def get_db() -> sqlite3.Connection:
    """Open (and create-if-missing) the SQLite index. Caller owns the connection."""
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    conn.executescript(_DB_SCHEMA)
    conn.commit()
    return conn


def _now_us() -> str:
    """Current UTC time with microsecond precision — used for summarized_at comparisons."""
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def upsert_session_summary(
    id: str,
    agent: str,
    model: str,
    title: str,
    summary: str,
    tags: list[str],
    created_at: str,
) -> None:
    """Insert or replace a session summary row in the index."""
    now = _now()
    now_us = _now_us()
    # closing() closes the connection; sqlite3's own context manager only commits/rolls back
    with closing(get_db()) as conn, conn:
        conn.execute(
            """
            INSERT INTO sessions (id, agent, model, title, summary, tags, created_at, updated_at, summarized_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                agent         = excluded.agent,
                model         = excluded.model,
                title         = excluded.title,
                summary       = excluded.summary,
                tags          = excluded.tags,
                updated_at    = excluded.updated_at,
                summarized_at = excluded.summarized_at
            """,
            (id, agent, model, title, summary, ",".join(tags), created_at, now, now_us),
        )
        conn.commit()


def get_session_row(id: str) -> dict | None:
    """Return a single session row dict, or None if not yet in the index."""
    with closing(get_db()) as conn:
        row = conn.execute("SELECT * FROM sessions WHERE id = ?", (id,)).fetchone()
        return dict(row) if row else None


def list_session_rows(tag: str | None = None, search: str | None = None) -> list[dict]:
    """All summarized session rows from the DB index, newest first.

    Args:
        tag:    exact-word match in the comma-separated tags column
        search: case-insensitive substring over title + summary
    """
    with closing(get_db()) as conn:
        rows = conn.execute("SELECT * FROM sessions ORDER BY id DESC").fetchall()
    results = [dict(r) for r in rows]
    if tag:
        tag_lower = tag.lower()
        # lowercase both sides — the summarizer is *asked* for lowercase tags, not guaranteed
        results = [
            r
            for r in results
            if tag_lower in [t.lower() for t in (r.get("tags") or "").split(",")]
        ]
    if search:
        needle = search.lower()
        results = [
            r for r in results
            if needle in (r.get("title") or "").lower()
            or needle in (r.get("summary") or "").lower()
        ]
    return results


def _digest(messages: list[dict], max_chars: int = 8000) -> str:
    """Build a deterministic, budget-aware digest from raw message dicts.

    Three tiers, in priority order (budget: ~8k chars):
    1. User prompts (kept verbatim) and assistant text parts (kept verbatim) —
       these carry the meaning. First user prompt and final assistant answer are
       the last things sacrificed (middle turns drop first; only if the anchors
       alone exceed the budget do they get hard-capped).
    2. Tool calls are compressed to one line each:
       ``[tool: <name>(<args up to 80 chars>) → <return up to 150 chars>…]``
       They confirm *what the session actually did* without the plumbing cost.
       Dropped first when budget is tight.
    3. Long content is hard-capped at the budget; no model involved.

    Pure function: testable offline with fake message dicts.
    """
    # Pass 1: collect all segments with their tier
    # tier 0 = user/assistant text, tier 1 = tool summary lines
    segments: list[tuple[int, str]] = []

    # Pair up tool calls with their returns for compression
    pending_calls: dict[str, dict] = {}  # tool_call_id -> call part

    for m in messages:
        for p in m.get("parts", []):
            pk = p.get("part_kind")
            if pk == "user-prompt":
                content = p.get("content", "")
                if isinstance(content, str) and content.strip():
                    segments.append((0, f"USER: {content.strip()}"))
            elif pk == "text":
                content = p.get("content", "")
                if isinstance(content, str) and content.strip():
                    segments.append((0, f"ASSISTANT: {content.strip()}"))
            elif pk == "tool-call":
                # store for pairing with its return
                call_id = p.get("tool_call_id", "")
                tool_name = p.get("tool_name", "?")
                args = p.get("args", {})
                args_str = json.dumps(args) if isinstance(args, dict) else str(args)
                pending_calls[call_id] = {"name": tool_name, "args": args_str[:80]}
            elif pk == "tool-return":
                call_id = p.get("tool_call_id", "")
                ret = p.get("content", "")
                ret_str = str(ret)[:150]
                if call_id in pending_calls:
                    call = pending_calls.pop(call_id)
                    line = f"[tool: {call['name']}({call['args']}) → {ret_str}…]"
                else:
                    line = f"[tool-return: {ret_str}…]"
                segments.append((1, line))

    # Handle any unmatched tool calls (no return seen)
    for call in pending_calls.values():
        segments.append((1, f"[tool: {call['name']}({call['args']})]"))

    # Pass 2: budget allocation
    # Always keep first user segment (index 0) and last assistant segment
    # Drop tier-1 segments first, then middle tier-0 segments, never the anchors
    tier0 = [i for i, (t, _) in enumerate(segments) if t == 0]
    anchor_indices: set[int] = set()
    if tier0:
        anchor_indices.add(tier0[0])   # first user prompt
        anchor_indices.add(tier0[-1])  # last assistant text

    # Build output with budget, skipping tier-1 first if budget is tight
    def _join(idxs: list[int]) -> str:
        return "\n\n".join(segments[i][1] for i in idxs)

    all_idxs = list(range(len(segments)))
    full = _join(all_idxs)
    if len(full) <= max_chars:
        return full

    # Drop tier-1 (tool summary) lines first
    no_tools = [i for i in all_idxs if segments[i][0] == 0]
    reduced = _join(no_tools)
    if len(reduced) <= max_chars:
        return reduced

    # Still over budget: keep anchors + as many middle tier-0 as fit
    anchors_text = _join(sorted(anchor_indices))
    if len(anchors_text) >= max_chars:
        return anchors_text[:max_chars]

    # Add middle segments until budget runs out
    middle = [i for i in no_tools if i not in anchor_indices]
    result_idxs = sorted(anchor_indices)
    remaining = max_chars - len(anchors_text) - 4  # 4 for "\n\n" separators
    for i in middle:
        seg_text = segments[i][1]
        if len(seg_text) + 4 <= remaining:
            result_idxs.append(i)
            remaining -= len(seg_text) + 4
    return _join(sorted(result_idxs))
