"""Offline tests for ko.publish — naming, in-folder config, scaffold modes. No wrangler/network."""

import subprocess

import pytest
from typer.testing import CliRunner

from ko import publish
from ko.cli import app


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
    assert (
        publish.build_config("x", None, spa=False)["assets"]["not_found_handling"]
        == "404-page"
    )


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
    written = {
        p.name for p in publish.scaffold(folder, title="Robot Arm", mode="static")
    }
    assert {
        "index.html",
        "style.css",
        "app.js",
        "CLAUDE.md",
        ".gitignore",
        ".assetsignore",
        "wrangler.jsonc",
    } <= written
    # assets.directory is "." → meta/dev files must be excluded from the upload
    ignore = (folder / ".assetsignore").read_text().splitlines()
    assert {".*", "!.well-known", "wrangler.jsonc"} <= set(ignore)
    index = (folder / "index.html").read_text()
    assert "@tailwindcss/browser@4" in index
    assert (
        "Robot Arm" in index and "{title}" not in index
    )  # title substituted, no stray braces
    assert 'src="app.js"' in index  # heavy JS split into its own module
    assert (
        ".card" in (folder / "style.css").read_text()
    )  # starter component classes shipped
    (folder / "index.html").write_text("MINE")
    publish.scaffold(folder, mode="static")
    assert (folder / "index.html").read_text() == "MINE"  # never clobbers


def test_scaffold_md(monkeypatch, tmp_path):
    monkeypatch.setattr(publish, "publish_domain", lambda: None)
    folder = tmp_path / "docs"
    written = {p.name for p in publish.scaffold(folder, title="Robot Arm", mode="md")}
    assert {
        "README.md",
        "index.html",
        "style.css",
        "CLAUDE.md",
        ".assetsignore",
        "wrangler.jsonc",
    } <= written
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
    written = {
        str(p.relative_to(folder))
        for p in publish.scaffold(folder, mode="hono", pin=True)
    }
    assert {
        "public/README.md",
        "src/index.ts",
        "package.json",
        "wrangler.jsonc",
    } <= written
    cfg = (folder / "wrangler.jsonc").read_text()
    assert '"main": "src/index.ts"' in cfg and "KO_PIN" in cfg
    assert "hono" in (folder / "package.json").read_text()
    assert publish.config_pin(folder) is not None  # --pin generated one
    worker = (folder / "src/index.ts").read_text()
    assert (
        "/api/data" in worker and "caches.default" in worker
    )  # cached data endpoint example


def test_scaffold_hono_open_without_pin(monkeypatch, tmp_path):
    monkeypatch.setattr(publish, "publish_domain", lambda: None)
    folder = tmp_path / "open"
    publish.scaffold(folder, mode="hono", pin=False)
    assert publish.config_pin(folder) is None  # no gate unless --pin


def test_run_worker_first_only_when_gated():
    gated = publish.build_hono_config("a", None, pin="123456")
    assert (
        gated["assets"]["run_worker_first"] is True
    )  # load-bearing: protects static assets
    open_ = publish.build_hono_config("a", None, pin=None)
    assert (
        open_["assets"]["run_worker_first"] is False
    )  # assets serve from edge, no worker hit


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
    assert (
        '"main": "src/index.ts"' in (folder / "wrangler.jsonc").read_text()
    )  # untouched


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

    def fake_run(cmd, cwd, env, check=False):
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
        return _Resp(
            payload={"result": [{"service": "alpha", "hostname": "alpha.example.com"}]}
        )

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

    monkeypatch.setattr(
        publish.httpx, "delete", lambda url, **kw: _Resp(status_code=404)
    )
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


# --- llms.txt generation ---


def test_parse_front_matter():
    meta, body = publish._parse_front_matter(
        '---\ntitle: My Page\ndesc: "quoted"\ntags: a, b\n---\n\n# Hi\n'
    )
    assert meta == {"title": "My Page", "desc": "quoted", "tags": "a, b"}
    assert body.startswith("# Hi")
    meta, body = publish._parse_front_matter("# no front matter\n")
    assert meta == {} and body.startswith("# no")


