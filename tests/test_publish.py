"""Offline tests for ko.publish — naming, in-folder config, scaffold modes. No wrangler/network."""

from ko import publish


def test_slugify():
    assert publish.slugify("Robot Arm!") == "robot-arm"
    assert publish.slugify("UPPER_case 99") == "upper-case-99"
    assert publish.slugify("") == "site"


def test_publish_domain_default(monkeypatch):
    monkeypatch.setattr(publish.config, "get", lambda *a, **k: None)
    assert publish.publish_domain() == "khalido.dev"  # repo default
    monkeypatch.setattr(publish.config, "get", lambda *a, **k: "example.com")
    assert publish.publish_domain() == "example.com"  # config.toml override wins


def test_resolve_name(tmp_path):
    folder = tmp_path / "robot-arm"
    folder.mkdir()
    assert publish.resolve_name(folder) == "robot-arm"  # folder slug
    assert publish.resolve_name(folder, "My Site") == "my-site"  # explicit, slugified
    (folder / "wrangler.jsonc").write_text('{ "name": "stuck" }')
    assert publish.resolve_name(folder) == "stuck"  # reads the existing config name


def test_worker_exists_none_without_creds(monkeypatch):
    monkeypatch.delenv("KO_CLOUDFLARE_API_TOKEN", raising=False)
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    monkeypatch.setattr(publish.config, "get", lambda *a, **k: None)
    assert publish.worker_exists("anything") is None  # can't tell → never blocks


def test_build_config_with_and_without_domain():
    cfg = publish.build_config("robot-arm", "khalido.dev")
    assert cfg["name"] == "robot-arm"
    assert cfg["assets"]["directory"] == "."
    assert cfg["assets"]["not_found_handling"] == "single-page-application"
    assert cfg["routes"][0]["pattern"] == "robot-arm.khalido.dev"
    assert "routes" not in publish.build_config("robot-arm", None)
    assert publish.build_config("x", None, spa=False)["assets"]["not_found_handling"] == "404-page"


def test_ensure_config_creates_respects_and_overrides(monkeypatch, tmp_path):
    monkeypatch.setattr(publish, "publish_domain", lambda: None)
    folder = tmp_path / "robot-arm"
    folder.mkdir()

    assert publish.ensure_config(folder) == "robot-arm"
    assert publish.config_name(folder) == "robot-arm"

    (folder / "wrangler.jsonc").write_text('{ "name": "custom-name" }')
    assert publish.ensure_config(folder) == "custom-name"  # respected (sticky)

    assert publish.ensure_config(folder, "My Site") == "my-site"  # --name rewrites
    assert publish.config_name(folder) == "my-site"


def test_parse_url_empty_when_absent(tmp_path):
    assert publish._parse_url("nothing here", tmp_path / "missing.ndjson") == ""


def test_scaffold_static(monkeypatch, tmp_path):
    monkeypatch.setattr(publish, "publish_domain", lambda: None)
    folder = tmp_path / "site"
    written = {p.name for p in publish.scaffold(folder, title="Robot Arm", mode="static")}
    assert {"index.html", "style.css", "app.js", "CLAUDE.md", ".gitignore", ".assetsignore", "wrangler.jsonc"} <= written
    # assets.directory is "." → meta/dev files must be excluded from the upload
    assert ".wrangler" in (folder / ".assetsignore").read_text()
    assert "wrangler.jsonc" in (folder / ".assetsignore").read_text()
    index = (folder / "index.html").read_text()
    assert "cdn.tailwindcss.com" in index
    assert "Robot Arm" in index and "{title}" not in index  # title substituted, no stray braces
    assert 'src="app.js"' in index  # heavy JS split into its own module
    assert ".card" in (folder / "style.css").read_text()  # starter component classes shipped
    (folder / "index.html").write_text("MINE")
    publish.scaffold(folder, mode="static")
    assert (folder / "index.html").read_text() == "MINE"  # never clobbers


