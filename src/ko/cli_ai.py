"""AI/meta/tools cluster CLI commands: llm, models, prompt, agent, mcp, publish, tt."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

import typer

from . import billing as billing_mod
from . import llm as llm_mod
from . import mcp_client as mcp_client_mod
from . import prompt as prompt_mod
from . import publish as publish_mod
from . import ticktick as ticktick_mod
from .agents import research_run, research_repl, tv_run, tv_repl
from ._cli_shared import _die, _emit_json, _no_results, app

# --- sub-apps ---

mcp_app = typer.Typer(
    help="Inspect/call MCP servers by name or url (ko is itself an MCP client). "
    "`ko mcp inspect <name|url>` · `call` · `servers`. Names from ~/.config/ko/mcp.json.",
    no_args_is_help=True,
)
app.add_typer(mcp_app, name="mcp")

tt_app = typer.Typer(
    help="TickTick lists/tasks, read-only via its hosted MCP (TICKTICK_API_KEY). Manage tasks in TickTick itself.",
    no_args_is_help=True,
)
app.add_typer(tt_app, name="tt")

publish_app = typer.Typer(
    help=(
        "Publish a folder to Cloudflare; re-publishing overwrites the same URL. "
        "Two steps: scaffold a site, then deploy it.\n\n"
        "Scaffold pathways (`ko publish new <dir> [flag]`) — each drops a CLAUDE.md with how-to:\n\n"
        "• (default) static HTML page — Tailwind + Alpine, no build.\n\n"
        "• --md   markdown doc site — write .md (README is the hub); raw HTML/SVG + Tailwind + "
        "Alpine render inline for custom visuals; TOC, syntax highlighting, print/PDF.\n\n"
        "• --bare  just a CLAUDE.md — the agent builds from scratch.\n\n"
        "• --hono  a Hono worker — API routes, D1/R2, server-side code; add --pin to gate it "
        "behind a 6-digit PIN.\n\n"
        "Preview: `ko publish preview <dir>` → wrangler dev at http://localhost:8787 (real http, "
        "so ES modules + fetch work; view it as you build).\n\n"
        "Deploy: `ko publish <dir>` → <name>.khalido.dev (`--name` to set it, `--force` to take "
        "over an existing name). Needs wrangler (`npm install`) + KO_CLOUDFLARE_API_TOKEN."
    ),
    no_args_is_help=True,
)
app.add_typer(publish_app, name="publish")

agent_app = typer.Typer(help="AI agents powered by pydantic-ai.", no_args_is_help=True)
app.add_typer(agent_app, name="agent")
app.add_typer(agent_app, name="a", hidden=True)  # shortcut: `ko a research`


# --- llm commands ---


@app.command("llm")
def llm_cmd(
    prompt: str = typer.Argument(
        ..., help="what to do; piped stdin becomes the input text"
    ),
    model: str = typer.Option(
        None,
        "--model",
        "-m",
        help=f"model string, e.g. {llm_mod.FALLBACK_MODEL} (default: KO_DEFAULT_MODEL or that)",
        autocompletion=llm_mod.available_models,
    ),
    system: str = typer.Option(
        None, "--system", "-s", help="replace the default system prompt"
    ),
) -> None:
    """One-shot LLM call, no tools: `ko hn item 123 | ko llm "summarize the debate"`."""
    llm_mod.refresh_openrouter_models()  # keeps -m autocomplete catalog fresh (once/day)
    stdin = None if sys.stdin.isatty() else sys.stdin.read()
    typer.echo(llm_mod.run(prompt, stdin=stdin, model=model, system=system))


@app.command("models")
def models_cmd(
    refresh: bool = typer.Option(
        False,
        "--refresh",
        help="force re-fetch the OpenRouter catalog, ignoring the 24h cache",
    ),
) -> None:
    """List model strings usable with -m, one per line — filtered to providers whose key is set."""
    llm_mod.refresh_openrouter_models(force=refresh)
    for name in sorted(llm_mod.available_models()):
        typer.echo(name)


# --- agent commands ---


@agent_app.command("research")
def agent_research(
    prompt: str = typer.Argument(
        None, help="research prompt; omit to enter interactive mode"
    ),
    model: str = typer.Option(
        None, "--model", "-m", help="model override, e.g. openrouter:anthropic/claude-sonnet-4"
    ),
    resume: str = typer.Option(
        None, "--resume", "-r", help="resume a saved session id (enters interactive mode)"
    ),
) -> None:
    """Research agent across web (exa), papers (arxiv, hf), and HN. No prompt = interactive REPL."""
    if prompt:
        research_run(prompt, model=model, resume=resume)  # prints itself; resume continues a session
    else:
        research_repl(model=model, resume=resume)


@agent_app.command("tv")
def agent_tv(
    prompt: str = typer.Argument(
        None, help="what you're in the mood for; omit to enter interactive mode"
    ),
    model: str = typer.Option(None, "--model", "-m", help="model override"),
    resume: str = typer.Option(
        None, "--resume", "-r", help="resume a saved session id (enters interactive mode)"
    ),
) -> None:
    """TV/movie agent — what to watch in Australia, tuned to Ko. No prompt = interactive REPL."""
    if prompt:
        tv_run(prompt, model=model, resume=resume)
    else:
        tv_repl(model=model, resume=resume)


@agent_app.command("sessions")
def agent_sessions(
    as_json: bool = typer.Option(False, "--json", help="emit JSON instead of TSV"),
) -> None:
    """List saved agent sessions, newest first (TSV: id, agent, model, title)."""
    from ko import sessions

    rows = sessions.listing()
    if not rows:
        _no_results("no sessions yet", as_json)
    if as_json:
        typer.echo(json.dumps(rows, default=str))
        return
    for s in rows:
        typer.echo(f"{s['id']}\t{s['agent']}\t{s['model']}\t{s['title']}")


# --- ticktick commands ---


@tt_app.command("lists")
def tt_lists(
    json_out: bool = typer.Option(False, "--json", help="JSON instead of TSV"),
) -> None:
    """List your TickTick lists (TSV: id, name)."""
    projects = ticktick_mod.list_projects()
    if json_out:
        _emit_json(projects)
        return
    if not projects:
        typer.echo("no lists", err=True)
        raise typer.Exit(0)
    for p in projects:
        typer.echo(f"{p.id}\t{p.name}")


@tt_app.command("items")
def tt_items(
    list_name: str = typer.Argument(..., help="list name (substring ok) or id"),
    json_out: bool = typer.Option(False, "--json", help="JSON instead of TSV"),
) -> None:
    """Open tasks in a list (TSV: priority, due, title). `ko tt <list>` is a shortcut."""
    proj = ticktick_mod.resolve_list(list_name)
    if not proj:
        typer.echo(f"no list matching {list_name!r}", err=True)
        raise typer.Exit(1)
    tasks = ticktick_mod.get_tasks(proj.id)
    if json_out:
        _emit_json(tasks)
        return
    if not tasks:
        typer.echo(f"{proj.name}: no open tasks", err=True)
        raise typer.Exit(0)
    for t in tasks:
        typer.echo(f"{t.priority}\t{t.due or '—'}\t{t.title}")


# --- publish commands ---


@publish_app.command("new")
def publish_new(
    path: str = typer.Argument(..., help="folder to scaffold (created if missing)"),
    title: str = typer.Option(None, "--title", "-t", help="page title (default: folder name)"),
    md: bool = typer.Option(False, "--md", help="markdown doc site (write .md, README is the hub)"),
    bare: bool = typer.Option(False, "--bare", help="just a CLAUDE.md of hints; build from scratch"),
    hono: bool = typer.Option(False, "--hono", help="Hono worker site — backend-ready (API routes, D1/R2)"),
    pin: bool = typer.Option(False, "--pin", help="PIN-gate it (implies --hono; generates a 6-digit PIN)"),
    name: str = typer.Option(
        None, "--name", "-n", help="worker/subdomain name (default: folder, or parent if folder is generic)"
    ),
) -> None:
    """Scaffold a site to publish. Default: static (Tailwind + Alpine). `--md` / `--bare` / `--hono`."""
    if pin:
        hono = True
    if sum([md, bare, hono]) > 1:
        typer.echo("--md / --bare / --hono are mutually exclusive", err=True)
        raise typer.Exit(2)
    mode = "md" if md else "bare" if bare else "hono" if hono else "static"
    folder = Path(path)
    written = publish_mod.scaffold(folder, title=title, mode=mode, pin=pin, name=name)
    if written:
        for p in written:
            typer.echo(f"created {p}")
    else:
        typer.echo(f"{folder} already scaffolded (nothing overwritten)", err=True)
    edit = {"md": "README.md", "hono": "public/README.md"}.get(mode, "index.html")
    if mode == "bare":
        hint = f"build an index.html in {folder}, then: ko publish {folder}"
    else:
        hint = f"edit {folder}/{edit}, then: ko publish {folder}"
    typer.echo(f"\n{hint}", err=True)


@publish_app.command("preview")
def publish_preview(
    path: str = typer.Argument(".", help="folder to preview (default: current dir)"),
    port: int = typer.Option(None, "--port", "-p", help="port (default: wrangler's 8787)"),
) -> None:
    """Preview a folder locally with `wrangler dev` (http://localhost:8787). Serves over http so ES
    modules + fetch work (a `file://` open doesn't); for --hono it runs the real worker. Ctrl-C to stop."""
    try:
        code = publish_mod.preview(Path(path), port=port)
    except RuntimeError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from None
    raise typer.Exit(code)


@publish_app.command("up")
def publish_up(
    path: str = typer.Argument(".", help="folder to publish (default: current dir)"),
    name: str = typer.Option(
        None, "--name", "-n", help="subdomain/name (default: derived from folder, sticky)"
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="take over an existing subdomain/Worker name"
    ),
    pin: str = typer.Option(
        None, "--pin", help="set/rotate the gate PIN on a --hono site ('new' = random 6-digit)"
    ),
) -> None:
    """Deploy a folder to Cloudflare. Prints the URL. Re-running overwrites the same URL."""
    try:
        if pin is not None:
            publish_mod.set_pin(Path(path), pin)  # rotate before deploy so the new PIN ships
        url = publish_mod.deploy(Path(path), name=name, force=force)
    except RuntimeError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from None
    if url:
        typer.echo(url)  # stdout: the URL itself (pipeable)
        gate_pin = publish_mod.config_pin(Path(path))
        code = publish_mod.check_url(url)  # sanity check: is it actually live?
        if gate_pin:
            typer.echo(f"🔒 PIN: {gate_pin}", err=True)
            note = "✓ live (PIN gate active)" if code in (200, 401) else (
                f"⚠ deployed but HTTP {code}" if code else "⚠ deployed but not reachable yet"
            )
        elif code == 200:
            note = "✓ live (HTTP 200)"
        elif code:
            note = f"⚠ deployed but HTTP {code} (cert may still be provisioning)"
        else:
            note = "⚠ deployed but not reachable yet (cert may be provisioning)"
        typer.echo(note, err=True)
    else:
        typer.echo("deployed — couldn't parse the URL; run `wrangler deployments list`", err=True)


@publish_app.command("list")
def publish_list(
    as_json: bool = typer.Option(False, "--json", help="emit JSON instead of TSV"),
) -> None:
    """List everything published (TSV: name, url, folder)."""
    rows = publish_mod.published()
    if not rows:
        _no_results("nothing published yet", as_json)
    if as_json:
        _emit_json(rows)
        return
    for p in rows:
        typer.echo(f"{p.name}\t{p.url}\t{p.folder}")


# --- prompt helpers ---


def _prompt_names(incomplete: str) -> list[str]:
    return [n for n in prompt_mod.names() if n.startswith(incomplete)]


def _copy_clipboard(text: str) -> None:
    import subprocess

    try:
        subprocess.run(["pbcopy"], input=text.encode(), check=True)
    except (OSError, subprocess.CalledProcessError) as e:
        _die(f"couldn't copy to clipboard (pbcopy): {e}")


# --- prompt command ---


@app.command("prompt")
def prompt_cmd(
    name: str = typer.Argument(
        None, help="brief to print; omit to list all", autocompletion=_prompt_names
    ),
    as_json: bool = typer.Option(False, "--json", help="emit JSON instead of text"),
    copy: bool = typer.Option(False, "--copy", help="copy the brief to the clipboard (pbcopy)"),
) -> None:
    """Kickoff briefs — my opinionated 'how I build X' notes to load into an agent.

    Bare `ko prompt` lists them (TSV: name, description); `ko prompt <name>` prints one.
    Add your own in ~/.config/ko/prompts/*.md — a file there overrides a packaged brief of
    the same name. `--copy` puts the brief on the clipboard; `--json` for structured output.
    """
    if name is None:
        prompts = prompt_mod.list_prompts()
        if not prompts:
            _no_results("no briefs found", as_json)
        if as_json:
            typer.echo(
                json.dumps(
                    [
                        {"name": p.name, "description": p.description, "source": p.source}
                        for p in prompts
                    ]
                )
            )
            return
        for p in prompts:
            typer.echo(f"{p.name}\t{p.description}")
        return
    try:
        p = prompt_mod.get_prompt(name)
    except KeyError:
        avail = ", ".join(prompt_mod.names()) or "(none)"
        _die(f"no brief named {name!r}. Available: {avail}", as_json=as_json, code="not_found")
    if copy:
        _copy_clipboard(p.body)
        typer.echo(f"Copied '{p.name}' to the clipboard ({len(p.body):,} chars).", err=True)
        return
    if as_json:
        typer.echo(json.dumps(asdict(p)))
        return
    typer.echo(p.body)


# --- mcp commands ---


def _mcp_args(arg: list[str] | None, as_json: bool) -> dict:
    args: dict = {}
    for a in arg or []:
        k, sep, v = a.partition("=")
        if not sep:
            _die(f"bad --arg {a!r}; expected k=value", as_json=as_json, code="usage")
        args[k.strip()] = v.strip()
    return args


@mcp_app.command("inspect")
def mcp_inspect(
    server: str = typer.Argument(
        ..., help="server name (from ~/.config/ko/mcp.json) or a URL, e.g. http://localhost:5180/mcp"
    ),
    header: list[str] = typer.Option(
        None, "--header", "-H",
        help="extra header 'Key: Value' (overrides config; e.g. 'Authorization: Bearer ...'); repeatable",
    ),
    tool: str = typer.Option(None, "--tool", help="print one tool's full JSON input schema and exit"),
    as_json: bool = typer.Option(False, "--json", help="emit JSON"),
) -> None:
    """Investigate an MCP server: its tools, resources, and prompts (+ capabilities and any server
    instructions). Target a configured name or a raw URL. `--tool <name>` dumps that tool's full
    input schema. On failure it prints the server's real HTTP status + body — so a 503 'not
    configured' is clearly a server issue, not your client. Listings to stdout; banner to stderr.
    """
    try:
        headers = mcp_client_mod.parse_headers(header)
    except mcp_client_mod.MCPTestError as e:
        _die(str(e), as_json=as_json, code="usage")
    try:
        spec = mcp_client_mod.resolve(server, headers)
        info = mcp_client_mod.inspect(spec)
    except mcp_client_mod.MCPTestError as e:
        _die(str(e), as_json=as_json, code="connect")
    if tool:
        match = next((t for t in info.tools if t.name == tool), None)
        if match is None:
            avail = ", ".join(t.name for t in info.tools) or "(none)"
            _die(f"no tool named {tool!r}. Tools: {avail}", as_json=as_json, code="not_found")
        typer.echo(json.dumps(match.schema, indent=2, default=str))
        return
    if as_json:
        typer.echo(json.dumps(asdict(info), default=str))
        return
    typer.echo(f"✓ {info.name} v{info.version}  (protocol {info.protocol})", err=True)
    typer.echo(f"  capabilities: {', '.join(info.capabilities) or '(none)'}", err=True)
    if info.instructions:
        typer.echo(f"  instructions: {info.instructions[:200]}", err=True)

    def _section(title: str, items: list, render) -> None:
        if items:
            typer.echo(f"\n{title} ({len(items)}):")
            for it in items:
                typer.echo("  " + render(it))

    _section(
        "TOOLS", info.tools,
        lambda t: f"{t.name}({', '.join(t.required)})" + (f"  —  {t.description}" if t.description else ""),
    )
    _section(
        "RESOURCES", info.resources,
        lambda r: f"{r.uri}" + (" [template]" if r.template else "") + (f"  —  {r.description}" if r.description else ""),
    )
    _section(
        "PROMPTS", info.prompts,
        lambda p: f"{p.name}({', '.join(p.arguments)})" + (f"  —  {p.description}" if p.description else ""),
    )
    if not (info.tools or info.resources or info.prompts):
        typer.echo("(server exposes no tools, resources, or prompts)", err=True)


@mcp_app.command("call")
def mcp_call(
    server: str = typer.Argument(..., help="server name (from mcp.json) or URL"),
    tool: str = typer.Argument(..., help="tool name to call (see `ko mcp inspect`)"),
    arg: list[str] = typer.Option(None, "--arg", help="k=value argument; repeatable"),
    header: list[str] = typer.Option(
        None, "--header", "-H", help="extra header 'Key: Value' (overrides config); repeatable"
    ),
    as_json: bool = typer.Option(False, "--json", help="emit JSON"),
) -> None:
    """Call one tool on an MCP server and print its result to stdout."""
    try:
        headers = mcp_client_mod.parse_headers(header)
    except mcp_client_mod.MCPTestError as e:
        _die(str(e), as_json=as_json, code="usage")
    args = _mcp_args(arg, as_json)
    try:
        spec = mcp_client_mod.resolve(server, headers)
        out = mcp_client_mod.call(spec, tool, args)
    except mcp_client_mod.MCPTestError as e:
        _die(str(e), as_json=as_json, code="call")
    typer.echo(json.dumps({"tool": tool, "result": out}) if as_json else out)


@mcp_app.command("overview")
def mcp_overview(
    server: str = typer.Argument(..., help="server name (from mcp.json) or URL"),
    header: list[str] = typer.Option(
        None, "--header", "-H", help="extra header 'Key: Value' (overrides config); repeatable"
    ),
    model: str = typer.Option(
        None, "--model", "-m", help="agent model (default: KO_AGENT_MODEL or openrouter:z-ai/glm-5.2)"
    ),
) -> None:
    """An LLM agent connects to an MCP server, explores it, and summarizes what it's for and what's
    most useful — great for sizing up a server (incl. your own dev server). Makes a (cheap) model call.
    """
    import os

    try:
        headers = mcp_client_mod.parse_headers(header)
    except mcp_client_mod.MCPTestError as e:
        _die(str(e), code="usage")
    mdl = model or os.environ.get("KO_AGENT_MODEL") or "openrouter:z-ai/glm-5.2"
    try:
        spec = mcp_client_mod.resolve(server, headers)
        out = mcp_client_mod.overview(spec, mdl)
    except mcp_client_mod.MCPTestError as e:
        _die(str(e), code="overview")
    typer.echo(out)


@app.command("billing")
def billing(
    as_json: bool = typer.Option(False, "--json", help="emit JSON"),
) -> None:
    """Balance + recent usage across paid services (read-only). v1: OpenRouter credits."""
    balances = [fn() for fn in billing_mod.PROVIDERS]
    if as_json:
        typer.echo(json.dumps([asdict(b) for b in balances], default=str))
        return
    for b in balances:
        if b.error:
            typer.echo(f"{b.provider:12} {b.error}", err=True)
            continue
        left = f"${b.remaining:.2f} left" if b.remaining is not None else "—"
        of = f"  (of ${b.total:.0f}; ${b.used:.2f} used)" if b.total is not None else ""
        trend = f"  · {b.trend}" if b.trend else ""
        acct = f"  [{b.account}]" if b.account else ""
        typer.echo(f"{b.provider:12}{acct}  {left}{of}{trend}")


@mcp_app.command("auth-info")
def mcp_auth_info(
    server: str = typer.Argument(..., help="server name (from mcp.json) or URL"),
    header: list[str] = typer.Option(None, "--header", "-H", help="extra header 'Key: Value'; repeatable"),
    as_json: bool = typer.Option(False, "--json", help="emit JSON"),
) -> None:
    """Validate a remote server's OAuth discovery surface BEFORE connecting — the public `.well-known`
    metadata (protected-resource + auth-server), scopes, endpoints, DCR — and flag anything malformed
    (e.g. HTML where JSON should be). Spec-aware (RFC 9728 + RFC 8414). Exit 1 if problems are found.
    """
    try:
        headers = mcp_client_mod.parse_headers(header)
        spec = mcp_client_mod.resolve(server, headers)
    except mcp_client_mod.MCPTestError as e:
        _die(str(e), as_json=as_json, code="usage")
    if spec.get("transport") != "http":
        _die("auth-info is for remote (http) servers", as_json=as_json, code="usage")
    info = mcp_client_mod.auth_info(spec["url"], spec.get("headers"))
    if as_json:
        typer.echo(json.dumps(info, default=str))
        raise typer.Exit(1 if info.get("problems") else 0)
    typer.echo(f"MCP endpoint: HTTP {info.get('mcp_status', '?')}  (auth required: {info.get('requires_auth')})")
    if info.get("www_authenticate"):
        typer.echo(f"  WWW-Authenticate: {info['www_authenticate']}")
    pr = info.get("protected_resource")
    if pr:
        typer.echo(f"\nProtected resource  [{info.get('protected_resource_url')}]")
        typer.echo(f"  resource:     {pr.get('resource')}")
        typer.echo(f"  scopes:       {', '.join(pr.get('scopes_supported', [])) or '(none)'}")
        typer.echo(f"  auth servers: {', '.join(pr.get('authorization_servers', []))}")
    for asm in info.get("auth_servers", []):
        typer.echo(f"\nAuth server  [{asm.get('_discovered_at')}]")
        typer.echo(f"  issuer:       {asm.get('issuer')}")
        typer.echo(f"  authorize:    {asm.get('authorization_endpoint')}")
        typer.echo(f"  token:        {asm.get('token_endpoint')}")
        typer.echo(f"  registration: {asm.get('registration_endpoint') or '— (no DCR)'}")
        typer.echo(f"  scopes:       {', '.join(asm.get('scopes_supported', [])) or '(none)'}")
        typer.echo(f"  PKCE S256:    {'S256' in (asm.get('code_challenge_methods_supported') or [])}")
    if info.get("problems"):
        typer.echo("\nPROBLEMS:", err=True)
        for prob in info["problems"]:
            typer.echo(f"  ✗ {prob}", err=True)
        raise typer.Exit(1)
    typer.echo("\n✓ discovery surface looks well-formed", err=True)


@mcp_app.command("servers")
def mcp_servers(
    as_json: bool = typer.Option(False, "--json", help="emit JSON"),
) -> None:
    """List MCP servers configured in ~/.config/ko/mcp.json (standard `mcpServers` shape).
    TSV: name, transport, target. Use a name with `ko mcp inspect <name>` / `call <name> <tool>`."""
    try:
        servers = mcp_client_mod.load_servers()
    except mcp_client_mod.MCPTestError as e:
        _die(str(e), as_json=as_json, code="config")
    if not servers:
        _no_results("no servers in ~/.config/ko/mcp.json (add an `mcpServers` object)", as_json)
    if as_json:
        typer.echo(json.dumps(servers))
        return
    for name, cfg in sorted(servers.items()):
        if cfg.get("command"):
            kind, target = "stdio", " ".join([cfg["command"], *cfg.get("args", [])])
        else:
            kind, target = "http", cfg.get("url", "?")
        typer.echo(f"{name}\t{kind}\t{target}")
