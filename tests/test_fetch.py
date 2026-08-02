"""Offline tests for ko.fetch — routing logic + extraction, no network."""

import httpx
import pytest
from typer.testing import CliRunner

from ko import fetch
from ko.cli import app

runner = CliRunner()


def _fake_fetch(**by_url):
    """Build a fetch() replacement: known URL → its Fetched, unknown → raises (a failed fetch)."""

    def fake(url, **kw):
        if url in by_url:
            return by_url[url]
        raise RuntimeError("dead link")

    return fake


def test_arxiv_id_detection():
    assert fetch._arxiv_id("https://arxiv.org/abs/2412.20138") == "2412.20138"
    assert fetch._arxiv_id("https://arxiv.org/html/2412.20138v2") == "2412.20138"
    # /pdf/ deliberately does NOT match — those fall through to the PDF parser
    assert fetch._arxiv_id("https://arxiv.org/pdf/2412.20138v2") is None
    assert fetch._arxiv_id("https://example.com/paper.pdf") is None


def test_reddit_mirror_rewrites_known_hosts():
    # straight host swap, path + query preserved
    assert (
        fetch._reddit_mirror("https://www.reddit.com/r/Python/comments/abc/title/?x=1")
        == "https://reddit.synth.download/r/Python/comments/abc/title/?x=1"
    )
    for host in ("reddit.com", "old.reddit.com", "np.reddit.com", "m.reddit.com"):
        out = fetch._reddit_mirror(f"https://{host}/r/x/comments/1/t/")
        assert out == "https://reddit.synth.download/r/x/comments/1/t/"


def test_reddit_mirror_passes_through_non_reddit_and_short_links():
    # non-reddit, redd.it short links, and /s/ share links are left for the normal path
    assert fetch._reddit_mirror("https://example.com/r/Python/comments/abc/") is None
    assert fetch._reddit_mirror("https://redd.it/abc123") is None
    assert fetch._reddit_mirror("https://www.reddit.com/r/Python/s/xYz123") is None


def test_reddit_route_sets_source_and_note(monkeypatch):
    # a live reddit fetch routes through the mirror; source + note make the swap observable
    def fake_get(url, **kw):
        assert url.startswith("https://reddit.synth.download/")  # never hits reddit itself
        html = (
            "<html><head><title>T</title></head><body><article><h1>Thread</h1>"
            "<p>A long enough comment body that trafilatura keeps it as real content "
            "rather than discarding it as boilerplate navigation or footer noise.</p>"
            "</article></body></html>"
        )
        return httpx.Response(200, html=html, request=httpx.Request("GET", url))

    monkeypatch.setattr(fetch.httpx, "get", fake_get)
    r = fetch.fetch("https://www.reddit.com/r/Python/comments/abc/title/")
    assert r.source == "reddit-mirror"
    assert "reddit.synth.download" in r.note  # substitution is visible/debuggable


def test_reddit_mirror_dead_falls_back_to_wayback_of_original(monkeypatch):
    # mirror 404s → recover via Wayback of the ORIGINAL reddit url, not the mirror
    def dead_get(url, **kw):
        return httpx.Response(404, request=httpx.Request("GET", url))

    monkeypatch.setattr(fetch.httpx, "get", dead_get)
    seen = {}

    def fake_wayback(url, date):
        seen["url"] = url
        return fetch.Fetched("archived thread", "wayback")

    monkeypatch.setattr(fetch, "_wayback", fake_wayback)
    r = fetch.fetch("https://www.reddit.com/r/Python/comments/abc/title/")
    assert r.source == "wayback"
    assert seen["url"] == "https://www.reddit.com/r/Python/comments/abc/title/"  # original


def test_pdf_name_sanitized():
    assert (
        fetch._pdf_name("https://x.com/a/My Paper (final).pdf")
        == "My_Paper__final_.pdf"
    )
    assert fetch._pdf_name("https://x.com/report") == "report.pdf"
    assert fetch._pdf_name("https://x.com/") == "download.pdf"