def test_scaffold_md(monkeypatch, tmp_path):
    monkeypatch.setattr(publish, "publish_domain", lambda: None)
    folder = tmp_path / "docs"
    written = {p.name for p in publish.scaffold(folder, title="Robot Arm", mode="md")}
    assert {"README.md", "index.html", "style.css", "CLAUDE.md", ".assetsignore", "wrangler.jsonc"} <= written
    shell = (folder / "index.html").read_text()
    assert "markdown-it" in shell and "safePage" in shell
    # highlight.js must be the browser build — /lib/common.min.js is CommonJS (require()),
    # which throws in-browser and hangs the page on "loading…".
    assert "@highlightjs/cdn-assets" in shell and "highlight.js@11/lib/" not in shell
    assert "Robot Arm" in (folder / "README.md").read_text()
    assert '"not_found_handling": "404-page"' in (folder / "wrangler.jsonc").read_text()


def test_scaffold_bare(monkeypatch, tmp_path):
    monkeypatch.setattr(publish, "publish_domain", lambda: None)
    folder = tmp_path / "blank"
    written = {p.name for p in publish.scaffold(folder, mode="bare")}
    assert written == {"CLAUDE.md", ".gitignore", ".assetsignore", "wrangler.jsonc"}
    assert not (folder / "index.html").exists()  # bare: the agent builds it


def test_scaffold_hono(monkeypatch, tmp_path):
    monkeypatch.setattr(publish, "publish_domain", lambda: None)
    folder = tmp_path / "app"
    written = {str(p.relative_to(folder)) for p in publish.scaffold(folder, mode="hono", pin=True)}
    assert {"public/README.md", "src/index.ts", "package.json", "wrangler.jsonc"} <= written
    cfg = (folder / "wrangler.jsonc").read_text()
    assert '"main": "src/index.ts"' in cfg and "KO_PIN" in cfg
    assert "hono" in (folder / "package.json").read_text()
    assert publish.config_pin(folder) is not None  # --pin generated one
    worker = (folder / "src/index.ts").read_text()
    assert "/api/data" in worker and "caches.default" in worker  # cached data endpoint example


def test_scaffold_hono_open_without_pin(monkeypatch, tmp_path):
    monkeypatch.setattr(publish, "publish_domain", lambda: None)
    folder = tmp_path / "open"
    publish.scaffold(folder, mode="hono", pin=False)
    assert publish.config_pin(folder) is None  # no gate unless --pin


def test_run_worker_first_only_when_gated():
    gated = publish.build_hono_config("a", None, pin="123456")
    assert gated["assets"]["run_worker_first"] is True  # load-bearing: protects static assets
    open_ = publish.build_hono_config("a", None, pin=None)
    assert open_["assets"]["run_worker_first"] is False  # assets serve from edge, no worker hit


def test_ensure_hono_config_rename_preserves_worker_and_pin(monkeypatch, tmp_path):
    """Regression: renaming a Hono site must NOT rewrite it as a static config (which would
    drop `main`/`./public`/KO_PIN and expose the repo root)."""
    monkeypatch.setattr(publish, "publish_domain", lambda: None)
    folder = tmp_path / "app"
    publish.scaffold(folder, mode="hono", pin=True)
    pin = publish.config_pin(folder)

    publish.ensure_hono_config(folder, name="renamed")  # the --name path
    cfg = (folder / "wrangler.jsonc").read_text()
    assert '"main": "src/index.ts"' in cfg  # still a worker
    assert '"directory": "./public"' in cfg  # still serves public/, not repo root
    assert publish.config_pin(folder) == pin  # PIN carried forward
    assert publish.config_name(folder) == "renamed"


def test_set_pin_rotates_and_sets(monkeypatch, tmp_path):
    monkeypatch.setattr(publish, "publish_domain", lambda: None)
    folder = tmp_path / "app"
    publish.scaffold(folder, mode="hono", pin=True)

    new = publish.set_pin(folder, "new")
    assert new.isdigit() and len(new) == 6 and publish.config_pin(folder) == new
    assert publish.set_pin(folder, "424242") == "424242"
    assert publish.config_pin(folder) == "424242"
    assert '"main": "src/index.ts"' in (folder / "wrangler.jsonc").read_text()  # untouched


