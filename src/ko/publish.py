"""Publish a folder to Cloudflare as a static Workers site — and scaffold new ones.

v1 is deliberately small: static assets only, no build step. The published folder
is a **self-describing wrangler project** — it carries its own `wrangler.jsonc`
(name + assets + custom-domain route), so re-publishing is just `wrangler deploy`
in that folder: same name → same Worker → same URL. `ko publish` is a thin
orchestrator over exactly that; you can run `wrangler deploy` by hand identically.

Scaffold flavors (`ko publish new`):
- **static** (default) — `index.html` (Tailwind Play CDN + Alpine) + `style.css`.
- **md** (`--md`) — write markdown; a generic shell renders it client-side
  (markdown-it) with a dark theme + right-sidebar TOC. `README.md` is the nav hub;
  link pages with `[Title](?page=other.md)`. No build, no per-project HTML edits.
- **bare** (`--bare`) — just a `CLAUDE.md` of hints; the agent builds from scratch.

Set `[publish] domain` in `~/.config/ko/config.toml` for `<name>.<domain>` custom
domains; else `*.workers.dev`. Auth is wrangler's own (`CLOUDFLARE_API_TOKEN`).
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx

from ko import config
from ko.dirs import state_dir

COMPAT_DATE = "2026-06-01"
WRANGLER_CONFIG = "wrangler.jsonc"
# ko's default publish domain; any user can override via `[publish] domain` in config.toml.
DEFAULT_DOMAIN = "khalido.dev"


def publish_domain() -> str | None:
    """The domain for custom subdomains: `[publish] domain` in config.toml, else ko's default."""
    return config.get("publish", "domain") or DEFAULT_DOMAIN


@dataclass
class Published:
    name: str
    url: str
    folder: str
    updated_at: str


def slugify(name: str) -> str:
    """Worker-safe name: lowercase, alphanumeric + hyphens, trimmed, <=54 chars."""
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s[:54] or "site"


# --- wrangler resolution (repo-local pinned first) ---


def _repo_wrangler() -> Path:
    """The pinned wrangler from the repo's node_modules (present in dev/clone installs)."""
    return Path(__file__).resolve().parents[2] / "node_modules" / ".bin" / "wrangler"


def wrangler_source() -> str:
    """Where wrangler resolves from — for `ko doctor`. 'env'|'local'|'path'|'npx'|'none'."""
    if os.environ.get("KO_WRANGLER"):
        return "env"
    if _repo_wrangler().exists():
        return "local"
    if shutil.which("wrangler"):
        return "path"
    return "npx" if shutil.which("npx") else "none"


def _wrangler() -> list[str]:
    """Resolve the wrangler command. Prefer the repo-local pinned install (Cloudflare's
    recommendation; avoids `npx` pulling 'latest'); fall back to PATH, then a pinned npx."""
    if override := os.environ.get("KO_WRANGLER"):
        return [override]
    if (local := _repo_wrangler()).exists():
        return [str(local)]
    if shutil.which("wrangler"):
        return ["wrangler"]
    return ["npx", "--yes", "wrangler@4"]


