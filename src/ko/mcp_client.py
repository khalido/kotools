"""Minimal MCP client for *testing* servers — connect, list tools, optionally call one.

Speaks Streamable HTTP (the modern transport), reusing the same `mcp` SDK ko uses as a
TickTick client. `ko mcp test <url>` is the CLI front-end. On failure it falls back to a raw
initialize POST so the error is the server's actual HTTP status + body (e.g. a 503
"MCP endpoint is not configured"), not an opaque SDK TaskGroup wrapper.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

_TIMEOUT = timedelta(seconds=30)
_PROTOCOL = "2025-06-18"


class MCPTestError(RuntimeError):
    pass


@dataclass
class ToolInfo:
    name: str
    description: str
    required: list[str] = field(default_factory=list)


@dataclass
class ServerInfo:
    name: str
    version: str
    protocol: str
    capabilities: list[str]
    tools: list[ToolInfo]


def _unwrap(exc: BaseException) -> BaseException:
    """Peel ExceptionGroups (the SDK wraps failures in a TaskGroup) down to the first leaf."""
    seen = 0
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions and seen < 10:
        exc, seen = exc.exceptions[0], seen + 1
    return exc


def _probe(url: str, headers: dict[str, str]) -> str:
    """Raw initialize POST → a human 'HTTP <code>: <body>' line, so a failed connect explains itself."""
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"protocolVersion": _PROTOCOL, "capabilities": {}, "clientInfo": {"name": "ko", "version": "0"}},
    }
    h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream", **headers}
    try:
        r = httpx.post(url, json=body, headers=h, timeout=8.0)
    except httpx.HTTPError as e:
        return f"could not reach {url}: {e}"
    snippet = " ".join(r.text.split())[:300]
    return f"HTTP {r.status_code}: {snippet}" if snippet else f"HTTP {r.status_code}"


async def _inspect(url: str, headers: dict[str, str]) -> ServerInfo:
    async with streamablehttp_client(url, headers=headers or None) as (read, write, _):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            caps = [
                name
                for name, val in (
                    ("tools", init.capabilities.tools),
                    ("resources", init.capabilities.resources),
                    ("prompts", init.capabilities.prompts),
                )
                if val is not None
            ]
            tools: list[ToolInfo] = []
            if init.capabilities.tools is not None:
                for t in (await session.list_tools()).tools:
                    req = list((t.inputSchema or {}).get("required", []) or [])
                    desc = (t.description or "").strip().split("\n")[0]
                    tools.append(ToolInfo(t.name, desc, req))
            si = init.serverInfo
            return ServerInfo(si.name, si.version, init.protocolVersion, caps, tools)


def inspect(url: str, headers: dict[str, str] | None = None) -> ServerInfo:
    """Connect, initialize, and list tools. Raises MCPTestError with the server's real HTTP reply."""
    headers = headers or {}
    try:
        return asyncio.run(_inspect(url, headers))
    except Exception as e:
        raise MCPTestError(f"{type(_unwrap(e)).__name__}. Server said: {_probe(url, headers)}") from e


async def _call(url: str, headers: dict[str, str], tool: str, args: dict) -> str:
    async with streamablehttp_client(url, headers=headers or None) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool, args, read_timeout_seconds=_TIMEOUT)
            parts = [getattr(c, "text", None) or str(c) for c in (result.content or [])]
            return "\n".join(parts)


def call(url: str, tool: str, args: dict, headers: dict[str, str] | None = None) -> str:
    """Call one tool and return its text content. Raises MCPTestError on failure."""
    headers = headers or {}
    try:
        return asyncio.run(_call(url, headers, tool, args))
    except Exception as e:
        raise MCPTestError(f"{type(_unwrap(e)).__name__}. Server said: {_probe(url, headers)}") from e


def parse_headers(items: list[str] | None) -> dict[str, str]:
    """'Key: Value' strings -> dict. Tolerates 'Key:Value' too."""
    out: dict[str, str] = {}
    for item in items or []:
        key, sep, val = item.partition(":")
        if not sep:
            raise MCPTestError(f"bad --header {item!r}; expected 'Key: Value'")
        out[key.strip()] = val.strip()
    return out