def test_set_pin_adds_gate_to_open_site(monkeypatch, tmp_path):
    monkeypatch.setattr(publish, "publish_domain", lambda: None)
    folder = tmp_path / "open"
    publish.scaffold(folder, mode="hono", pin=False)
    assert publish.config_pin(folder) is None

    publish.set_pin(folder, "111222")
    assert publish.config_pin(folder) == "111222"
    # adding the gate must flip run_worker_first on, else /README.md bypasses it
    assert '"run_worker_first": true' in (folder / "wrangler.jsonc").read_text()


def test_preview_runs_wrangler_dev(monkeypatch, tmp_path):
    folder = tmp_path / "site"
    folder.mkdir()
    (folder / "wrangler.jsonc").write_text('{ "name": "x" }')
    calls = {}
    monkeypatch.setattr(publish, "_wrangler", lambda: ["wrangler"])

    def fake_run(cmd, cwd, env):
        calls["cmd"], calls["cwd"] = cmd, cwd
        return type("R", (), {"returncode": 0})()

    monkeypatch.setattr(publish.subprocess, "run", fake_run)
    assert publish.preview(folder, port=4000) == 0
    assert calls["cmd"] == ["wrangler", "dev", "--port", "4000"]
    assert calls["cwd"] == str(folder)


def test_preview_requires_config(tmp_path):
    folder = tmp_path / "empty"
    folder.mkdir()
    try:
        publish.preview(folder)
        raise AssertionError("expected RuntimeError without wrangler.jsonc")
    except RuntimeError:
        pass


def test_set_pin_requires_worker(monkeypatch, tmp_path):
    monkeypatch.setattr(publish, "publish_domain", lambda: None)
    folder = tmp_path / "static"
    publish.scaffold(folder, mode="static")
    try:
        publish.set_pin(folder, "new")
        raise AssertionError("expected RuntimeError on a non-worker folder")
    except RuntimeError:
        pass


# --- Cloudflare reconcile + rm (offline: httpx mocked) ---


class _Resp:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_account_workers_sorted_and_domains(monkeypatch):
    monkeypatch.setattr(publish, "cf_creds", lambda: ("tok", "acct"))

    def fake_get(url, **kw):
        if url.endswith("/workers/scripts"):
            return _Resp(payload={"result": [{"id": "beta"}, {"id": "alpha"}]})
        return _Resp(payload={"result": [{"service": "alpha", "hostname": "alpha.example.com"}]})

    monkeypatch.setattr(publish.httpx, "get", fake_get)
    assert publish.account_workers() == ["alpha", "beta"]
    assert publish.worker_domains() == {"alpha": "alpha.example.com"}


def test_account_workers_none_without_creds(monkeypatch):
    monkeypatch.setattr(publish, "cf_creds", lambda: (None, None))
    assert publish.account_workers() is None  # can't tell — caller says so
    assert publish.worker_domains() == {}  # never blocks


def test_delete_worker_force_and_404(monkeypatch):
    monkeypatch.setattr(publish, "cf_creds", lambda: ("tok", "acct"))
    seen = {}

    def fake_delete(url, **kw):
        seen["url"], seen["params"] = url, kw.get("params")
        return _Resp(status_code=200)

    monkeypatch.setattr(publish.httpx, "delete", fake_delete)
    publish.delete_worker("mysite")  # no raise
    assert seen["url"].endswith("/workers/scripts/mysite")
    assert seen["params"] == {"force": "true"}  # detaches custom domains too

    monkeypatch.setattr(publish.httpx, "delete", lambda url, **kw: _Resp(status_code=404))
    try:
        publish.delete_worker("ghost")
        raise AssertionError("expected RuntimeError for a missing worker")
    except RuntimeError as e:
        assert "no Worker named" in str(e)


def test_forget_removes_registry_entry(monkeypatch, tmp_path):
    monkeypatch.setenv("KO_STATE_DIR", str(tmp_path))
    folder = tmp_path / "site"
    folder.mkdir()
    publish._record(folder, "site", "https://site.example.com")
    assert [p.name for p in publish.published()] == ["site"]
    assert publish.forget("site") == [str(folder)]
    assert publish.published() == []
    assert publish.forget("site") == []  # idempotent
