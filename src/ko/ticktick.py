"""TickTick tasks — read & write over the Open API v1, plus its hosted MCP for search.

One static token serves BOTH surfaces: TickTick (web app or desktop) → avatar →
Settings → Account → API Token. Set `TICKTICK_API_KEY` (env, or `~/.config/ko/config.toml`
under `[keys]`). No OAuth dance.

REST (`https://api.ticktick.com/open/v1`) is the spine: ~0.6s a call and it covers lists,
tasks, create, update, complete. The hosted MCP costs a ~3s handshake per invocation and
times out often, so it's kept for the one thing REST has no endpoint for: keyword `search`.

A task's markdown body is `content` — TickTick renders it in the app, so write markdown
verbatim (no escaping, no conversion). `desc` is only for CHECKLIST-kind tasks; we don't
use it. TickTick markdown quirks: single `~text~` is UNDERLINE (strikethrough is `~~text~~`),
`::text::` is highlight, and headings stop at three levels.

Deliberately not built: delete, un-complete, project create/delete, habits, focus — do
those in TickTick itself.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx2 as httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from . import config

API = "https://api.ticktick.com/open/v1"
ENDPOINT = "https://mcp.ticktick.com/"  # MCP: search only
_TIMEOUT = 20.0  # both surfaces can be slow; fail clearly, don't hang
DEFAULT_TZ = "Australia/Sydney"

_KEY_HINT = (
    "In TickTick: avatar → Settings → Account → API Token, then export "
    "TICKTICK_API_KEY=<token> (or add it to ~/.config/ko/config.toml under [keys])."
)

# TickTick's own wire format for dates: "yyyy-MM-dd'T'HH:mm:ssZ" (offset, no colon).
_TT_FMT = "%Y-%m-%dT%H:%M:%S%z"

_PRIORITY = {0: "—", 1: "low", 3: "med", 5: "high"}
PRIORITIES = {"none": 0, "low": 1, "med": 3, "medium": 3, "high": 5}

# TickTick's Inbox is a real list you can't see in GET /project, but the literal id
# "inbox" works on every /project/{id} route — so we surface it as a normal list.
INBOX = "inbox"


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
    project_id: str = ""  # which list it lives in — `done` needs it to complete the task


@dataclass
class Hit:
    """A keyword-search result — MCP search returns only these four fields."""

    id: str
    project: str  # list name (resolved from the url's project id)
    title: str
    url: str


def _key() -> str:
    return config.require_key("TICKTICK_API_KEY", _KEY_HINT)


def _request(method: str, path: str, payload: dict | None = None) -> Any:
    """One Open API call → decoded JSON (None for an empty body). Raises RuntimeError with
    the server's own status + body on failure, so the CLI can print one clean line."""
    try:
        resp = httpx.request(
            method,
            f"{API}{path}",
            json=payload,
            headers={"Authorization": f"Bearer {_key()}"},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        detail = e.response.text.strip()[:200] or "(no body)"
        hint = " — is TICKTICK_API_KEY current?" if e.response.status_code == 401 else ""
        raise RuntimeError(f"TickTick API {e.response.status_code}{hint}: {detail}") from e
    except httpx.HTTPError as e:
        raise RuntimeError(f"TickTick API unreachable: {e}") from e
    return resp.json() if resp.content else None


# --- dates ---


def tz_name() -> str:
    """The zone dates are read/written in — shared with `ko cal` (`[cal] timezone`)."""
    return config.get("cal", "timezone") or DEFAULT_TZ


def parse_due(s: str) -> tuple[bool, str]:
    """A CLI due string -> (isAllDay, dueDate in TickTick's wire format). Accepts
    'today' / 'tomorrow' / 'YYYY-MM-DD' (all-day) or 'YYYY-MM-DDTHH:MM' (timed), in the
    `[cal] timezone`. Raises ValueError otherwise. Mirrors `ko cal add` — no date library."""
    s = s.strip()
    zone = ZoneInfo(tz_name())
    if s == "today":
        day = datetime.now(zone).date()
    elif s == "tomorrow":
        day = datetime.now(zone).date() + timedelta(days=1)
    elif "T" in s:
        dt = datetime.fromisoformat(s)
        return False, (dt if dt.tzinfo else dt.replace(tzinfo=zone)).strftime(_TT_FMT)
    else:
        day = date.fromisoformat(s)
    return True, datetime(day.year, day.month, day.day, tzinfo=zone).strftime(_TT_FMT)


def _fmt_due(raw: str | None, all_day: bool) -> str | None:
    """TickTick returns dueDate in UTC ('2026-07-28T14:00:00.000+0000'), which shows the
    *wrong day* for a local all-day task. Render it in the local zone: 'YYYY-MM-DD' for an
    all-day task, 'YYYY-MM-DD HH:MM' for a timed one. Unparseable values pass through."""
    if not raw:
        return None
    try:
        local = datetime.fromisoformat(raw).astimezone(ZoneInfo(tz_name()))
    except ValueError:
        return raw
    return local.strftime("%Y-%m-%d" if all_day else "%Y-%m-%d %H:%M")


# --- reads ---


def _task(t: dict) -> Task:
    return Task(
        id=t["id"],
        title=(t.get("title") or "").strip(),
        priority=_PRIORITY.get(t.get("priority") or 0, "—"),
        due=_fmt_due(t.get("dueDate"), bool(t.get("isAllDay"))),
        tags=t.get("tags") or [],
        project_id=t.get("projectId") or "",
    )


def list_projects() -> list[Project]:
    """All your open lists, in TickTick's own order, with the virtual Inbox first.
    GET /project omits the Inbox; the literal id 'inbox' works on the task routes."""
    data = _request("GET", "/project") or []
    return [Project(id=INBOX, name="Inbox")] + [
        Project(id=p["id"], name=p.get("name", "")) for p in data if not p.get("closed")
    ]


def get_tasks(project_id: str) -> list[Task]:
    """Open (undone) tasks in one list, by project id. Completed tasks are not returned."""
    data = _request("GET", f"/project/{project_id}/data") or {}
    return [_task(t) for t in data.get("tasks") or []]


def open_tasks(project_ids: list[str] | None = None) -> list[Task]:
    """Every open task across every list (Inbox included) in ONE call — the resolver behind
    `done`. POST /task/filter with status [0]; pass project_ids to scope it. Open only."""
    payload: dict[str, Any] = {"status": [0]}
    if project_ids:
        payload["projectIds"] = project_ids
    return [_task(t) for t in _request("POST", "/task/filter", payload) or []]


# --- writes ---


def create_task(
    title: str,
    projectId: str | None = None,
    content: str | None = None,
    due: str | None = None,
    priority: int = 0,
    tags: list[str] | None = None,
) -> Task:
    """Create a task. No projectId (or 'inbox') → your Inbox. `content` is the markdown body,
    written verbatim. `due` takes the `parse_due` spellings. Param names mirror the API."""
    body: dict[str, Any] = {"title": title, "priority": priority}
    if projectId and projectId != INBOX:
        body["projectId"] = projectId
    if content:
        body["content"] = content
    if tags:
        body["tags"] = tags
    if due:
        all_day, when = parse_due(due)
        body |= {"isAllDay": all_day, "dueDate": when, "timeZone": tz_name()}
    return _task(_request("POST", "/task", body) or {})


def complete_task(projectId: str, taskId: str) -> None:
    """Mark a task done (status 2). Not reversible from here — un-complete in TickTick."""
    _request("POST", f"/project/{projectId}/task/{taskId}/complete")


# --- resolving ---


def _match_key(name: str) -> str:
    """A list name reduced to something comparable: TickTick glues the list's emoji icon onto
    the name ('🛒Shopping'), so drop any leading non-word characters, then casefold."""
    return re.sub(r"^\W+", "", name.strip()).casefold()


def resolve_list(name_or_id: str, projects: list[Project] | None = None) -> Project | None:
    """Find a list by exact id, then name (emoji-prefix- and case-insensitive), then unique
    substring. '🛒Shopping' matches 'shopping'."""
    projects = projects if projects is not None else list_projects()
    key = _match_key(name_or_id)
    for p in projects:
        if p.id == name_or_id or _match_key(p.name) == key:
            return p
    partial = [p for p in projects if key in _match_key(p.name)]
    if len(partial) > 1:
        print(
            f"note: {len(partial)} lists match {name_or_id!r}; using {partial[0].name!r} "
            f"(others: {', '.join(p.name for p in partial[1:])})",
            file=sys.stderr,
        )
    return partial[0] if partial else None


def find_tasks(query: str, tasks: list[Task]) -> list[Task]:
    """Tasks matching a free-text query: an exact id wins outright, else case-insensitive
    substring on the title. Pure — the caller decides what to do with 0, 1 or many."""
    for t in tasks:
        if t.id == query:
            return [t]
    lowered = query.casefold()
    return [t for t in tasks if lowered in t.title.casefold()]


# --- keyword search (MCP: REST has no search endpoint) ---


async def _mcp_call(tool: str, args: dict) -> dict:
    headers = {"Authorization": f"Bearer {_key()}"}
    async with (
        streamablehttp_client(ENDPOINT, headers=headers) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        result = await session.call_tool(
            tool, args, read_timeout_seconds=timedelta(seconds=_TIMEOUT)
        )
        if result.isError:
            detail = (
                getattr(result.content[0], "text", str(result.content[0]))
                if result.content
                else "(no detail)"
            )
            raise RuntimeError(f"TickTick {tool} failed: {detail}")
        if result.structuredContent:
            return result.structuredContent
        # Some tools answer with one JSON object per content text block instead.
        blocks = []
        for c in result.content:
            try:
                blocks.append(json.loads(getattr(c, "text", "")))
            except ValueError:
                pass
        return {"results": blocks}


def _project_id_from_url(url: str) -> str:
    """'…/webapp/#p/<projectId>/tasks/<taskId>' -> projectId ('' if the shape changes)."""
    return url.split("#p/")[1].split("/")[0] if "#p/" in url else ""


def search(query: str) -> list[Hit]:
    """Keyword search across ALL tasks (open and completed, every list) via the hosted MCP.
    Server-side and fuzzy: case-insensitive, matches `content` as well as the title, and
    ranks loosely — not a strict substring filter. Costs a ~3s handshake, and the index
    trails writes by ~10-30s (a task you just added won't be found yet — `open_tasks` is
    the live read). Occasionally returns an empty set for a query that does match; re-run.
    Returns id, list name, title and web url."""
    data = asyncio.run(_mcp_call("search_task", {"query": query}))
    raw = data.get("results") or data.get("result") or []
    if not raw:
        return []
    names = {p.id: p.name for p in list_projects()}
    hits = []
    for r in raw:
        url = r.get("url") or ""
        pid = _project_id_from_url(url)
        hits.append(
            Hit(
                id=r.get("id") or "",
                project=names.get(pid, "Inbox" if pid.startswith(INBOX) else pid),
                title=(r.get("title") or "").strip(),
                url=url,
            )
        )
    return hits
