import json
from datetime import UTC, datetime, timedelta

import pytest
from typer.testing import CliRunner

from ko import pkg
from ko.cli import app

runner = CliRunner()


def _days_ago(n: int) -> str:
    return (datetime.now(UTC) - timedelta(days=n)).strftime("%Y-%m-%dT00:00:00Z")


class _Resp:
    def __init__(self, status: int, body):
        self.status_code, self._body = status, body
        self.text = body if isinstance(body, str) else json.dumps(body)

    def json(self):
        return self._body

    def raise_for_status(self):
        assert self.status_code == 200, self.status_code
        return self


@pytest.fixture
def fake_web(monkeypatch):
    """deps.dev knows a pre-release as 'default'; PyPI's latest is 2.0.0, released yesterday."""
    routes = {
        f"{pkg.DEPS}/systems/pypi/packages/demo": {
            "versions": [
                {"versionKey": {"version": "1.9.0"}, "publishedAt": _days_ago(40)},
                {"versionKey": {"version": "1.10.0"}, "publishedAt": _days_ago(20)},
                {"versionKey": {"version": "2.0.0"}, "publishedAt": _days_ago(1)},
                {
                    "versionKey": {"version": "3.0.0.dev1"},
                    "publishedAt": _days_ago(1),
                    "isDefault": True,
                },
            ]
        },
        "https://pypi.org/pypi/demo/json": {"info": {"version": "2.0.0", "project_urls": {}}},
        f"{pkg.DEPS}/systems/pypi/packages/demo/versions/2.0.0": {
            "licenses": ["MIT"],
            "advisoryKeys": [],
            "links": [{"label": "SOURCE_REPO", "url": "https://github.com/acme/demo.git"}],
            "relatedProjects": [],
            "attestations": [{"verified": True}],
        },
        f"{pkg.DEPS}/systems/pypi/packages/demo/versions/1.9.0": {"advisoryKeys": [{"id": "GHSA-x"}]},
        f"{pkg.DEPS}/advisories/GHSA-x": {
            "title": "bad thing",
            "cvss3Score": 7.5,
            "url": "https://osv.dev/x",
        },
        f"{pkg.DEPS}/projects/github.com%2Facme%2Fdemo": {
            "starsCount": 12,
            "openIssuesCount": 3,
        },
        "https://api.github.com/repos/acme/demo": {"pushed_at": "2026-10-01T00:00:00Z"},
        "https://api.github.com/repos/acme/demo/releases?per_page=30": [
            {"tag_name": "v1.10.0", "html_url": "x", "body": "old"},
            {
                "tag_name": "v2.0.0",
                "html_url": "https://github.com/acme/demo/releases/tag/v2.0.0",
                "body": "<!-- generated -->\n## What's new\n\n* faster\n",
            },
        ],
    }

    def fake_get(url, **kw):
        return _Resp(200, routes[url]) if url in routes else _Resp(404, {})

    monkeypatch.setattr(pkg, "_get", fake_get)
    monkeypatch.setattr(pkg, "_downloads", lambda s, n: 1234)
    pkg._gh_headers.cache_clear()
    monkeypatch.setenv("GITHUB_TOKEN", "fake")


def test_info_uses_registry_latest_floor_lock_and_notes(fake_web, tmp_path, monkeypatch):
    (tmp_path / "uv.lock").write_text('[[package]]\nname = "Demo"\nversion = "1.9.0"\n')
    monkeypatch.chdir(tmp_path)
    [p] = pkg.info("demo")  # in uv.lock, so npm isn't tried
    assert (p.system, p.latest) == (
        "pypi",
        "2.0.0",
    )  # not deps.dev's pre-release default
    assert p.floor_pick == "1.10.0"  # 2.0.0 is a day old
    assert p.locked == "1.9.0"
    assert [a.title for a in p.locked_advisories] == ["bad thing"] and p.advisories == []
    assert (p.stars, p.pushed, p.provenance, p.releases_1y) == (
        12,
        "2026-10-01",
        True,
        3,
    )
    assert p.notes_url.endswith("/v2.0.0") and p.notes == ["## What's new", "* faster"]
    assert p.recent == [("1.10.0", _days_ago(20)[:10]), ("1.9.0", _days_ago(40)[:10])]


def test_systems_for():
    assert pkg.systems_for("npm:react") == [("npm", "react")]
    assert pkg.systems_for("@sveltejs/kit") == [("npm", "@sveltejs/kit")]


def test_cli_compare_reports_a_miss(fake_web, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    r = runner.invoke(app, ["pkg", "info", "demo", "nope"])
    assert r.exit_code == 0
    assert "[nope: no npm or PyPI package named 'nope']" in r.stderr
    assert "pypi:demo" in r.stdout and "12" in r.stdout
    r = runner.invoke(app, ["pkg", "info", "nope", "--json"])
    assert r.exit_code == 1 and json.loads(r.stdout) == [
        {
            "query": "nope",
            "error": "no npm or PyPI package named 'nope'",
            "code": "not_found",
        }
    ]


def test_big_never_goes_scientific():
    from ko._cli_shared import _big

    assert [_big(x) for x in (12, 999_600, 1_048_576, 1_500_000_000, None)] == ["12", "1M", "1.05M", "1.5B", "–"]


def test_floor_is_72_hours_and_covers_unindexed_latest(fake_web, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    vs = pkg._get(f"{pkg.DEPS}/systems/pypi/packages/demo").json()["versions"]
    hours_ago = (datetime.now(UTC) - timedelta(hours=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
    vs[2]["publishedAt"] = hours_ago  # 2.0.0: 2.5 days, maybe on the cutoff's calendar day
    [p] = pkg.info("pypi:demo")
    assert p.floor_pick == "1.10.0"

    vs.pop(2)  # deps.dev hasn't indexed 2.0.0, and PyPI's JSON here carries no upload time
    [p] = pkg.info("pypi:demo")
    assert (p.released, p.floor_pick) == ("", "1.10.0")
