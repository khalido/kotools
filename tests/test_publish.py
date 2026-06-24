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
    assert {"index.html", "style.css", "CLAUDE.md", ".gitignore", "wrangler.jsonc"} <= written
    assert "cdn.tailwindcss.com" in (folder / "index.html").read_text()
    (folder / "index.html").write_text("MINE")
    publish.scaffold(folder, mode="static")
    assert (folder / "index.html").read_text() == "MINE"  # never clobbers


def test_scaffold_md(monkeypatch, tmp_path):
    monkeypatch.setattr(publish, "publish_domain", lambda: None)
    folder = tmp_path / "docs"
    written = {p.name for p in publish.scaffold(folder, title="Robot Arm", mode="md")}
    assert {"README.md", "index.html", "style.css", "CLAUDE.md", "wrangler.jsonc"} <= written
    shell = (folder / "index.html").read_text()
    assert "markdown-it" in shell and "safePage" in shell
    assert "Robot Arm" in (folder / "README.md").read_text()
    assert '"not_found_handling": "404-page"' in (folder / "wrangler.jsonc").read_text()


def test_scaffold_bare(monkeypatch, tmp_path):
    monkeypatch.setattr(publish, "publish_domain", lambda: None)
    folder = tmp_path / "blank"
    written = {p.name for p in publish.scaffold(folder, mode="bare")}
    assert written == {"CLAUDE.md", ".gitignore", "wrangler.jsonc"}
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


def test_scaffold_hono_open_without_pin(monkeypatch, tmp_path):
    monkeypatch.setattr(publish, "publish_domain", lambda: None)
    folder = tmp_path / "open"
    publish.scaffold(folder, mode="hono", pin=False)
    assert publish.config_pin(folder) is None  # no gate unless --pin