def test_page_info_fallbacks_and_truncation(tmp_path):
    page = tmp_path / "notes.md"
    page.write_text("# The Title\n\n> a quote to skip\n\n" + "word " * 60 + "\n")
    info = publish._page_info(page)
    assert info["title"] == "The Title"
    assert info["desc"].endswith("…")  # first prose paragraph, truncated at ~200 chars
    assert len(info["desc"]) <= 205
    page.write_text("---\ntitle: Override\nsummary: short and sweet\n---\n# Ignored\n")
    info = publish._page_info(page)
    assert (info["title"], info["desc"]) == ("Override", "short and sweet")


def test_generate_llms_txt_tiny_site_inlines_content(monkeypatch, tmp_path):
    monkeypatch.setattr(publish, "publish_domain", lambda: None)
    folder = tmp_path / "docs"
    publish.scaffold(folder, title="My Docs", mode="md")
    text = (folder / "llms.txt").read_text()  # scaffold seeds it
    assert text.startswith("# My Docs")
    assert "## Pages" in text and "(/README.md)" in text
    assert publish.LLMS_MARKER in text
    assert "## My Docs (/README.md)" in text  # ≤2 pages → content inlined


def test_generate_llms_txt_many_pages_index_only(monkeypatch, tmp_path):
    monkeypatch.setattr(publish, "publish_domain", lambda: None)
    folder = tmp_path / "docs"
    publish.scaffold(folder, title="Big", mode="md")
    (folder / "a.md").write_text("---\ndescription: page a\ntags: x, y\n---\n# A\n")
    (folder / "b.md").write_text("# B\n\nAbout b.\n")
    text = publish.generate_llms_txt(folder)
    assert "- [A](/a.md): page a (x, y)" in text  # front matter desc + tags
    assert "- [B](/b.md): About b." in text  # H1 + first-paragraph fallback
    assert "CLAUDE.md" not in text  # agent instructions aren't content
    assert "## A (/a.md)" not in text  # >2 pages → no inline excerpts


def test_write_llms_txt_respects_hand_authored(monkeypatch, tmp_path):
    monkeypatch.setattr(publish, "publish_domain", lambda: None)
    folder = tmp_path / "docs"
    publish.scaffold(folder, title="Docs", mode="md")
    (folder / "llms.txt").write_text("# mine, hands off\n")  # no marker
    assert publish.write_llms_txt(folder) is None
    assert (folder / "llms.txt").read_text() == "# mine, hands off\n"


def test_write_llms_txt_skips_non_md_sites(monkeypatch, tmp_path):
    monkeypatch.setattr(publish, "publish_domain", lambda: None)
    folder = tmp_path / "static"
    publish.scaffold(folder, mode="static")
    assert publish.write_llms_txt(folder) is None
    assert not (folder / "llms.txt").exists()


def test_hono_llms_txt_lives_in_public(monkeypatch, tmp_path):
    monkeypatch.setattr(publish, "publish_domain", lambda: None)
    folder = tmp_path / "app"
    publish.scaffold(folder, title="App", mode="hono")
    assert (folder / "public" / "llms.txt").exists()
    assert publish.content_dir(folder) == folder / "public"


