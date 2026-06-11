"""MCP server (stub).

Planned: expose ko's core functions as MCP tools so AI agents (Claude Desktop,
Cursor, Claude Code with MCP, Claude.ai custom connectors) can call them natively
without shelling out to bash.

Architecture
------------
ko has three layers:

    ko/<domain>.py      pure functions — data in, data out, no printing
    ko/cli.py           Typer wrappers — formatting, file I/O, stdout
    ko/mcp_server.py    MCP wrappers — same pure functions, exposed as tools

Both `cli.py` and this file import from the domain modules. Modules never learn
about transports. If a function in `<domain>.py` gets printing or file I/O, it's
a bug — lift those into the transport layer.

Structured output (why @dataclass is doing work for us)
-------------------------------------------------------
FastMCP introspects return type annotations and auto-generates structured output
schemas. Supported return types (per MCP Python SDK docs):

  - Pydantic models (BaseModel subclasses) — richest: per-field descriptions
  - TypedDicts
  - Dataclasses and other classes with type hints     ← what ko uses today
  - dict[str, T] for JSON-serializable T
  - Primitives and generics (str, list, etc.) — auto-wrapped as {"result": ...}

So the MCP tool wrappers below just return domain types directly — no manual
asdict() needed. FastMCP serializes.

If we ever want per-field descriptions in the MCP schema (visible to agents
when they call a tool), migrate the dataclasses to Pydantic BaseModel with
`Field(description=...)`. Not needed for v0.

Wiring sketch (uncomment when ready)
------------------------------------
    import os
    from mcp.server.fastmcp import FastMCP

    from ko import arxiv, exa, gsheets
    from ko.arxiv import SearchResult as ArxivResult
    from ko.exa import ExaResult

    # stateless_http=True + json_response=True is the Railway-friendly combo:
    # short POSTs, no long-lived connections — sidesteps Railway's 60s idle
    # timeout and its 15min max request duration.
    mcp = FastMCP("ko", stateless_http=True, json_response=True)

    @mcp.tool()
    def exa_search(query: str, n: int = 10) -> list[ExaResult]:
        '''Semantic web search via Exa.'''
        return exa.search(query, n=n, with_text=False)

    @mcp.tool()
    def arxiv_search(query: str, since_months: int = 18, n: int = 20) -> list[ArxivResult]:
        '''Search arxiv, newest first.'''
        return arxiv.search(query, since_months=since_months, max_results=n)

    @mcp.tool()
    def gsheets_get(spreadsheet_id: str, range_name: str) -> list[list]:
        '''Read a range from a Google Sheet. Returns a 2D array of cells.'''
        return gsheets.get_range(spreadsheet_id, range_name)

    if __name__ == "__main__":
        transport = os.getenv("MCP_TRANSPORT", "stdio")
        mcp.run(transport=transport)  # stdio for local, "streamable-http" for remote

Running
-------
- Local (Claude Desktop):  `python -m ko.mcp_server`  (stdio, default)
- Dev + inspector:          `mcp dev src/ko/mcp_server.py`  (comes with mcp[cli])
- Install to Claude Desktop:`mcp install src/ko/mcp_server.py`
- Remote (HTTP ASGI):       expose `mcp.streamable_http_app()` under uvicorn —
                            see the Railway deploy section below.

Design notes
------------
- Prefer fewer, more obvious params at the MCP boundary. If the CLI has ten
  flags, the MCP tool should have three. Keep the power in the CLI, keep the
  agent surface simple. Flatten here; don't push transport concerns into the
  domain modules.
- MCP Resources (read-only data with URIs) are a good fit for static config —
  e.g. "list of known sheet IDs". Tools are for actions with arguments.
- Every @mcp.tool docstring becomes the tool description agents see. Make it
  a useful first sentence, same discipline as Typer `help=`.

Deploy to Railway (future)
--------------------------
Since modules are transport-agnostic, going remote is mostly hosting config:

  # expose ASGI app
  app = mcp.streamable_http_app()

  # Procfile or railway.toml:
  # web: uvicorn ko.mcp_server:app --host 0.0.0.0 --port $PORT

Auth: MCP spec recommends OAuth 2.1 for remote servers but it's not required.
For a personal server, a bearer-token check in a TokenVerifier is enough
(compare against $KO_MCP_SECRET). Full OAuth can wait until we're public.

Client side once hosted at e.g. https://ko-mcp.railway.app/mcp:
- Claude.ai:        Settings → Connectors → Add custom connector (paste URL)
- Claude Desktop:   claude_desktop_config.json → mcpServers.ko.type="http", url=...
- Cursor:           Settings → MCP → add URL, type http

Reference
---------
- MCP intro:         https://modelcontextprotocol.io/docs/getting-started/intro
- Python SDK:        https://github.com/modelcontextprotocol/python-sdk
- Transports:        https://modelcontextprotocol.io/docs/concepts/transports
- Remote servers:    https://modelcontextprotocol.io/docs/develop/connect-remote-servers
- Agent skills:      https://modelcontextprotocol.io/docs/develop/build-with-agent-skills
"""
