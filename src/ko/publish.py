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


# generic publish-folder names → take the worker/domain from the PARENT project folder instead
_GENERIC_FOLDER_NAMES = {"publish", "site", "html", "www", "public", "dist", "out", "build", "pub", "web"}


def _default_name(folder: Path) -> str:
    """Worker name from the folder — or its parent when the folder is generically named, so
    `robot-arm/publish` → `robot-arm` (the project), not `publish`."""
    folder = folder.resolve()
    if folder.name.lower() in _GENERIC_FOLDER_NAMES and folder.parent.name:
        return slugify(folder.parent.name)
    return slugify(folder.name)


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
    """wrangler config for a Hono worker site: the worker (src/index.ts) serves API routes,
    optionally gates behind a PIN, then falls through to the static assets in ./public via the
    ASSETS binding. PIN is active iff `KO_PIN` is set.

    `run_worker_first` is on ONLY for gated sites — it's load-bearing for the PIN: without it
    Cloudflare serves a matching asset (e.g. /README.md) before the worker runs, bypassing the
    gate. For an open site we leave it off so static assets serve straight from the edge (no
    per-asset worker invocation); the worker still handles unmatched/`/api` routes via the catch-all.
    """
    cfg: dict = {
        "name": name,
        "main": "src/index.ts",
        "compatibility_date": COMPAT_DATE,
        "assets": {
            "directory": "./public",
            "binding": "ASSETS",
            "run_worker_first": bool(pin),
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
    return config_name(folder) or _default_name(folder)


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
        return _default_name(folder)
    final = slugify(name) if name else _default_name(folder)
    text = json.dumps(build_config(final, publish_domain(), spa=spa), indent=2) + "\n"
    path.write_text(text)
    return final


def _gen_pin() -> str:
    """A fresh random 6-digit gate PIN."""
    return f"{secrets.randbelow(1000000):06d}"


def ensure_hono_config(folder: Path, name: str | None = None, pin: bool = False) -> str:
    """Like ensure_config but for a Hono worker site. With `pin=True`, generates a random
    6-digit PIN into `vars.KO_PIN` on creation. An existing config (and its PIN) is sticky —
    a rename (`--name`) regenerates the config but carries the existing PIN forward."""
    path = folder / WRANGLER_CONFIG
    if path.exists() and not name:
        return config_name(folder) or _default_name(folder)
    final = slugify(name) if name else _default_name(folder)
    pin_val = config_pin(folder) or (_gen_pin() if pin else None)
    text = json.dumps(build_hono_config(final, publish_domain(), pin_val), indent=2) + "\n"
    path.write_text(text)
    return final


def set_pin(folder: Path, pin: str) -> str:
    """Set/rotate the gate PIN on a worker folder, rewriting `vars.KO_PIN` in place. `pin="new"`
    generates a random 6-digit one; any other value is used literally. Returns the resulting PIN.

    Only valid on a Hono/worker folder (one with `main` in its config). If the site has a
    `KO_PIN` already we patch just that value (preserving any hand-added D1/R2/KV bindings);
    otherwise we add the gate by regenerating the config from the template.
    """
    path = folder / WRANGLER_CONFIG
    if not path.exists() or _config_value(folder, "main") is None:
        raise RuntimeError(
            f"{folder} isn't a worker site (no Hono config) — only `--hono` sites take a PIN."
        )
    value = _gen_pin() if pin == "new" else pin
    text = path.read_text()
    if '"KO_PIN"' in text:  # patch the value, leave everything else (bindings, routes) untouched
        text = re.sub(r'("KO_PIN"\s*:\s*")[^"]*(")', lambda m: m.group(1) + value + m.group(2), text)
        path.write_text(text)
    else:  # no gate yet → rebuild from the template to add vars + flip run_worker_first on
        name = config_name(folder) or _default_name(folder)
        text = json.dumps(build_hono_config(name, publish_domain(), value), indent=2) + "\n"
        path.write_text(text)
    return value


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

    # route to the right config writer — a static rewrite over a Hono folder would drop
    # `main`/`./public`/`KO_PIN` and expose the repo root (esp. on a `--name` rename).
    name = ensure_hono_config(folder, name) if is_worker else ensure_config(folder, name)
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


def preview(folder: Path, port: int | None = None) -> int:
    """Run `wrangler dev` in `folder` — a local http preview that mirrors production: serves the
    static assets and, for Hono sites, runs the real worker (PIN gate + `/api/*` included). This is
    how to view a site as it builds — over http, so ES modules and `fetch()` work (a plain
    `file://` open does not). Long-running and interactive (Ctrl-C to stop); inherits the terminal.
    Returns wrangler's exit code.
    """
    folder = folder.resolve()
    if not folder.is_dir():
        raise RuntimeError(f"not a folder: {folder}")
    if not (folder / WRANGLER_CONFIG).exists():
        raise RuntimeError(f"{folder} has no {WRANGLER_CONFIG} — run `ko publish new {folder}` first?")
    _npm_install(folder)  # worker sites (Hono) need deps before wrangler can bundle
    cmd = [*_wrangler(), "dev"]
    if port:
        cmd += ["--port", str(port)]
    return subprocess.run(cmd, cwd=str(folder), env=_cf_env()).returncode


# --- scaffold ---


def scaffold(
    folder: Path,
    title: str | None = None,
    mode: str = "static",
    pin: bool = False,
    name: str | None = None,
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
            ".assetsignore": _ASSETSIGNORE,
        }
    elif mode == "bare":
        files = {
            "CLAUDE.md": _BARE_CLAUDE.replace("__TITLE__", title),
            ".gitignore": _GITIGNORE,
            ".assetsignore": _ASSETSIGNORE,
        }
    elif mode == "hono":
        # the doc/app lives in public/ (served via the ASSETS binding); the worker is src/
        files = {
            "public/README.md": _MD_README.replace("__TITLE__", title),
            "public/index.html": _MD_INDEX.replace("__TITLE__", title),
            "public/style.css": _MD_STYLE,
            "src/index.ts": _HONO_INDEX,
            "package.json": _HONO_PKG.replace("__NAME__", _default_name(folder)),
            "CLAUDE.md": _HONO_CLAUDE.replace("__TITLE__", title),
            ".gitignore": _HONO_GITIGNORE,
        }
    else:
        files = {
            "index.html": _INDEX_HTML.replace("__TITLE__", title),
            "style.css": _STYLE_CSS,
            "app.js": _APP_JS,
            "CLAUDE.md": _CLAUDE_MD.replace("__TITLE__", title),
            ".gitignore": _GITIGNORE,
            ".assetsignore": _ASSETSIGNORE,
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
            ensure_hono_config(folder, name=name, pin=pin)
        else:
            ensure_config(folder, name=name, spa=(mode != "md"))  # md → real 404s
        written.append(folder / WRANGLER_CONFIG)
    return written


_GITIGNORE = ".wrangler/\n"

# static/md/bare serve `assets.directory: "."`, so without this wrangler would upload the dev +
# meta files (the .wrangler preview dir, the config, CLAUDE.md) and serve them at the site root.
# Same syntax as .gitignore. (Hono doesn't need it — its assets dir is ./public, meta files sit outside.)
_ASSETSIGNORE = """\
.assetsignore
.gitignore
.wrangler
CLAUDE.md
wrangler.jsonc
node_modules
"""

# --- static template (HTML + Tailwind + Alpine) ---

_INDEX_HTML = """\
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>__TITLE__</title>
    <!-- For the EXTRAS only: Tailwind utilities + Alpine for small interactivity. -->
    <script src="https://cdn.tailwindcss.com"></script>
    <script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3.14.9/dist/cdn.min.js"></script>
    <!-- style.css sets clean base styles for plain HTML, and loads last so it wins. -->
    <link rel="stylesheet" href="style.css" />
  </head>
  <body>
    <main>
      <h1>__TITLE__</h1>
      <p>
        Write the page as clean, semantic HTML — <code>style.css</code> already styles
        headings, text, links, tables and code. Add a Tailwind class or some Alpine only
        where you want an <em>extra</em> (a coloured callout, a toggle, an accordion).
      </p>
      <p class="muted">
        Edit <code>index.html</code>, then run <code>ko publish</code> to update — same URL every time.
      </p>

      <!-- Heavy / interactive JS lives in its own module file, mounted here, so this HTML
           stays readable. app.js is a tiny working example — swap it for your real widget. -->
      <div id="app" class="my-8"></div>
    </main>
    <script type="module" src="app.js"></script>
  </body>
</html>
"""

_STYLE_CSS = """\
:root {
  --accent: #6366f1; /* indigo-500 — your one knob */
  --bg: #0a0a0b;
  --fg: #e4e4e7;
  --muted: #a1a1aa;
  --border: #27272a;
  --code-bg: #18181b;
}

/* Minimal base styles so plain, semantic HTML already looks good — no class on every
   element. Loads AFTER Tailwind, so these are the defaults and Tailwind utilities (or your
   own classes) still win wherever you add them. Style with clean HTML; reach for Tailwind/
   Alpine only for the extras. */
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--fg);
  font: 16px/1.7 -apple-system, BlinkMacSystemFont, system-ui, sans-serif;
}
main { max-width: 48rem; margin: 0 auto; padding: 3rem 1.25rem; }
h1, h2, h3 { line-height: 1.2; margin: 1.6em 0 0.5em; }
h1 { font-size: 2.25rem; margin-top: 0; letter-spacing: -0.02em; }
h2 { font-size: 1.5rem; }
a { color: var(--accent); }
.muted, small { color: var(--muted); }
code { background: var(--code-bg); padding: 0.15em 0.4em; border-radius: 4px; font-size: 0.9em; }
pre { background: var(--code-bg); border: 1px solid var(--border); padding: 1rem; border-radius: 8px; overflow: auto; }
pre code { background: none; padding: 0; }
table { border-collapse: collapse; }
th, td { border: 1px solid var(--border); padding: 0.4em 0.7em; }
blockquote { margin: 1em 0; padding: 0.2em 1em; border-left: 3px solid var(--accent); color: var(--muted); }
img { max-width: 100%; border-radius: 8px; }

/* A ready-made "extra" — the button in app.js uses it. Add more of your own below. */
.btn {
  background: var(--accent);
  color: #fff;
  border: 0;
  border-radius: 8px;
  cursor: pointer;
}
.btn:hover { filter: brightness(1.1); }

/* Two building blocks — a panel and a pill. Use them, restyle them, or ignore them and build
   something fully custom; they're a starting point, not a design system. The rule that keeps a
   page readable: repeating a class string 3+ times → promote it to a class like these. */
.card {
  border: 1px solid var(--border);
  background: rgba(24, 24, 27, 0.4);
  border-radius: 12px;
  padding: 1rem 1.2rem;
}
.pill {
  display: inline-block;
  border-radius: 999px;
  padding: 0.15em 0.7em;
  font-size: 0.8rem;
  background: rgba(99, 102, 241, 0.15); /* accent-tinted */
}
"""

# A tiny ES-module component — models the "keep index.html readable, heavy JS in its own file" pattern.
_APP_JS = """\
// app.js — your interactive / heavy code lives here, so index.html stays readable.
//
// The pattern for anything bigger than a few lines (a Three.js scene, a chart, a
// robot-arm viz): write it as an ES module, mount it into a <div id> in index.html,
// and load it with <script type="module" src="app.js">. Import libraries straight
// from a CDN — no build step:
//
//   import * as THREE from "https://esm.sh/three@0.160.0";
//
// Replace the demo below with your component.

function mount(el) {
  let count = 0;
  const btn = document.createElement("button");
  btn.className = "btn px-4 py-2 font-medium";
  const render = () => (btn.textContent = `clicked ${count} times`);
  btn.addEventListener("click", () => {
    count += 1;
    render();
  });
  render();
  el.appendChild(btn);
}

const el = document.getElementById("app");
if (el) mount(el);
"""

_CLAUDE_MD = """\
# __TITLE__ — a ko-published static site

> Scaffolded by `ko publish`. **Make a genuinely useful site with what's here.** Need a
> capability this scaffold lacks? Ask Ko to extend `ko publish` rather than working around it.

Single-page static site, deployed to Cloudflare via `ko publish`. No build step.

## How to style it
- **Write clean, semantic HTML.** `style.css` already styles headings, text, links, tables,
  code and blockquotes (dark theme — tweak `--accent` first). You shouldn't need a class on
  every element. **Keep body text on the bright default** — reserve grey (`.muted`, ~zinc-400)
  for captions/footnotes; greying prose down to zinc-500/600 makes it hard to read.
- **Tailwind** (Play CDN) — reach for utility classes only for the *extras*: layout, spacing,
  a coloured callout, a blinking heading.
- **Alpine.js** — small inline interactivity: `x-data`, `@click`, `x-show`, `x-text`
  (a toggle, an accordion).
- **A couple of helpers, not a framework** — `style.css` ships `.card` and `.pill` to get you
  started; use them, restyle them, or ignore them and build something fully custom. The one rule
  that keeps a page readable: repeating the same Tailwind class string 3+ times → promote it to a
  class in `style.css` instead of pasting it again.

## Keep the page readable — split heavy JS out
More than a few lines of JavaScript (a chart, a Three.js / robot-arm viz, anything stateful)?
Don't inline it. Put it in its own ES-module file and mount it:
```html
<div id="viz"></div>
<script type="module" src="viz.js"></script>
```
```js
// viz.js — import libs straight from a CDN, no build step
import * as THREE from "https://esm.sh/three@0.160.0";
export function mount(el) { /* ...build the widget... */ }
mount(document.getElementById("viz"));
```
`app.js` here is a tiny working example of exactly this — repurpose or delete it.

## Preview & deploy
- **Preview locally:** `npx wrangler dev` here → http://localhost:8787 (serves over http so
  `app.js`/ES modules load; refresh on save). Double-clicking `index.html` (`file://`) won't run
  ES modules — use the dev server.
- `wrangler.jsonc` here describes this site. `ko publish` from this folder re-deploys to the **same URL**.
- Everything here is served at the site root; keep asset paths relative (`./img/...`).

## Going further (not yet wired)
- 3D / graphics: **Three.js** via `https://esm.sh/three` in a module file (above) — still no build.
- Backend / API / DB or a PIN gate: a Hono Worker (`--hono`). Refs: https://hono.dev/llms.txt
"""

# --- md template (write markdown, rendered client-side) ---

_MD_README = """\
# __TITLE__

This is the homepage — edit `README.md`. It's also the nav **hub**: link other pages
with `[Title](?page=other.md)` and create a matching `.md` file for each.

## Pages
- _example:_ `[Hardware](?page=hardware.md)` → then create `hardware.md`

## Custom visuals
Raw HTML/SVG renders inline (Tailwind + Alpine are loaded) — generate bespoke charts,
diagrams, dashboards directly. More flexible than a chart library. Example:

<div class="my-6 rounded-lg border border-zinc-700 p-4" x-data="{ n: 0 }">
  <p>Clicked <span x-text="n"></span> times</p>
  <button class="mt-2 rounded bg-indigo-600 px-3 py-1 text-white" @click="n++">+1</button>
</div>

## Writing
Normal markdown for prose. `##`/`###` headings → the TOC; code blocks get syntax
highlighting. Images: relative paths (`./img/x.png`).
"""

_MD_INDEX = """\
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>__TITLE__</title>
    <!-- Share-card preview — edit og:description for your pitch link. -->
    <meta property="og:title" content="__TITLE__" />
    <meta property="og:description" content="__TITLE__" />
    <meta property="og:type" content="website" />
    <meta name="twitter:card" content="summary" />
    <link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>📄</text></svg>" />
    <link rel="stylesheet" href="style.css" />
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/highlight.js@11/styles/github-dark.min.css" />
    <!-- Tailwind (for inline HTML/SVG components you embed) + markdown-it (prose) -->
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdn.jsdelivr.net/npm/markdown-it@14/dist/markdown-it.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/markdown-it-anchor@9/dist/markdownItAnchor.umd.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/highlight.js@11/lib/common.min.js"></script>
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
    <script type="module">
      import Alpine from "https://cdn.jsdelivr.net/npm/alpinejs@3.14.9/dist/module.esm.js";
      window.Alpine = Alpine;

      // Only fetch a local *.md file (no URLs, no path traversal).
      function safePage(p) {
        if (!p) return "README.md";
        p = p.replace(/^[./\\\\]+/, "");
        return /^[\\w-]+(\\/[\\w-]+)*\\.md$/.test(p) ? p : "README.md";
      }
      const page = safePage(new URLSearchParams(location.search).get("page"));
      if (page !== "README.md") document.getElementById("home").hidden = false;

      const md = window.markdownit({
        html: true,  // embed custom HTML/SVG/Tailwind/Alpine components inline (you author the content)
        linkify: true,
        typographer: true,
        highlight: (str, lang) => {
          if (lang && window.hljs.getLanguage(lang)) {
            try {
              return '<pre><code class="hljs">' +
                window.hljs.highlight(str, { language: lang }).value + "</code></pre>";
            } catch (e) {}
          }
          return "";
        },
      }).use(window.markdownItAnchor);

      const content = document.getElementById("content");
      const res = await fetch(page).catch(() => null);
      if (!res || !res.ok) {
        content.innerHTML = "<p>Couldn't load <code>" + page + "</code>.</p>";
      } else {
        content.innerHTML = md.render(await res.text());
        const h1 = content.querySelector("h1");
        if (h1) document.title = h1.textContent;
        buildToc(content);
        Alpine.start();  // activate any inline Alpine (x-data) components in the rendered HTML
      }

      // Right-sidebar TOC from rendered h2/h3 (ids added by markdown-it-anchor).
      function buildToc(content) {
        const heads = content.querySelectorAll("h2[id], h3[id]");
        if (!heads.length) return;
        const ul = document.createElement("ul");
        heads.forEach((h) => {
          const li = document.createElement("li");
          li.className = h.tagName.toLowerCase();
          const a = document.createElement("a");
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
/* Starter building blocks for inline custom HTML (use, restyle, or ignore). Repeat a class
   string 3+ times in your markdown's HTML → promote it to a class like these. */
.card {
  border: 1px solid var(--border);
  background: rgba(24, 24, 27, 0.4);
  border-radius: 12px;
  padding: 1rem 1.2rem;
}
.pill {
  display: inline-block;
  border-radius: 999px;
  padding: 0.15em 0.7em;
  font-size: 0.8rem;
  background: rgba(99, 102, 241, 0.15); /* accent-tinted */
}
@media (max-width: 760px) {
  .layout { grid-template-columns: 1fr; }
  .toc { display: none; }
}

/* Print / "Save as PDF" — drop the chrome, go black-on-white, avoid mid-heading breaks. */
@media print {
  .topbar, .toc { display: none !important; }
  .layout { display: block; max-width: none; margin: 0; padding: 0; }
  body { background: #fff; color: #111; }
  .prose a { color: #111; text-decoration: underline; }
  .prose pre, .prose code { background: #f4f4f5; color: #111; border-color: #ddd; }
  .prose h1, .prose h2, .prose h3 { break-after: avoid; }
  .prose pre, .mermaid, .prose img, .prose table { break-inside: avoid; }
}
"""

_MD_CLAUDE = """\
# __TITLE__ — a ko-published markdown site

> Scaffolded by `ko publish`. **Make a genuinely useful doc with what's here.** Need a
> capability this scaffold lacks? Ask Ko to extend `ko publish` rather than working around it.

A static doc site: write markdown, it renders **client-side** (markdown-it) with a
dark theme + right-sidebar TOC. No build step. Deployed to Cloudflare via `ko publish`.

## How it works
- `index.html` is a **generic shell — don't edit it** (one exception below). It reads
  `?page=<file>.md` from the URL (default `README.md`), fetches it, and renders it.
- **`README.md` is the homepage + nav hub.** Link pages with `[Title](?page=other.md)`.
- Add a page = drop `other.md` + link it from `README.md`. No HTML changes.
- `##`/`###` headings auto-populate the right-sidebar TOC.
- `style.css` is the theme — tweak `--accent` etc. Keep body text bright; reserve grey (`.muted`,
  ~zinc-400) for captions/footnotes — don't grey prose down to zinc-500/600 (too faint to read).

## Visuals & frameworks (all loaded, no build — `html: true`, so embed HTML/SVG inline)
- **Tailwind** — utility classes on any inline HTML you write: `class="flex gap-4 rounded-lg p-4"`.
- **Alpine** — lightweight inline interactivity: `x-data`, `@click`, `x-show`, `x-text`.
  Start here: https://alpinejs.dev/start-here
- **Diagrams / infographics** (architecture, flows) → **hand-write inline `<svg>`**. No library —
  most flexible, and the thing an agent is good at.
- **Data charts** (bar/line/scatter/pie from real numbers) → **Chart.js, on that page only**:
  `<script src="https://cdn.jsdelivr.net/npm/chart.js/dist/chart.umd.min.js"></script>`
  then a `<canvas>` + `new Chart(ctx, {type, data, options})`. (Per-page keeps diagram pages lean.)
- **Heavy / stateful JS** (a Three.js viz, a big interactive widget) → don't paste it into the
  markdown. Put the code in its own file `viz.js`, and keep the prose clean with just a mount point:
  `<div id="viz"></div>` then `<script type="module" src="viz.js"></script>` (import libs from a
  CDN, e.g. `https://esm.sh/three`). Editing the doc's text then never means scrolling past the widget.

## Also
- **Starter classes** — `style.css` ships `.card` / `.pill` for inline custom HTML; repeat a
  class string 3+ times → promote it to a class there (keeps the markdown readable). Not a
  design system — restyle or ignore them.
- **Syntax highlighting** on fenced code blocks (highlight.js).
- **Print / PDF** — `style.css` has print rules; "Save as PDF" gives a clean, nav-free export.
- **Share card** — the ONE `index.html` edit worth making: set `og:description` (+ add
  `og:image`) so a shared link shows a title/description/preview.

## Preview & publish
- **Preview locally:** `npx wrangler dev` here → http://localhost:8787, then refresh on save.
  The page uses `fetch()` to load `.md`, which browsers block on `file://` — so preview via the
  dev server, not by double-clicking `index.html`.
- `ko publish` from this folder re-deploys to the **same URL**.
- Raw `.md` is also served (`/other.md`) — agent / markdown-for-agents friendly.
- markdown-it: https://github.com/markdown-it/markdown-it
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
// (Prefer a native browser login? Swap this block for `hono/basic-auth` — no styling, no logout.)
app.use("*", async (c, next) => {
  const pin = c.env.KO_PIN;
  if (!pin || getCookie(c, "ko_pin") === pin) return next();
  if (c.req.method === "POST") {
    const body = await c.req.parseBody();
    if (body.pin === pin) {
      setCookie(c, "ko_pin", pin, {
        // 90 days — a soft gate, so favour not re-prompting returning visitors. Bump freely.
        httpOnly: true, secure: true, sameSite: "Lax", path: "/", maxAge: 60 * 60 * 24 * 90,
      });
      return c.redirect(new URL(c.req.url).pathname);
    }
  }
  return c.html(pinPage(c.req.method === "POST"), 401);
});

// --- Cached data endpoint --------------------------------------------------
// A route the frontend GETs for data, with the upstream API cached at Cloudflare's edge.
// Lazy + access-driven: the upstream is hit at most once per TTL (per location) and refreshed
// on the first request after it expires — no cron, no KV. Tune per route: 60 = 1 min,
// 1800 = 30 min, 86400 = 1 day. Edit the fetch + reshape; point DATA_TTL at how fresh you need it.
const DATA_TTL = 1800; // 30 minutes

app.get("/api/data", async (c) => {
  const cache = caches.default;
  const key = new Request(new URL("/api/data", c.req.url)); // cache key = this URL
  const hit = await cache.match(key);
  if (hit) return hit;

  // Fetch + reshape your upstream here. API key? `wrangler secret put NAME` (encrypted, not
  // committed — unlike KO_PIN) and read c.env.NAME.
  const upstream = await fetch("https://api.example.com/prices?days=30");
  const data = await upstream.json();

  const res = Response.json(data, { headers: { "cache-control": `public, max-age=${DATA_TTL}` } });
  c.executionCtx.waitUntil(cache.put(key, res.clone())); // store without blocking the response
  return res;
});

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

**Preview locally:** `npx wrangler dev` here → http://localhost:8787 — runs the real worker (PIN
gate, `/api/*`) over http and reloads on save. Opening `public/index.html` via `file://` breaks the
markdown shell's `fetch()` and ES modules, so always preview through the dev server.

## Layout
- `public/` — the static site (markdown doc by default: edit `public/README.md`, the nav hub;
  link pages with `[Title](?page=other.md)`). Same client-side visuals as the `--md` scaffold:
  Tailwind + Alpine + inline `<svg>` for diagrams + Chart.js (per-page) for data charts, plus
  `.card`/`.pill` starter classes in `public/style.css`. Building fully custom HTML? Repeat a
  Tailwind string 3+ times → make it a class in `public/style.css` (keeps the markup readable).
- `src/index.ts` — the Hono worker. Order: PIN gate -> your routes -> serve `public/` assets.
- `wrangler.jsonc` — on a gated site `run_worker_first` is on so the worker sees every request
  before assets (required, or `/README.md` would bypass the PIN); open sites serve assets from
  the edge directly.

## Client components (no build)
The site in `public/` is no-build, so heavy client widgets are **plain ES modules**, not JSX.
Split anything bigger than a few lines out of the markdown/HTML into its own file — keeps the
page readable:
```html
<div id="viz"></div>
<script type="module" src="viz.js"></script>
```
Import libs from a CDN (`import * as THREE from "https://esm.sh/three@0.160.0"`). `hono/jsx/dom`
components need a bundler — only reach for them if you add a build step.

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
- A soft access code (share a link + PIN), not real auth — it's a plaintext Worker var. Remove
  the var to make it public.
- **Change it:** `ko publish --pin new` (random) or `ko publish --pin 123456` (specific), then it
  re-deploys with the new PIN. Safe to commit `KO_PIN` (private repo, soft gate).
- Want a hard wall instead of the styled PIN page? `hono/basic-auth` is ~3 lines — but it's a
  browser username/password dialog (unstyleable, no logout), so we default to the cookie/PIN gate.

## Cached data (external API → chart)
`src/index.ts` ships a ready `/api/data` route: it fetches an upstream API and caches the result
at Cloudflare's edge. **Lazy + access-driven** — the upstream is hit at most once per `DATA_TTL`
(per location) and refreshed on the first request after it expires. No cron, no KV binding. Point
it at your API, reshape the JSON, set `DATA_TTL` (`60` = 1 min, `1800` = 30 min, `86400` = 1 day).
- **API keys** → `wrangler secret put NAME` (encrypted, **not** committed — unlike `KO_PIN`), read `c.env.NAME`.
- **Frontend** — fetch it, draw with Chart.js (see the visuals note):
  ```js
  const data = await (await fetch("/api/data")).json();
  new Chart(canvas, { type: "line", data: toChart(data) });
  ```
- **Pure passthrough** (no reshape)? Skip the helper: `fetch(url, { cf: { cacheTtl: 1800, cacheEverything: true } })`.
- **Need a global / cross-deploy cache?** Add a KV binding in `wrangler.jsonc` and use `c.env.<KV>` —
  the edge cache above is per-location and clears on redeploy (fine for most dashboards).

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