def test_md_shell_js_is_valid(monkeypatch, tmp_path):
    """The md shell's inline module script must be real JS — Python string templates
    eat unescaped \\n (regression: front-matter strip broke the shell; earlier c785b58)."""
    import re as _re
    import shutil as _shutil
    import subprocess as _subprocess

    monkeypatch.setattr(publish, "publish_domain", lambda: None)
    folder = tmp_path / "docs"
    publish.scaffold(folder, title="Docs", mode="md")
    shell = (folder / "index.html").read_text()
    assert 'startsWith("---\\n")' in shell  # the escape survived Python templating
    node = _shutil.which("node")
    if not node:
        return  # escape assertion above still guards the known failure mode
    m = _re.search(r'<script type="module">(.*?)</script>', shell, _re.DOTALL)
    assert m, "md shell has no module script"
    proc = _subprocess.run(
        [node, "--check", "--input-type=module", "-"],
        input=m.group(1),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"md shell JS is broken:\n{proc.stderr}"


def test_strip_html_keeps_text_and_fences():
    md = (
        '<div class="grid">\n  <div class="card"><strong>ko fetch</strong> — any URL. '
        "<code>ko &lt;url&gt;</code> for short.</div>\n</div>\n\n"
        "See <https://example.com> too.\n\n```html\n<div>kept verbatim</div>\n```\n"
    )
    out = publish._strip_html(md)
    assert (
        "ko fetch — any URL. ko <url> for short." in out
    )  # tags gone, entities unescaped
    assert 'class="grid"' not in out and "<strong>" not in out
    assert "See https://example.com too." in out  # autolink keeps its URL
    assert "<div>kept verbatim</div>" in out  # fenced code untouched


def test_llms_txt_excerpt_has_no_html(monkeypatch, tmp_path):
    monkeypatch.setattr(publish, "publish_domain", lambda: None)
    folder = tmp_path / "docs"
    publish.scaffold(folder, title="Docs", mode="md")
    (folder / "README.md").write_text(
        '# Docs\n\nIntro line.\n\n<div class="card"><strong>ko x</strong> — tool.</div>\n'
    )
    text = publish.generate_llms_txt(folder)
    assert "ko x — tool." in text
    assert "<div" not in text and "<strong>" not in text


def test_write_llms_txt_drops_headers_file_once(monkeypatch, tmp_path):
    monkeypatch.setattr(publish, "publish_domain", lambda: None)
    folder = tmp_path / "docs"
    publish.scaffold(folder, title="Docs", mode="md")  # scaffold → write_llms_txt ran
    hdrs = folder / "_headers"
    assert "charset=utf-8" in hdrs.read_text()
    hdrs.write_text("/custom\n  X-Mine: yes\n")  # hand-written wins forever
    publish.write_llms_txt(folder)
    assert hdrs.read_text() == "/custom\n  X-Mine: yes\n"


def test_strip_html_preserves_inline_code_spans():
    out = publish._strip_html("An agent runs `ko fetch <url>` on <b>every</b> call.\n")
    assert "`ko fetch <url>`" in out  # placeholder inside inline code survives
    assert "every call" in out and "<b>" not in out


def test_strip_html_multiline_tag_and_script_body():
    md = (
        '<div class="a"\n     id="b">kept</div>\n\n<script>\nconst x = `y`;\nboom();\n</script>\n'
        "<style>\n.a { color: red }\n</style>\n\nSee `<script>` here and 1 < 2 and\n\n3 > 2.\n"
    )
    out = publish._strip_html(md)
    assert "kept" in out and "class=" not in out and "id=" not in out
    assert "boom" not in out and "color: red" not in out
    assert "`<script>`" in out  # code stays verbatim
    assert "1 < 2 and\n\n3 > 2." in out  # a stray < isn't a tag


# --- the Hono template ---


def test_pin_redirect_keeps_query_and_stays_on_site():
    assert "c.redirect(c.req.url, 303)" in publish._HONO_INDEX
    assert ".pathname" not in publish._HONO_INDEX  # //evil.com as a Location leaves the site


def test_api_data_template_never_caches_upstream_errors():
    src = publish._HONO_INDEX
    assert "upstream.ok" in src
    assert src.index("upstream.ok") < src.index("cache.put")


def test_templates_preview_with_ko_and_current_libs():
    templates = [
        publish._CLAUDE_MD,
        publish._MD_CLAUDE,
        publish._HONO_CLAUDE,
        publish._BARE_CLAUDE,
        publish._INDEX_HTML,
        publish._MD_INDEX,
    ]
    joined = "\n".join(templates)
    for stale in ("npx wrangler dev", "cdn.tailwindcss.com", "three@0.160", "alpinejs@3.14"):
        assert stale not in joined
    assert "chart.js@4/" in publish._MD_CLAUDE


def test_md_shell_runs_page_scripts_and_layers_css():
    assert "await runScripts(content)" in publish._MD_INDEX  # innerHTML never runs <script>
    assert "github-dark.min.css\") layer(base)" in publish._MD_INDEX
    for css in (publish._MD_STYLE, publish._STYLE_CSS):  # unlayered CSS would beat utilities
        assert "@layer theme, base, site, components, utilities;" in css


def test_title_is_escaped_in_html_only(monkeypatch, tmp_path):
    monkeypatch.setattr(publish, "publish_domain", lambda: None)
    folder = tmp_path / "docs"
    publish.scaffold(folder, title='A & "B" <i>', mode="md")
    index = (folder / "index.html").read_text()
    assert "A &amp; &quot;B&quot; &lt;i&gt;" in index and "<i>" not in index
    assert '# A & "B" <i>' in (folder / "README.md").read_text()


# --- config: JSONC, edited in place ---

_HAND_EDITED = """\
// my site
{
  // "name": "commented-out",
  "d1_databases": [{ "binding": "DB", "name": "db-name", "database_id": "x" }],
  "name": "real", /* keep me */
  "main": "src/index.ts",
  "assets": { "directory": "./public", "binding": "ASSETS", "run_worker_first": false, },
  "routes": [{ "pattern": "real.example.com", "custom_domain": true }],
}
"""


def _parsed(folder):
    data, _ = publish._parse_jsonc((folder / "wrangler.jsonc").read_text())
    return data


def _hand_edited(tmp_path, text=_HAND_EDITED):
    folder = tmp_path / "app"
    folder.mkdir()
    (folder / "wrangler.jsonc").write_text(text)
    return folder


def test_config_name_ignores_comments_and_nested_names(tmp_path):
    folder = _hand_edited(tmp_path)
    assert publish.config_name(folder) == "real"
    assert publish.config_route(folder) == "real.example.com"


def test_broken_config_fails_loud(tmp_path):
    folder = _hand_edited(tmp_path, '{ "name": ')
    with pytest.raises(publish.PublishError, match="can't parse"):
        publish.config_name(folder)


def test_add_pin_keeps_bindings_and_comments(tmp_path):
    folder = _hand_edited(tmp_path)
    publish.set_pin(folder, 'a"b')
    text = (folder / "wrangler.jsonc").read_text()
    assert "// my site" in text and "/* keep me */" in text and "db-name" in text
    cfg = _parsed(folder)
    assert cfg["vars"]["KO_PIN"] == 'a"b' == publish.config_pin(folder)  # escaped, parses
    assert cfg["assets"]["run_worker_first"] is True
    publish.set_pin(folder, "424242")  # the rotate path, on the same file
    assert publish.config_pin(folder) == "424242"


def test_empty_pin_refused(tmp_path):
    folder = _hand_edited(tmp_path)
    with pytest.raises(publish.PublishError, match="empty"):
        publish.set_pin(folder, "")


def test_rename_keeps_bindings_comments_and_moves_route(tmp_path):
    folder = _hand_edited(tmp_path)
    assert publish.ensure_config(folder, "New Name") == "new-name"
    text = (folder / "wrangler.jsonc").read_text()
    assert "// my site" in text and "db-name" in text
    assert publish.config_name(folder) == "new-name"
    assert publish.config_route(folder) == "new-name.example.com"


def test_rename_keeps_md_site_404_mode(monkeypatch, tmp_path):
    monkeypatch.setattr(publish, "publish_domain", lambda: None)
    folder = tmp_path / "docs"
    publish.scaffold(folder, mode="md")
    publish.ensure_config(folder, "renamed")
    assert _parsed(folder)["assets"]["not_found_handling"] == "404-page"


def test_empty_domain_means_workers_dev(monkeypatch):
    monkeypatch.setattr(publish.config, "get", lambda *a, **k: "")
    assert publish.publish_domain() is None


# --- deploy (subprocess stubbed) ---


@pytest.fixture
def site(monkeypatch, tmp_path):
    """A plain folder with a page, wrangler stubbed to succeed; `.calls` records its commands."""
    monkeypatch.setenv("KO_STATE_DIR", str(tmp_path / "state"))
    (tmp_path / "state").mkdir()
    monkeypatch.setattr(publish, "publish_domain", lambda: "example.com")
    monkeypatch.setattr(publish, "_wrangler", lambda: ["wrangler"])
    monkeypatch.setattr(publish, "cf_creds", lambda: ("tok", "acct"))
    monkeypatch.setattr(publish, "worker_exists", lambda name: False)
    folder = tmp_path / "mysite"
    folder.mkdir()
    (folder / "index.html").write_text("<h1>hi</h1>")
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(publish.subprocess, "run", fake_run)
    return type("Site", (), {"path": folder, "calls": calls})


def test_deploy_unscaffolded_writes_ignore_config_and_registry(site):
    url = publish.deploy(site.path)
    assert url == "https://mysite.example.com"
    assert site.calls == [["wrangler", "deploy"]]
    ignore = (site.path / ".assetsignore").read_text().splitlines()
    assert {".*", "!.well-known", "wrangler.jsonc"} <= set(ignore)
    assert publish.registry()[str(site.path.resolve())]["name"] == "mysite"


def test_deploy_keeps_own_lines_below_the_list_and_refreshes_it(site):
    ignore = site.path / ".assetsignore"
    ignore.write_text("notes/\n!.env\n")
    publish.deploy(site.path)
    text = ignore.read_text()
    assert text.startswith(publish._ASSETSIGNORE) and text.endswith("notes/\n!.env\n")
    publish.deploy(site.path)
    assert ignore.read_text() == text  # a second run adds nothing


def test_deploy_replaces_an_older_list(site):
    ignore = site.path / ".assetsignore"
    ignore.write_text(f"# an older list\n.git\n{publish._IGNORE_END}mine.txt\n")
    publish.deploy(site.path)
    assert ignore.read_text() == f"{publish._ASSETSIGNORE}mine.txt\n"


def test_guard_looks_in_the_served_folder_of_a_worker(site):
    (site.path / ".env").write_text("X=1")  # outside ./public: never uploaded
    (site.path / "public").mkdir()
    (site.path / "wrangler.jsonc").write_text(
        '{"name": "w", "main": "src/index.ts", "assets": {"directory": "./public"}}'
    )
    publish.deploy(site.path)
    assert (site.path / "public" / ".assetsignore").exists()
    assert not (site.path / ".assetsignore").exists()


def test_refused_takeover_changes_nothing(site, monkeypatch):
    monkeypatch.setattr(publish, "worker_exists", lambda name: True)
    (site.path / "wrangler.jsonc").write_text(
        '{"name": "taken", "main": "src/index.ts", "assets": {"directory": "."}}'
    )
    before = (site.path / "wrangler.jsonc").read_text()
    with pytest.raises(publish.PublishError, match="already a Worker") as e:
        publish.deploy(site.path, pin="123456")
    assert e.value.code == "usage"
    assert (site.path / "wrangler.jsonc").read_text() == before
    assert site.calls == []


def test_force_takeover_drops_other_registry_rows(site, tmp_path):
    other = tmp_path / "other"
    publish._record(other, "mysite", "https://old")
    publish.deploy(site.path, force=True)
    assert [p.folder for p in publish.published()] == [str(site.path.resolve())]


@pytest.mark.parametrize(
    ("stderr", "token", "code", "hint"),
    [
        ("You are not authenticated. Please run `wrangler login`.", None, "auth", True),
        ("Authentication error [code: 10000]", "tok", "auth", False),
        ("Build failed: src/index.ts", None, "error", False),
    ],
)
def test_auth_hint_only_when_creds_missing(site, monkeypatch, stderr, token, code, hint):
    monkeypatch.setattr(publish, "cf_creds", lambda: (token, None))
    monkeypatch.setattr(
        publish.subprocess,
        "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", stderr),
    )
    with pytest.raises(publish.PublishError) as e:
        publish.deploy(site.path)
    assert e.value.code == code
    assert ("KO_CLOUDFLARE_API_TOKEN" in str(e.value)) is hint


