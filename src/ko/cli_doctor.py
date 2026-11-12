"""`ko doctor`: every tool, what it needs and whether it's ready, then settings, dirs, versions,
refs weight and live credit balances."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import typer

from . import config
from ._cli_shared import _emit_json, app

Status = tuple[str, str]  # (label, rich colour)
GREEN_NO_AUTH: Status = ("✓ no auth", "green")


def _online(host: str = "ai-gateway.vercel.sh", timeout: float = 1.5) -> bool:
    """Can we reach the internet? One TCP connect, so `ko doctor` can skip its live checks
    offline instead of stalling on each one's timeout."""
    import socket

    try:
        socket.create_connection((host, 443), timeout=timeout).close()
        return True
    except OSError:
        return False


def _key_status(var: str) -> Status:
    return {
        "env": ("✓ env", "green"),
        "config": ("✓ config", "cyan"),
    }.get(config.key_source(var), ("✗ missing", "red"))


def _google_status() -> Status:
    from . import google_auth

    if google_auth.token_file().exists():
        return (f"✓ authed ({google_auth.active_account()})", "green")
    if google_auth.client_file().exists():
        return ("– run `ko google auth`", "yellow")
    return ("✗ no client file", "red")


def _mcp_status() -> Status:
    from . import mcp_client

    try:
        servers = mcp_client.load_servers()
    except mcp_client.MCPTestError:
        return ("✗ invalid mcp.json", "red")
    if not servers:
        return ("– no mcp.json", "yellow")
    return (f"✓ {len(servers)} server{'s' if len(servers) != 1 else ''} configured", "green")


def _pdf_status() -> Status:
    from . import pdf

    if not pdf.native_libs_ok():
        return ("✗ brew install pango", "red")
    # A configured brand path that doesn't exist is worse than none — ko pdf would fail
    # outright — so require the file, not just the setting.
    brand = pdf.brand_path()
    if brand and brand.exists():
        return ("✓ ready", "green")
    return ("– no brand.yml: letterhead placeholder", "yellow")


def _publish_status() -> Status:
    from . import publish

    source = publish.wrangler_source()
    if not (publish.cf_creds()[0] or source == "none"):
        return ("– no Cloudflare token (fine after `wrangler login`)", "yellow")
    return {
        "local": ("✓ repo-local", "green"),
        "path": ("✓ on PATH", "green"),
        "env": ("✓ KO_WRANGLER", "green"),
        "npx": ("– via npx (run `npm install`)", "yellow"),
    }.get(source, ("✗ no wrangler/npx", "red"))


def _telemetry_status() -> Status:
    from . import telemetry

    if not telemetry.enabled():
        return ("– off (default)", "yellow")
    if config.key_source("POSTHOG_API_KEY"):
        return ("✓ on", "green")
    return ("✗ enabled but no POSTHOG_API_KEY", "red")


def _found(ok: bool, missing: Status = ("– optional", "yellow")) -> Status:
    return ("✓ found", "green") if ok else missing