def test_archive_is_detects_captcha_block(monkeypatch):
    def fake_get(url, **kw):
        return httpx.Response(429, text="<html>please solve the CAPTCHA</html>")

    monkeypatch.setattr(fetch.httpx, "get", fake_get)
    with pytest.raises(RuntimeError, match="blocked"):
        fetch._archive_is("https://example.com/paywalled")


def test_paywall_fallback_cascades_to_wayback(monkeypatch):
    monkeypatch.setattr(
        fetch, "_archive_is", lambda url: (_ for _ in ()).throw(RuntimeError("blocked"))
    )
    monkeypatch.setattr(
        fetch, "_wayback", lambda url, date: fetch.Fetched("recovered", "wayback")
    )
    result = fetch._paywall_fallback("https://example.com/x", None)
    assert result.source == "wayback" and result.text == "recovered"


def test_extract_html_to_markdown():
    html = """<html><head><title>T</title></head><body>
    <nav>menu junk</nav>
    <article><h1>Real Title</h1><p>The actual content paragraph, which needs to be
    long enough that trafilatura treats it as real content rather than boilerplate
    noise from a navigation sidebar or footer.</p></article>
    <footer>copyright junk</footer></body></html>"""
    text = fetch._extract(html, "https://example.com/post")
    assert text is not None
    assert "actual content paragraph" in text
    assert "menu junk" not in text


# --- CLI: token banner + multi-URL orchestration -----------------------------


def test_fetch_banner_has_source_and_tokens():
    # ~4 chars/token, and the note detail is appended when present
    r = fetch.Fetched(text="x" * 40, source="live", note="saved /tmp/x.pdf")
    from ko.cli_web import _fetch_banner

    banner = _fetch_banner(r)
    assert banner == "[live · ~10 tokens · saved /tmp/x.pdf]"


def test_cli_single_url_stdout_byte_identical(monkeypatch):
    # single URL: stdout is the raw text, no `# url` header (it's piped everywhere)
    monkeypatch.setattr(
        fetch, "fetch", _fake_fetch(**{"http://a": fetch.Fetched("BODY", "live")})
    )
    r = runner.invoke(app, ["fetch", "http://a"])
    assert r.exit_code == 0
    assert r.stdout == "BODY\n"
    assert "~1 tokens" in r.stderr  # the consistent banner still fires on stderr


def test_cli_multi_url_delimited(monkeypatch):
    monkeypatch.setattr(
        fetch,
        "fetch",
        _fake_fetch(
            **{"http://a": fetch.Fetched("AAA", "live"), "http://b": fetch.Fetched("BBB", "live")}
        ),
    )
    r = runner.invoke(app, ["fetch", "http://a", "http://b"])
    assert r.exit_code == 0
    # each doc under a `# <url>` header, `---`-separated
    assert r.stdout == "# http://a\n\nAAA\n\n---\n\n# http://b\n\nBBB\n"
    assert r.stderr.count("tokens") == 2  # one banner per document


def test_cli_multi_url_partial_failure_exit_0(monkeypatch):
    monkeypatch.setattr(
        fetch, "fetch", _fake_fetch(**{"http://a": fetch.Fetched("AAA", "live")})
    )
    r = runner.invoke(app, ["fetch", "http://a", "http://dead"])
    assert r.exit_code == 0  # any success → exit 0
    # >1 URL requested → header format even though only one came back; no trailing rule
    assert r.stdout == "# http://a\n\nAAA\n"
    assert "failed: http://dead" in r.stderr


def test_cli_multi_url_all_fail_exit_1(monkeypatch):
    monkeypatch.setattr(fetch, "fetch", _fake_fetch())  # every URL raises
    r = runner.invoke(app, ["fetch", "http://x", "http://y"])
    assert r.exit_code == 1
    assert "no content returned for any" in r.stderr