def test_missing_tools_are_clean_errors(monkeypatch, tmp_path):
    monkeypatch.setattr(publish.shutil, "which", lambda name: None)
    monkeypatch.setattr(publish, "_repo_wrangler", lambda: None)
    monkeypatch.delenv("KO_WRANGLER", raising=False)
    with pytest.raises(publish.PublishError, match="Node.js") as e:
        publish._wrangler()
    assert e.value.code == "config"
    monkeypatch.setenv("KO_WRANGLER", "/nope/wrangler")
    with pytest.raises(publish.PublishError, match="KO_WRANGLER"):
        publish._wrangler()
    (tmp_path / "package.json").write_text("{}")
    with pytest.raises(publish.PublishError, match="npm"):
        publish._npm_install(tmp_path)


@pytest.mark.parametrize("outcome", [130, KeyboardInterrupt])
def test_preview_ctrl_c_is_a_normal_stop(monkeypatch, tmp_path, outcome):
    (tmp_path / "wrangler.jsonc").write_text('{"name": "x"}')
    monkeypatch.setattr(publish, "_wrangler", lambda: ["wrangler"])

    def fake_run(cmd, **kw):
        if outcome is KeyboardInterrupt:
            raise KeyboardInterrupt
        return subprocess.CompletedProcess(cmd, outcome)

    monkeypatch.setattr(publish.subprocess, "run", fake_run)
    assert publish.preview(tmp_path) == 0