def _tool_rows() -> list[tuple[str, str, str, Status]]:
    """One row per tool: (name, what it does, what it needs, status)."""
    from . import llm, models

    default = llm.default_model()
    default_key = models.PROVIDER_KEYS.get(default.split(":")[0], "")
    llm_status = _key_status(default_key) if default_key else ("? unknown provider", "yellow")
    arxiv2md = bool(shutil.which("arxiv2md")) or (Path(sys.executable).parent / "arxiv2md").exists()
    soffice = bool(shutil.which("soffice")) or Path("/Applications/LibreOffice.app").exists()
    google = _google_status()
    return [
        ("hn", "Hacker News top/search/comments", "—", GREEN_NO_AUTH),
        ("hf", "HF papers: daily feed, search, metadata", "—", GREEN_NO_AUTH),
        ("yt", "YouTube transcripts; video + channel metadata", "GOOGLE_CLOUD_API_KEY (info/channel); -s: GEMINI_FREE_API_KEY (free) else the gateway", _key_status("GOOGLE_CLOUD_API_KEY")),
        ("brief", "morning brief (cal+gmail+hn+hf → LLM)", f"{default_key or '?'} + Google auth (optional-degrade)", llm_status),
        ("papers", "cross-publisher paper search + citation graph", "S2_API_KEY (optional: tldr/similar)", _key_status("S2_API_KEY") if config.key_source("S2_API_KEY") else ("– optional", "yellow")),
        ("fetch", "URL → markdown (articles, PDFs, Wayback)", "—", GREEN_NO_AUTH),
        ("arxiv", "arxiv search + paper → markdown", "arxiv2md binary", _found(arxiv2md, ("✗ missing", "red"))),
        ("doc", "PDF/Office/image → text; OCR for pages with no text layer", "LibreOffice for Office files (not .docx); OCR: the gateway key, or -e vision/tesseract", _found(soffice)),
        ("ocr", "free OCR with `ko doc -e vision` (macOS)", "swiftc (xcode-select --install); losing lines? rm -r ~/Library/Caches/ko-vision-ocr", _found(bool(shutil.which("swiftc")))),
        ("pdf", "markdown → print-ready PDF (local)", "pango/cairo (brew install pango) + [pdf] brand", _pdf_status()),
        ("exa", "semantic web search (paid)", "EXA_API_KEY", _key_status("EXA_API_KEY")),
        ("llm", f"one-shot LLM (default {default})", default_key or "?", llm_status),
        ("x", "X posts (paid tier for reads)", "X_BEARER_TOKEN", _key_status("X_BEARER_TOKEN")),
        ("tt", "TickTick tasks (read+write via Open API v1) — Settings → Account → API Token", "TICKTICK_API_KEY", _key_status("TICKTICK_API_KEY")),
        ("publish", "deploy a folder to Cloudflare (static)", "wrangler + KO_CLOUDFLARE_API_TOKEN/ACCOUNT_ID", _publish_status()),
        ("agent", "research agent (web search via exa)", "VERCEL_AI_GATEWAY_API_KEY + EXA_API_KEY", _key_status("VERCEL_AI_GATEWAY_API_KEY")),
        ("tv", "movie/TV info + where to stream — themoviedb.org API settings", "TMDB_READ_ACCESS_TOKEN", _key_status("TMDB_READ_ACCESS_TOKEN")),
        ("gsheets", "read & write Google Sheets", "OAuth client + token", google),
        ("gdocs/cal/gmail", "Google Docs/Calendar/Gmail (shares gsheets token; gmail read + drafts, send config-gated)", "OAuth client + token (same as gsheets)", google),
        ("mcp", "inspect/call MCP servers by name or url", "~/.config/ko/mcp.json", _mcp_status()),
        ("billing", "balance + usage across paid services (AI Gateway, OpenRouter)", "VERCEL_AI_GATEWAY_API_KEY", _key_status("VERCEL_AI_GATEWAY_API_KEY")),
        ("telemetry", "LLM traces → PostHog (opt-in, metadata-only by default)", "[telemetry] enabled + POSTHOG_API_KEY", _telemetry_status()),
    ]


def _settings() -> list[tuple[str, str, str]]:
    """Effective non-secret settings: (name, value, where it resolves from)."""
    from . import gcal, llm, models, ocr, publish
    from .google_auth import active_account
    from .x import _default_handle

    src = config.setting_source
    return [
        ("llm model", llm.default_model(), src("KO_DEFAULT_MODEL", "llm", "model")),
        ("agent model", config.setting("KO_AGENT_MODEL", "agents", "model", "(per-agent default)"), src("KO_AGENT_MODEL", "agents", "model")),
        *(
            (f"{tier} tier", models.model_for(tier), "config" if config.get("llm", tier) else "default")
            for tier in models.TIERS
        ),
        ("sum model", llm.sum_model(), src("KO_SUM_MODEL", "llm", "sum")),
        ("ocr engine", ocr.engine().removeprefix("vercel:"), src("KO_OCR_ENGINE", "ocr", "engine")),
        ("cal timezone", gcal.tz_name(), "config" if config.get("cal", "timezone") else "default"),
        ("x handle", _default_handle(), src("KO_X_HANDLE", "x", "handle")),
        ("google account", active_account(), src("KO_GOOGLE_ACCOUNT", "google", "account")),
        ("publish domain", publish.publish_domain() or "(workers.dev)", "config" if config.get("publish", "domain") else "default"),
    ]


def _versions_line() -> str:
    """What this install actually runs: a uv tool env can drift from the lock and the last tag."""
    import platform
    from importlib.metadata import PackageNotFoundError, version

    from .mcp_client import _MODERN

    def installed(dist: str) -> str:
        try:
            return version(dist)
        except PackageNotFoundError:
            return "not installed"

    return (
        f"ko {installed('kotools')} · Python {platform.python_version()} · "
        f"pydantic-ai {installed('pydantic-ai-slim')} · mcp {installed('mcp')} (MCP spec {_MODERN})"
    )


def _refs_line() -> str | None:
    """refs folder: count + disk weight, flagging clones heavy enough to care about."""
    from . import refs

    base = refs.refs_dir()
    sizes = refs.sizes(base) if base.is_dir() else []
    if not sizes:
        return None
    total = sum(b for _, b in sizes)
    big = [(n, b) for n, b in sizes if b > 500 * 1024 * 1024]
    line = f"refs: {len(sizes)} repos · {total / 1e9:.1f} GB at {base}"
    if big:
        line += "  [red]⚠ big: " + ", ".join(f"{n} {b / 1e9:.1f} GB" for n, b in big) + "[/red]"
    return line