def _npm_install(folder: Path) -> None:
    """Install a worker site's deps (Hono) before wrangler bundles it. Cached after first run."""
    if not (folder / "package.json").exists() or (folder / "node_modules").exists():
        return
    npm = shutil.which("npm") or "npm"
    proc = subprocess.run([npm, "install"], cwd=str(folder), capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError("npm install failed:\n" + (proc.stderr or proc.stdout))


def cf_creds() -> tuple[str | None, str | None]:
    """Cloudflare (token, account_id) for wrangler. ko's own `KO_CLOUDFLARE_*` vars win
    (kept distinct so they don't clash across multiple CF accounts), then config.toml
    `[publish]`, then wrangler's standard `CLOUDFLARE_*`."""
    token = (
        os.environ.get("KO_CLOUDFLARE_API_TOKEN")
        or config.get("publish", "api_token")
        or os.environ.get("CLOUDFLARE_API_TOKEN")
    )
    account = (
        os.environ.get("KO_CLOUDFLARE_ACCOUNT_ID")
        or config.get("publish", "account_id")
        or os.environ.get("CLOUDFLARE_ACCOUNT_ID")
    )
    return token, account


def _cf_env() -> dict:
    """os.environ + the resolved Cloudflare creds mapped to the names wrangler expects."""
    env = dict(os.environ)
    token, account = cf_creds()
    if token:
        env["CLOUDFLARE_API_TOKEN"] = token
    if account:
        env["CLOUDFLARE_ACCOUNT_ID"] = account
    return env


def worker_exists(name: str) -> bool | None:
    """Does a Worker named `name` already exist on the account? None if we can't tell
    (no creds / API error) — callers should not block on uncertainty."""
    token, account = cf_creds()
    if not (token and account):
        return None
    try:
        r = httpx.get(
            f"https://api.cloudflare.com/client/v4/accounts/{account}/workers/scripts/{name}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
    except Exception:
        return None
    if 200 <= r.status_code < 300:  # existing scripts answer 204 (No Content), not 200
        return True
    if r.status_code == 404:
        return False
    return None


# --- per-folder wrangler config (the source of truth) ---


def build_config(name: str, domain: str | None, spa: bool = True) -> dict:
    """The `wrangler.jsonc` written into the folder. Pure static: assets only, no `main`.
    `assets.directory` is "." — the config lives alongside the files it serves.

    spa=True serves index.html for any unmatched path (single-page apps). md sites fetch
    pages by name and need a real 404 so the shell's "couldn't load" path fires → spa=False.
    """
    cfg: dict = {
        "name": name,
        "compatibility_date": COMPAT_DATE,
        "assets": {
            "directory": ".",
            "not_found_handling": "single-page-application" if spa else "404-page",
        },
    }
    if domain:
        cfg["routes"] = [{"pattern": f"{name}.{domain}", "custom_domain": True}]
    return cfg


def build_hono_config(name: str, domain: str | None, pin: str | None = None) -> dict:
    """wrangler config for a Hono worker site: the worker (src/index.ts) runs first
    (`run_worker_first`), so it can add API routes, optionally gate behind a PIN, then serve
    the static assets in ./public via the ASSETS binding. PIN is active iff `KO_PIN` is set."""
    cfg: dict = {
        "name": name,
        "main": "src/index.ts",
        "compatibility_date": COMPAT_DATE,
        "assets": {
            "directory": "./public",
            "binding": "ASSETS",
            "run_worker_first": True,
            "not_found_handling": "404-page",
        },
    }
    if pin:
        cfg["vars"] = {"KO_PIN": pin}
    if domain:
        cfg["routes"] = [{"pattern": f"{name}.{domain}", "custom_domain": True}]
    return cfg


def _config_value(folder: Path, key: str) -> str | None:
    """Read a `"key": "value"` string from a folder's wrangler config (comment-safe regex)."""
    path = folder / WRANGLER_CONFIG
    if not path.exists():
        return None
    m = re.search(rf'"{key}"\s*:\s*"([^"]+)"', path.read_text())
    return m.group(1) if m else None


def config_name(folder: Path) -> str | None:
    """The Worker name from a folder's wrangler config, if present."""
    return _config_value(folder, "name")


def config_route(folder: Path) -> str | None:
    """The custom-domain route pattern from a folder's wrangler config, if it has one."""
    return _config_value(folder, "pattern")


def config_pin(folder: Path) -> str | None:
    """The PIN from a folder's wrangler config (`vars.KO_PIN`), if it's a gated site."""
    path = folder / WRANGLER_CONFIG
    if not path.exists():
        return None
    m = re.search(r'"KO_PIN"\s*:\s*"([^"]+)"', path.read_text())
    return m.group(1) if m else None


def resolve_name(folder: Path, name: str | None = None) -> str:
    """The Worker name we'd deploy `folder` under, WITHOUT writing anything: explicit
    `--name` wins (slugified), else the folder's existing config name, else a folder slug."""
    if name:
        return slugify(name)
    return config_name(folder) or slugify(folder.resolve().name)


def ensure_config(folder: Path, name: str | None = None, spa: bool = True) -> str:
    """Make sure `folder` has a wrangler config; create it if missing. Returns the name.

    An existing config is respected as-is unless `--name` is given (which rewrites it) —
    this is what makes re-publish sticky. If a config exists but we can't read its name,
    we warn and leave it untouched rather than silently overwrite the sticky name.
    """
    path = folder / WRANGLER_CONFIG
    if path.exists() and not name:
        existing = config_name(folder)
        if existing:
            return existing
        print(
            f"warning: couldn't read \"name\" from {path}; leaving it as-is "
            "(pass --name to regenerate).",
            file=sys.stderr,
        )
        return slugify(folder.resolve().name)
    final = slugify(name) if name else slugify(folder.resolve().name)
    text = json.dumps(build_config(final, publish_domain(), spa=spa), indent=2) + "\n"
    path.write_text(text)
    return final


def ensure_hono_config(folder: Path, name: str | None = None, pin: bool = False) -> str:
    """Like ensure_config but for a Hono worker site. With `pin=True`, generates a random
    6-digit PIN into `vars.KO_PIN` on creation. An existing config (and its PIN) is sticky."""
    path = folder / WRANGLER_CONFIG
    if path.exists() and not name:
        return config_name(folder) or slugify(folder.resolve().name)
    final = slugify(name) if name else slugify(folder.resolve().name)
    pin_val = config_pin(folder) or (f"{secrets.randbelow(1000000):06d}" if pin else None)
    text = json.dumps(build_hono_config(final, publish_domain(), pin_val), indent=2) + "\n"
    path.write_text(text)
    return final


# --- registry (a cache for `ko publish list`; the folder config is authoritative) ---


def _registry_file() -> Path:
    return state_dir() / "publish.json"


def registry() -> dict:
    try:
        return json.loads(_registry_file().read_text())
    except (OSError, ValueError):
        return {}


def _record(folder: Path, name: str, url: str) -> None:
    # keyed by absolute path — a deliberate single-user simplification (moving a folder
    # orphans its entry; re-publishing from the new path just makes a fresh one).
    reg = registry()
    reg[str(folder)] = {"name": name, "url": url, "updated_at": _now()}
    _registry_file().write_text(json.dumps(reg, indent=2))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def published() -> list[Published]:
    """Everything we've published, newest first."""
    rows = [
        Published(name=v["name"], url=v["url"], folder=k, updated_at=v.get("updated_at", ""))
        for k, v in registry().items()
    ]
    return sorted(rows, key=lambda p: p.updated_at, reverse=True)


# --- deploy ---


def check_url(url: str) -> int | None:
    """GET the published URL as a post-deploy sanity check. Returns the HTTP status, or
    None if unreachable. Non-fatal: a custom-domain cert can take 30-90s on first deploy."""
    try:
        return httpx.get(url, timeout=15, follow_redirects=True).status_code
    except Exception:
        return None


def _parse_url(stdout: str, out_file: Path) -> str:
    """Best-effort live URL from wrangler's ND-JSON output, then stdout. "" if not found."""
    try:
        for line in out_file.read_text().splitlines():
            obj = json.loads(line)
            for key in ("url", "deploy_url", "deployment_url"):
                if isinstance(obj.get(key), str) and obj[key].startswith("http"):
                    return obj[key]
    except (OSError, ValueError):
        pass
    m = re.search(r"https://\S+\.workers\.dev\S*", stdout)
    return m.group(0) if m else ""


def deploy(folder: Path, name: str | None = None, force: bool = False) -> str:
    """Deploy `folder` to Cloudflare via `wrangler deploy` run inside it. Returns the URL
    (or "" if wrangler succeeded but we couldn't parse one).

    Guards against silently overwriting someone else's Worker: wrangler does NOT warn if the
    name is already taken — it just overwrites. So before deploying a name this folder hasn't
    published before, we check the account and refuse (unless `force`) if it already exists.
    """
    folder = folder.resolve()
    if not folder.is_dir():
        raise RuntimeError(f"not a folder: {folder}")
    is_worker = _config_value(folder, "main") is not None  # pin/Hono sites serve via a Worker
    if not is_worker and not any(folder.glob("*.html")):
        raise RuntimeError(f"{folder} has no .html — run `ko publish new {folder}` first?")

    intended = resolve_name(folder, name)
    prev = registry().get(str(folder))
    ours = bool(prev) and prev.get("name") == intended  # this folder published it before
    if not ours and not force and worker_exists(intended) is True:
        raise RuntimeError(
            f"'{intended}' is already a Worker on your account, and this folder hasn't "
            f"published it before — deploying would take over its URL.\n"
            f"Use --force to take it over, or --name <other> to pick a different subdomain."
        )

    name = ensure_config(folder, name)
    _npm_install(folder)  # worker sites (Hono) need deps bundled before deploy
    with tempfile.TemporaryDirectory() as td:
        out_file = Path(td) / "out.ndjson"
        proc = subprocess.run(
            [*_wrangler(), "deploy"],
            cwd=str(folder),
            capture_output=True,
            text=True,
            env={**_cf_env(), "WRANGLER_OUTPUT_FILE_PATH": str(out_file)},
        )
        if proc.returncode != 0:
            raise RuntimeError(
                "wrangler deploy failed (set KO_CLOUDFLARE_API_TOKEN + "
                "KO_CLOUDFLARE_ACCOUNT_ID, or run `wrangler login`):\n"
                + (proc.stderr or proc.stdout)
            )
        # URL from the folder's ACTUAL config route (not the ambient domain, which may
        # not match a pre-existing wrangler.jsonc); else parse wrangler's workers.dev URL.
        route = config_route(folder)
        url = f"https://{route}" if route else _parse_url(proc.stdout, out_file)

    if url:
        _record(folder, name, url)
    return url


# --- scaffold ---


def scaffold(
    folder: Path, title: str | None = None, mode: str = "static", pin: bool = False
) -> list[Path]:
    """Drop a starter into `folder` (incl. its wrangler config). Never clobbers existing files.

    mode: 'static' (HTML+Alpine), 'md' (markdown doc), 'bare' (just a CLAUDE.md), or
    'hono' (a Hono worker over the doc in ./public — backend-ready; `pin=True` gates it).
    """
    folder.mkdir(parents=True, exist_ok=True)
    title = title or folder.resolve().name
    if mode == "md":
        files = {
            "README.md": _MD_README.replace("__TITLE__", title),
            "index.html": _MD_INDEX.replace("__TITLE__", title),
            "style.css": _MD_STYLE,
            "CLAUDE.md": _MD_CLAUDE.replace("__TITLE__", title),
            ".gitignore": _GITIGNORE,
        }
    elif mode == "bare":
        files = {
            "CLAUDE.md": _BARE_CLAUDE.replace("__TITLE__", title),
            ".gitignore": _GITIGNORE,
        }
    elif mode == "hono":
        # the doc/app lives in public/ (served via the ASSETS binding); the worker is src/
        files = {
            "public/README.md": _MD_README.replace("__TITLE__", title),
            "public/index.html": _MD_INDEX.replace("__TITLE__", title),
            "public/style.css": _MD_STYLE,
            "src/index.ts": _HONO_INDEX,
            "package.json": _HONO_PKG.replace("__NAME__", slugify(folder.resolve().name)),
            "CLAUDE.md": _HONO_CLAUDE.replace("__TITLE__", title),
            ".gitignore": _HONO_GITIGNORE,
        }
    else:
        files = {
            "index.html": _INDEX_HTML.format(title=title),
            "style.css": _STYLE_CSS,
            "CLAUDE.md": _CLAUDE_MD.format(title=title),
            ".gitignore": _GITIGNORE,
        }
    written = []
    for fname, content in files.items():
        p = folder / fname
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)  # public/, src/
            p.write_text(content)
            written.append(p)
    if not (folder / WRANGLER_CONFIG).exists():
        if mode == "hono":
            ensure_hono_config(folder, pin=pin)
        else:
            ensure_config(folder, spa=(mode != "md"))  # md fetches pages by name → real 404s
        written.append(folder / WRANGLER_CONFIG)
    return written


_GITIGNORE = ".wrangler/\n"

# --- static template (HTML + Tailwind + Alpine) ---

_INDEX_HTML = """\
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{title}</title>
    <!-- Tailwind (Play CDN — prototype-grade, zero build) -->
    <script src="https://cdn.tailwindcss.com"></script>
    <!-- Alpine for sprinkles of interactivity: x-data / @click / x-text / x-show -->
    <script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3.14.9/dist/cdn.min.js"></script>
    <!-- your own styles win over Tailwind (loaded last) -->
    <link rel="stylesheet" href="style.css" />
  </head>
  <body class="min-h-screen bg-zinc-950 text-zinc-100 antialiased">
    <main class="mx-auto max-w-3xl px-6 py-16" x-data="{{ count: 0 }}">
      <h1 class="text-4xl font-bold tracking-tight">{title}</h1>
      <p class="mt-3 text-zinc-400">
        Edit <code class="text-zinc-200">index.html</code>, then run
        <code class="text-zinc-200">ko publish</code> to update — same URL every time.
      </p>
      <button @click="count++" class="btn mt-8 rounded-lg px-4 py-2 font-medium">
        clicked <span x-text="count"></span> times
      </button>
    </main>
  </body>
</html>
"""

_STYLE_CSS = """\
/* Your styles. Tailwind handles utilities; put custom bits here — this file
   loads after Tailwind, so it wins on conflicts. One knob to start: */
:root {
  --accent: #6366f1; /* indigo-500 */
}

.btn {
  background: var(--accent);
  color: white;
}
.btn:hover {
  filter: brightness(1.1);
}
"""

_CLAUDE_MD = """\
# {title} — a ko-published static site

> Scaffolded by `ko publish`. **Make a genuinely useful site with what's here.** Need a
> capability this scaffold lacks? Ask Ko to extend `ko publish` rather than working around it.

Single-page static site, deployed to Cloudflare via `ko publish`. No build step.

## Stack
- **Tailwind** (Play CDN) — write utility classes directly in `class="..."`.
- **Alpine.js** — interactivity inline: `x-data`, `@click`, `x-text`, `x-show`.
- **style.css** — your own styles; loads after Tailwind so it wins. Tweak `--accent` first.

## Deploy
- `wrangler.jsonc` here describes this site. `ko publish` from this folder re-deploys to the **same URL**.
- Everything here is served at the site root; keep asset paths relative (`./img/...`).

## Going further (not yet wired)
- 3D / graphics: add **Three.js** via CDN + importmap (still no build).
- Backend / API / DB or a PIN gate: a Hono Worker (`--hono`, later). Refs: https://hono.dev/llms.txt
"""

# --- md template (write markdown, rendered client-side) ---

_MD_README = """\
# __TITLE__

This is the homepage — edit `README.md`. It's also the nav **hub**: link other pages
with query-param links, and create a matching `.md` file for each.

## Pages
- _example:_ `[Hardware](?page=hardware.md)` → then create `hardware.md`

## Writing
Write normal markdown. Every `##` / `###` heading shows up in the right-sidebar
table of contents automatically. Images: keep paths relative (`./img/x.png`).
"""

_MD_INDEX = """\
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>__TITLE__</title>
    <script src="https://cdn.jsdelivr.net/npm/markdown-it@14/dist/markdown-it.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/markdown-it-anchor@9/dist/markdownItAnchor.umd.js"></script>
    <link rel="stylesheet" href="style.css" />
  </head>
  <body>
    <header class="topbar">
      <strong class="brand">__TITLE__</strong>
      <a id="home" href="?page=README.md" hidden>&larr; Home</a>
    </header>
    <div class="layout">
      <article id="content" class="prose">loading&hellip;</article>
      <aside id="toc" class="toc"></aside>
    </div>
    <script>
      // Only fetch a local *.md file (no URLs, no path traversal).
      function safePage(p) {
        if (!p) return "README.md";
        p = p.replace(/^[./\\\\]+/, "");
        return /^[\\w-]+(\\/[\\w-]+)*\\.md$/.test(p) ? p : "README.md";
      }
      var page = safePage(new URLSearchParams(location.search).get("page"));
      if (page !== "README.md") document.getElementById("home").hidden = false;

      var md = window.markdownit({ html: false, linkify: true, typographer: true })
        .use(window.markdownItAnchor);

      fetch(page)
        .then(function (r) { if (!r.ok) throw r.status; return r.text(); })
        .then(function (text) {
          var content = document.getElementById("content");
          content.innerHTML = md.render(text);
          buildToc(content);
          var h1 = content.querySelector("h1");
          if (h1) document.title = h1.textContent;
        })
        .catch(function () {
          document.getElementById("content").innerHTML =
            "<p>Couldn't load <code>" + page + "</code>.</p>";
        });

      // Right-sidebar TOC from rendered h2/h3 (ids added by markdown-it-anchor).
      function buildToc(content) {
        var heads = content.querySelectorAll("h2[id], h3[id]");
        if (!heads.length) return;
        var ul = document.createElement("ul");
        heads.forEach(function (h) {
          var li = document.createElement("li");
          li.className = h.tagName.toLowerCase();
          var a = document.createElement("a");
          a.href = "#" + h.id;
          a.textContent = h.textContent;
          li.appendChild(a);
          ul.appendChild(li);
        });
        document.getElementById("toc").appendChild(ul);
      }
    </script>
  </body>
</html>
"""

_MD_STYLE = """\
:root {
  --accent: #6366f1;
  --bg: #0a0a0b;
  --fg: #e4e4e7;
  --muted: #a1a1aa;
  --border: #27272a;
  --code-bg: #18181b;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--fg);
  font: 16px/1.7 -apple-system, BlinkMacSystemFont, system-ui, sans-serif;
}
.topbar {
  position: sticky;
  top: 0;
  display: flex;
  gap: 1rem;
  align-items: center;
  padding: 0.8rem 1.2rem;
  background: var(--bg);
  border-bottom: 1px solid var(--border);
}
.topbar a { color: var(--accent); text-decoration: none; font-size: 0.9rem; }
.layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 15rem;
  gap: 2.5rem;
  max-width: 64rem;
  margin: 0 auto;
  padding: 2rem 1.2rem;
}
.prose { min-width: 0; }
.prose h1, .prose h2, .prose h3 { line-height: 1.25; margin: 1.6em 0 0.6em; }
.prose h1 { font-size: 2rem; margin-top: 0; }
.prose a { color: var(--accent); }
.prose code {
  background: var(--code-bg);
  padding: 0.15em 0.35em;
  border-radius: 4px;
  font-size: 0.9em;
}
.prose pre {
  background: var(--code-bg);
  border: 1px solid var(--border);
  padding: 1rem;
  border-radius: 8px;
  overflow: auto;
}
.prose pre code { background: none; padding: 0; }
.prose img { max-width: 100%; border-radius: 8px; }
.prose blockquote {
  margin: 1em 0;
  padding: 0.2em 1em;
  border-left: 3px solid var(--accent);
  color: var(--muted);
}
.prose table { border-collapse: collapse; }
.prose th, .prose td { border: 1px solid var(--border); padding: 0.4em 0.7em; }
.toc {
  position: sticky;
  top: 4rem;
  align-self: start;
  max-height: 80vh;
  overflow: auto;
  font-size: 0.9rem;
}
.toc ul { list-style: none; margin: 0; padding: 0; border-left: 1px solid var(--border); }
.toc li.h3 { padding-left: 1rem; }
.toc a {
  display: block;
  padding: 0.2em 0.8em;
  margin-left: -1px;
  color: var(--muted);
  text-decoration: none;
  border-left: 2px solid transparent;
}
.toc a:hover { color: var(--fg); border-left-color: var(--accent); }
@media (max-width: 760px) {
  .layout { grid-template-columns: 1fr; }
  .toc { display: none; }
}
"""

_MD_CLAUDE = """\
# __TITLE__ — a ko-published markdown site

> Scaffolded by `ko publish`. **Make a genuinely useful doc with what's here.** Need a
> capability this scaffold lacks? Ask Ko to extend `ko publish` rather than working around it.

A static doc site: write markdown, it renders **client-side** (markdown-it) with a
dark theme + right-sidebar TOC. No build step. Deployed to Cloudflare via `ko publish`.

## How it works
- `index.html` is a **generic shell — don't edit it.** It reads `?page=<file>.md` from
  the URL (default `README.md`), fetches that file, and renders it.
- **`README.md` is the homepage + nav hub.** Link pages with `[Title](?page=other.md)`.
- Add a page = drop `other.md` + link it from `README.md`. No HTML changes, ever.
- `##`/`###` headings auto-populate the right-sidebar TOC.
- `style.css` is the theme — tweak `--accent` etc.

## Publish
- `ko publish` from this folder re-deploys to the **same URL**.
- Raw `.md` is also served (`/other.md`) — agent / markdown-for-agents friendly.
- markdown-it reference: https://github.com/markdown-it/markdown-it
"""

# --- bare template (just hints; the agent builds from scratch) ---

_BARE_CLAUDE = """\
# __TITLE__ — a ko-published site (bare scaffold)

> Scaffolded by `ko publish`. **Make a genuinely useful site with what's here.** Need a
> capability this scaffold lacks? Ask Ko to extend `ko publish` rather than working around it.

An empty canvas, deployed to Cloudflare Workers as static assets via `ko publish`.
Build whatever you want. You just need an **`index.html`** at the root (everything
here is served at the site root); `ko publish` won't deploy until one exists.

## Publish
- Create `index.html` (+ any assets), then `ko publish` from this folder.
- Re-publishing overwrites the **same URL** (name is sticky via `wrangler.jsonc`).
- Keep asset paths relative (`./...`).

## No-build building blocks (all via CDN — no npm, no bundler)
- **Tailwind** (utility CSS): `<script src="https://cdn.tailwindcss.com"></script>`
- **Alpine** (interactivity): `https://cdn.jsdelivr.net/npm/alpinejs@3/dist/cdn.min.js`
- **markdown-it** (render markdown): `https://cdn.jsdelivr.net/npm/markdown-it/dist/markdown-it.min.js`
- **Three.js** (3D — e.g. a robot-arm viz): `https://cdn.jsdelivr.net/npm/three@0.160/build/three.module.js` via an importmap

## Going further
- Backend / API / DB or a PIN gate: a Hono Worker — `ko publish new <dir> --hono` (add `--pin`).
  Refs: https://hono.dev/llms.txt · https://hono.dev/docs/getting-started/cloudflare-workers
"""

# --- hono template (a Worker over the doc in public/; backend-ready, optional PIN) ---

_HONO_GITIGNORE = "node_modules/\n.wrangler/\n"

_HONO_PKG = """\
{
  "name": "__NAME__",
  "private": true,
  "type": "module",
  "dependencies": {
    "hono": "^4"
  }
}
"""

_HONO_INDEX = """\
import { Hono } from "hono";
import { getCookie, setCookie } from "hono/cookie";

type Bindings = { ASSETS: Fetcher; KO_PIN?: string };
const app = new Hono<{ Bindings: Bindings }>();

// PIN gate — active only when KO_PIN is set (the `--pin` flag sets it). Remove the var to open up.
app.use("*", async (c, next) => {
  const pin = c.env.KO_PIN;
  if (!pin || getCookie(c, "ko_pin") === pin) return next();
  if (c.req.method === "POST") {
    const body = await c.req.parseBody();
    if (body.pin === pin) {
      setCookie(c, "ko_pin", pin, {
        httpOnly: true, secure: true, sameSite: "Lax", path: "/", maxAge: 60 * 60 * 24 * 30,
      });
      return c.redirect(new URL(c.req.url).pathname);
    }
  }
  return c.html(pinPage(c.req.method === "POST"), 401);
});

// Your backend routes go here, e.g.:
// app.get("/api/hello", (c) => c.json({ hello: "world" }));

// Everything else → the static doc/app in ./public
app.all("*", (c) => c.env.ASSETS.fetch(c.req.raw));

export default app;

function pinPage(wrong: boolean): string {
  return `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Enter PIN</title>
<style>
  body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0a0a0b;
    color:#e4e4e7;font:16px/1.6 -apple-system,system-ui,sans-serif}
  form{display:grid;gap:.9rem;width:min(20rem,90vw);text-align:center}
  h1{font-size:1.3rem;margin:0}
  input{padding:.7rem;border-radius:8px;border:1px solid #27272a;background:#18181b;
    color:#e4e4e7;font-size:1.2rem;text-align:center;letter-spacing:.3em}
  button{padding:.7rem;border:0;border-radius:8px;background:#6366f1;color:#fff;cursor:pointer}
  .err{color:#f87171;font-size:.9rem;margin:0}
</style></head><body>
<form method="post"><h1>\\u{1F512} Enter PIN</h1>
<input name="pin" inputmode="numeric" autocomplete="off" autofocus placeholder="\\u2022\\u2022\\u2022\\u2022\\u2022\\u2022">
<button>Enter</button>${wrong ? '<p class="err">Wrong PIN \\u2014 try again.</p>' : ""}</form>
</body></html>`;
}
"""

_HONO_CLAUDE = """\
# __TITLE__ — a ko-published Hono worker site

> Scaffolded by `ko publish` — a ready-made tool to build & publish a site to Cloudflare.
> **Goal: make a genuinely useful site with what's here.** If you want a capability this
> scaffold doesn't have, ask Ko to extend the `ko publish` tool rather than working around it.

A Cloudflare Worker (Hono) serving the doc/app in `public/`, deployed via `ko publish`.
Backend-ready: add API routes, D1, R2 in `src/index.ts`. `ko publish` runs `npm install`
(once) then `wrangler deploy` — no build config to manage.

## Layout
- `public/` — the static site (markdown doc by default: edit `public/README.md`, the nav hub;
  link pages with `[Title](?page=other.md)`).
- `src/index.ts` — the Hono worker. Order: PIN gate -> your routes -> serve `public/` assets.
- `wrangler.jsonc` — `run_worker_first` so the worker sees every request before assets.

## Hono cheat-sheet (Cloudflare Workers)
```ts
type Bindings = { ASSETS: Fetcher; KO_PIN?: string; DB?: D1Database };  // your bindings
const app = new Hono<{ Bindings: Bindings }>();

app.get("/api/items/:id", (c) => {
  const id = c.req.param("id");          // path param
  const q = c.req.query("q");            // ?q=
  return c.json({ id, q });              // c.json / c.html / c.text / c.redirect / c.notFound
});
app.post("/api/x", async (c) => {
  const body = await c.req.parseBody();  // form; or await c.req.json()
  return c.text("ok");
});
app.use("*", async (c, next) => { /* before */ await next(); /* after */ });  // middleware
// bindings: c.env.DB, c.env.BUCKET, c.env.KO_PIN  (typed via <Bindings>)
// cookies:  import { getCookie, setCookie } from "hono/cookie"
// assets:   app.all("*", (c) => c.env.ASSETS.fetch(c.req.raw))  // keep this LAST
```

## PIN gate
- Active only when `vars.KO_PIN` is set in `wrangler.jsonc` (the `--pin` flag generates one).
- A soft access code (share a link + PIN), not real auth. Remove the var to make it public.

## Add a backend
- API route: add `app.get("/api/...")` ABOVE the catch-all asset handler in `src/index.ts`.
- D1 / R2 / KV: add the binding in `wrangler.jsonc`, use `c.env.<NAME>`.
- Errors: `app.onError((e, c) => c.json({ error: String(e) }, 500))`.
- Validate input with `@hono/zod-validator` once you have real API routes.

## Best practices (hono.dev)
- **Write handlers inline** with the route — extracting them to separate "controller"
  functions breaks path-param type inference (use `createHandlers` from `hono/factory` if needed).
- **Grow with `app.route()`** — feature files mounted onto the app, not one giant handler.
- Don't write `app.head(...)` — Hono auto-derives HEAD from GET.

## References
- On Workers: https://hono.dev/docs/getting-started/cloudflare-workers
- Best practices: https://hono.dev/docs/guides/best-practices
- Agent docs: https://hono.dev/llms.txt (index) · https://hono.dev/llms-small.txt (tight) · https://hono.dev/llms-full.txt (full)
"""