# --- the commands ---


@pytest.fixture
def cli(monkeypatch, tmp_path):
    monkeypatch.setenv("KO_STATE_DIR", str(tmp_path / "state"))
    (tmp_path / "state").mkdir()
    monkeypatch.setattr(publish, "publish_domain", lambda: None)
    monkeypatch.chdir(tmp_path)
    deleted = []
    monkeypatch.setattr(publish, "cf_creds", lambda: ("tok", "acct"))
    monkeypatch.setattr(
        publish.httpx,
        "delete",
        lambda url, **kw: deleted.append(url) or _Resp(status_code=200),
    )
    return type("Cli", (), {"run": CliRunner().invoke, "deleted": deleted})


@pytest.mark.parametrize("target", ["", "  ", "./foo", "../foo", "foo/bar", "..", ".", "~/foo"])
def test_rm_path_looking_target_never_deletes(cli, target):
    result = cli.run(app, ["publish", "rm", target, "--yes"])
    assert result.exit_code == 2, result.output
    assert cli.deleted == []


def test_rm_existing_folder_without_config_is_an_error(cli, tmp_path):
    (tmp_path / "foo").mkdir()
    result = cli.run(app, ["publish", "rm", "./foo", "--yes"])
    assert result.exit_code == 2
    assert cli.deleted == []


