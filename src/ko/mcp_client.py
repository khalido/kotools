"""Minimal MCP client for *inspecting / calling* servers, reusing the same `mcp` SDK ko uses
as a TickTick client. Backs `ko mcp inspect|call|servers`.

Targets a server by **name or url**: names resolve from `~/.config/ko/mcp.json`, which holds the
standard `{"mcpServers": {...}}` object (copy-paste-compatible with Claude Code / opencode / Cursor).
Both transports the standard defines are supported — `url` (+`headers`, Streamable HTTP) and stdio
(`command`/`args`/`env`). On an HTTP failure it falls back to a raw initialize POST so the error is
the server's actual status + body (e.g. a 503 "MCP endpoint is not configured"), not an opaque SDK
TaskGroup wrapper.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import timedelta

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client

from ko import dirs

_TIMEOUT = timedelta(seconds=30)
_PROTOCOL = "2025-06-18"


class MCPTestError(RuntimeError):
    pass


@dataclass
class ToolInfo:
    name: str
    description: str
    required: list[str] = field(default_factory=list)
    schema: dict = field(default_factory=dict)  # full inputSchema — surfaced by `--tool`


@dataclass
class ResourceInfo:
    uri: str
    name: str
    description: str
    mime_type: str
    template: bool = False  # a parameterized resource template vs a concrete resource


@dataclass
class PromptInfo:
    name: str
    description: str
    arguments: list[str] = field(default_factory=list)


@dataclass
class ServerInfo:
    name: str
    version: str
    protocol: str
    instructions: str
    capabilities: list[str]
    tools: list[ToolInfo]
    resources: list[ResourceInfo]
    prompts: list[PromptInfo]


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


def _first_line(text: str | None) -> str:
    return (text or "").strip().split("\n")[0]


@asynccontextmanager
async def _session(spec: dict):
    """Open a ClientSession to a server `spec` (http url or stdio command). Not yet initialized."""
    if spec.get("transport") == "stdio":
        params = StdioServerParameters(
            command=spec["command"], args=spec.get("args") or [], env=spec.get("env") or None
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                yield session
    else:
        async with streamablehttp_client(spec["url"], headers=spec.get("headers") or None) as (read, write, _):
            async with ClientSession(read, write) as session:
                yield session


async def _inspect(spec: dict) -> ServerInfo:
    async with _session(spec) as session:
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
        resources: list[ResourceInfo] = []
        prompts: list[PromptInfo] = []
        # Each surface is fetched only if advertised, and wrapped so a server that advertises
        # a capability but errors listing it doesn't sink the whole inspect.
        if init.capabilities.tools is not None:
            try:
                for t in (await session.list_tools()).tools:
                    schema = t.inputSchema or {}
                    req = list(schema.get("required", []) or [])
                    tools.append(ToolInfo(t.name, _first_line(t.description), req, schema))
            except Exception:
                pass
        if init.capabilities.resources is not None:
            try:
                for r in (await session.list_resources()).resources:
                    resources.append(
                        ResourceInfo(str(r.uri), r.name or "", _first_line(r.description), r.mimeType or "")
                    )
                for tpl in (await session.list_resource_templates()).resourceTemplates:
                    resources.append(
                        ResourceInfo(tpl.uriTemplate, tpl.name or "", _first_line(tpl.description), "", template=True)
                    )
            except Exception:
                pass
        if init.capabilities.prompts is not None:
            try:
                for p in (await session.list_prompts()).prompts:
                    args = [a.name for a in (p.arguments or [])]
                    prompts.append(PromptInfo(p.name, _first_line(p.description), args))
            except Exception:
                pass
        si = init.serverInfo
        return ServerInfo(
            si.name, si.version, init.protocolVersion, (init.instructions or "").strip(),
            caps, tools, resources, prompts,
        )


async def _call(spec: dict, tool: str, args: dict) -> str:
    async with _session(spec) as session:
        await session.initialize()
        result = await session.call_tool(tool, args, read_timeout_seconds=_TIMEOUT)
        parts = [getattr(c, "text", None) or str(c) for c in (result.content or [])]
        return "\n".join(parts)


def _explain(spec: dict, exc: Exception) -> str:
    """A useful failure line: the server's raw HTTP reply for http, the leaf exception for stdio."""
    leaf = _unwrap(exc)
    if spec.get("transport") == "stdio":
        return f"{type(leaf).__name__}: {leaf}"
    return f"{type(leaf).__name__}. Server said: {_probe(spec['url'], spec.get('headers') or {})}"


def inspect(spec: dict) -> ServerInfo:
    """Connect to a resolved server spec, initialize, and list all surfaces. Raises MCPTestError."""
    try:
        return asyncio.run(_inspect(spec))
    except Exception as e:
        raise MCPTestError(_explain(spec, e)) from e


def call(spec: dict, tool: str, args: dict) -> str:
    """Call one tool on a resolved server spec; return its text content. Raises MCPTestError."""
    try:
        return asyncio.run(_call(spec, tool, args))
    except Exception as e:
        raise MCPTestError(_explain(spec, e)) from e


# --- server registry (~/.config/ko/mcp.json, standard `mcpServers` shape) ---


def load_servers() -> dict[str, dict]:
    """Configured servers from ~/.config/ko/mcp.json. Accepts `{"mcpServers": {...}}` (standard) or
    a bare `{name: spec}` object. Returns {} if the file is absent."""
    path = dirs.config_dir() / "mcp.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise MCPTestError(f"{path} is not valid JSON: {e}") from e
    return data.get("mcpServers", data) or {}


def _normalize(name: str, cfg: dict) -> dict:
    """A raw mcp.json server entry -> an internal spec (transport http|stdio)."""
    if cfg.get("command"):
        return {
            "transport": "stdio", "name": name,
            "command": cfg["command"], "args": cfg.get("args") or [], "env": cfg.get("env"),
        }
    url = cfg.get("url")
    if not url:
        raise MCPTestError(f"server {name!r} in mcp.json needs a 'url' or 'command'")
    return {"transport": "http", "name": name, "url": url, "headers": cfg.get("headers") or {}}


def resolve(target: str, header_overrides: dict[str, str] | None = None) -> dict:
    """Resolve a name-or-url to a server spec. A configured name wins; else a http(s) URL is used
    directly; else an error naming the configured servers. `-H` headers override an http server's."""
    overrides = header_overrides or {}
    servers = load_servers()
    if target in servers:
        spec = _normalize(target, servers[target])
        if spec["transport"] == "http":
            spec["headers"] = {**spec["headers"], **overrides}
        return spec
    if target.startswith(("http://", "https://")):
        return {"transport": "http", "name": target, "url": target, "headers": overrides}
    names = ", ".join(sorted(servers)) or "(none configured in ~/.config/ko/mcp.json)"
    raise MCPTestError(f"unknown server {target!r}: not a URL and not in mcp.json. Configured: {names}")


def parse_headers(items: list[str] | None) -> dict[str, str]:
    """'Key: Value' strings -> dict. Tolerates 'Key:Value' too."""
    out: dict[str, str] = {}
    for item in items or []:
        key, sep, val = item.partition(":")
        if not sep:
            raise MCPTestError(f"bad --header {item!r}; expected 'Key: Value'")
        out[key.strip()] = val.strip()
    return out