def _balance_lines() -> list[str]:
    """Live balances: /credits is a free authenticated GET on both gateways, so this proves
    the key works and shows money left (every LLM call rides on one of them). Rich markup."""
    from . import billing

    out = []
    for check, key_env in ((billing.gateway, "VERCEL_AI_GATEWAY_API_KEY"), (billing.openrouter, "OPENROUTER_API_KEY")):
        if not config.key_source(key_env):
            continue
        b = check()  # never raises — errors land in b.error
        if b.error:
            out.append(f"[red]{b.provider}: key set but /credits failed — {b.error}[/red]")
        elif b.remaining is not None:
            low = "red" if b.remaining < 1 else "dim"
            out.append(
                f"[{low}]{b.provider}: key works — ${b.remaining:.2f} of ${b.total:.2f} credits left"
                + (f" ({b.trend})" if b.trend else "")
                + f"[/{low}]"
            )
        # A key can be capped below the account balance, so "credits left" reads healthy
        # while every call 403s. Say so loudly — that gap cost a debugging session.
        if b.key_capped:
            out.append(
                f"[red]{b.provider}: this key's ${b.key_limit:.0f} spend cap is used up "
                f"(${b.key_used:.2f}) — calls 403 until you raise it"
                + (" at https://openrouter.ai/settings/keys" if b.provider == "openrouter" else "")
                + ", even with credit left[/red]"
            )
        elif b.key_left is not None:
            out.append(f"[dim]{b.provider}: key cap ${b.key_limit:.0f}, ${b.key_left:.2f} left[/dim]")
    return out


@app.command("doctor")
def doctor(
    as_json: bool = typer.Option(
        False, "--json", help="emit JSON (tools, settings, dirs) — full values, no table truncation"
    ),
) -> None:
    """Setup health check: every tool, what it needs, and whether it's ready.

    --json: {tools:[{tool,does,needs,status}], settings:[{name,value,source}], dirs:{…},
    models:{count,providers}, config_error, online} — the table truncates long values, JSON never does."""
    from rich.console import Console
    from rich.table import Table

    from . import dirs, models
    from .cli_google import account_lines

    console = Console(quiet=as_json)  # --json: suppress the human output, emit one object at the end

    rows = _tool_rows()
    table = Table(title="ko doctor", title_justify="left")
    table.add_column("tool", style="bold")
    table.add_column("does")
    table.add_column("needs", style="dim", overflow="fold")
    table.add_column("status")
    for name, does, needs, (status, color) in rows:
        table.add_row(name, does, needs, f"[{color}]{status}[/{color}]")
    console.print(table)

    online = _online()
    if online:
        models.refresh_catalogs()  # populate/refresh the model catalog caches
    else:
        console.print("[yellow]offline — skipped the live checks (model catalog refresh, credit balances, account lookups)[/yellow]")
    extra = [m for m in models.available_models() if ":" in m]
    providers = sorted({m.split(":")[0] for m in extra})
    console.print(f"[dim]models available to -m: {len(extra)} across {', '.join(providers) or 'none'}[/dim]")

    settings = _settings()
    console.print("google accounts:")
    for line in account_lines(lookup=online):
        console.print("  " + line, highlight=False, markup=False, soft_wrap=True)
    console.print("[dim]settings: " + " · ".join(f"{name}={val} ({src})" for name, val, src in settings) + "[/dim]")
    console.print(f"[dim]dirs: config={dirs.config_dir()} · state={dirs.state_dir()} · cache={dirs.cache_dir()}[/dim]")
    console.print(f"[dim]versions: {_versions_line()}[/dim]", highlight=False)
    if err := config.config_error():
        console.print(f"[red]⚠ config.toml is malformed — keys/settings above are IGNORED: {err}[/red]")
    elif not (dirs.config_dir() / "config.toml").exists():
        console.print("[dim]no config.toml (env vars + baked defaults only)[/dim]")
    if refs_line := _refs_line():
        console.print(f"[dim]{refs_line}[/dim]")
    if online:
        for line in _balance_lines():
            console.print(line)

    if as_json:
        _emit_json(
            {
                "tools": [
                    {"tool": name, "does": does, "needs": needs, "status": status}
                    for name, does, needs, (status, _color) in rows
                ],
                "settings": [{"name": n, "value": v, "source": src} for n, v, src in settings],
                "dirs": {
                    "config": str(dirs.config_dir()),
                    "state": str(dirs.state_dir()),
                    "cache": str(dirs.cache_dir()),
                },
                "models": {"count": len(extra), "providers": providers},
                "config_error": config.config_error(),
                "online": online,
            }
        )