def test_rm_folder_uses_its_top_level_name_not_a_commented_one(cli, tmp_path):
    _hand_edited(tmp_path)
    result = cli.run(app, ["publish", "rm", "app", "--yes"])
    assert result.exit_code == 0, result.output
    assert [u.rsplit("/", 1)[-1] for u in cli.deleted] == ["real"]


def test_rm_plain_worker_name(cli):
    assert cli.run(app, ["publish", "rm", "foo", "--yes"]).exit_code == 0
    assert cli.deleted[0].endswith("/workers/scripts/foo")


def test_new_prints_paths_on_stdout_and_pin_on_stderr(cli, tmp_path):
    result = cli.run(app, ["publish", "new", "gated", "--pin"])
    assert result.exit_code == 0, result.output
    pin = publish.config_pin(tmp_path / "gated")
    assert f"PIN: {pin}" in result.stderr and pin not in result.stdout
    assert all((tmp_path / line).exists() for line in result.stdout.splitlines())


def test_new_md_with_pin_names_the_flag_that_was_passed(cli):
    result = cli.run(app, ["publish", "new", "x", "--md", "--pin"])
    assert result.exit_code == 2
    assert "--pin implies --hono" in result.stderr


def test_up_error_goes_through_die(cli, tmp_path):
    result = cli.run(app, ["publish", "up", "missing-folder"])
    assert result.exit_code == 2
    assert "not a folder" in result.stderr


@pytest.mark.parametrize("bad", ['{[]:1}', '{1:2}', '{"a":1} trailing'])
def test_parse_jsonc_rejects_malformed(tmp_path, bad):
    (tmp_path / publish.WRANGLER_CONFIG).write_text(bad)
    with pytest.raises(publish.PublishError, match="can't parse"):
        publish._read_config(tmp_path)


def test_config_with_wrong_shapes_fails_clean(tmp_path):
    (tmp_path / publish.WRANGLER_CONFIG).write_text('{"vars": "x", "assets": {"directory": 5}}')
    assert publish.config_pin(tmp_path) is None
    with pytest.raises(publish.PublishError, match="directory"):
        publish._served_dir(tmp_path)
