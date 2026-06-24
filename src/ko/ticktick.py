"""TickTick (read-only) via its official hosted MCP.

TickTick runs a hosted MCP server at https://mcp.ticktick.com over Streamable
HTTP. Auth is a single key: TickTick app → Account → MCP → generate → set
`TICKTICK_API_KEY` (env, or `~/.config/ko/config.toml` under `[keys]`).

Read-only on purpose: list your lists, read a list's open tasks. Manage tasks in
TickTick itself. We let the `mcp` SDK handle the init/session/SSE handshake.
"""

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass
from datetime import timedelta

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

ENDPOINT = "https://mcp.ticktick.com/"
_TIMEOUT = timedelta(seconds=20)  # hosted MCP can be slow; fail clearly, don't hang

_PRIORITY = {0: "—", 1: "low", 3: "med", 5: "high"}


@dataclass
class Project:
    id: str
    name: str


@dataclass
class Task:
    id: str
    title: str
    priority: str  # —/low/med/high
    due: str | None
    tags: list[str]


def _key() -> str:
    key = os.environ.get("TICKTICK_API_KEY")
    if not key:
        raise RuntimeError(
            "TICKTICK_API_KEY is not set. In TickTick: Account → MCP → generate an "
            "API key, then export TICKTICK_API_KEY=<key> (or add it to "
            "~/.config/ko/config.toml under [keys])."
        )
    return key


async def _call(tool: str, args: dict) -> dict:
    headers = {"Authorization": f"Bearer {_key()}"}
    async with streamablehttp_client(ENDPOINT, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool, args, read_timeout_seconds=_TIMEOUT)
            if result.isError:
                detail = (
                    getattr(result.content[0], "text", str(result.content[0]))
                    if result.content
                    else "(no detail)"
                )
                raise RuntimeError(f"TickTick {tool} failed: {detail}")
            return result.structuredContent or {}


def list_projects() -> list[Project]:
    """All your open lists (projects), in TickTick's own order."""
    data = asyncio.run(_call("list_projects", {}))
    return [
        Project(id=p["id"], name=p.get("name", ""))
        for p in data.get("result", [])
        if not p.get("closed")
    ]


def get_tasks(project_id: str) -> list[Task]:
    """Open (undone) tasks in a list, by project id."""
    data = asyncio.run(_call("get_project_with_undone_tasks", {"project_id": project_id}))
    return [
        Task(
            id=t["id"],
            title=t.get("title", "").strip(),
            priority=_PRIORITY.get(t.get("priority") or 0, "—"),
            due=t.get("dueDate"),
            tags=t.get("tags") or [],
        )
        for t in data.get("tasks", [])
    ]


def resolve_list(name_or_id: str, projects: list[Project] | None = None) -> Project | None:
    """Find a list by exact id, then exact name (case-insensitive), then unique substring."""
    projects = projects if projects is not None else list_projects()
    lowered = name_or_id.lower()
    for p in projects:
        if p.id == name_or_id or p.name.lower() == lowered:
            return p
    partial = [p for p in projects if lowered in p.name.lower()]
    if len(partial) > 1:
        print(
            f"note: {len(partial)} lists match {name_or_id!r}; using {partial[0].name!r} "
            f"(others: {', '.join(p.name for p in partial[1:])})",
            file=sys.stderr,
        )
    return partial[0] if partial else None
