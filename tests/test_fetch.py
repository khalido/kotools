"""Offline tests for ko.fetch — routing logic + extraction, no network."""

import json
import socket

import httpx2 as httpx
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
    # straight host swap per mirror, path + query preserved, ladder order
    out = fetch._reddit_mirrors("https://www.reddit.com/r/Python/comments/abc/title/?x=1")
    assert out == [
        "https://safereddit.com/r/Python/comments/abc/title/?x=1",
        "https://reddit.synth.download/r/Python/comments/abc/title/?x=1",
    ]
    for host in ("reddit.com", "old.reddit.com", "np.reddit.com", "m.reddit.com"):
        out = fetch._reddit_mirrors(f"https://{host}/r/x/comments/1/t/")
        assert out[0] == "https://safereddit.com/r/x/comments/1/t/"


def test_reddit_mirrors_pass_through_non_reddit_and_short_links():
    # non-reddit, redd.it short links, and /s/ share links are left for the normal path
    assert fetch._reddit_mirrors("https://example.com/r/Python/comments/abc/") is None
    assert fetch._reddit_mirrors("https://redd.it/abc123") is None
    assert fetch._reddit_mirrors("https://www.reddit.com/r/Python/s/xYz123") is None


def test_reddit_mirror_ladder_is_config_overridable(monkeypatch):
    # instance churn is THE failure mode — a dead ladder must be swappable without a release
    monkeypatch.setenv("KO_REDDIT_MIRRORS", "mine.example, other.example")
    out = fetch._reddit_mirrors("https://www.reddit.com/r/x/comments/1/t/")
    assert out == [
        "https://mine.example/r/x/comments/1/t/",
        "https://other.example/r/x/comments/1/t/",
    ]


def test_reddit_route_sets_source_and_note(monkeypatch):
    # a live reddit fetch routes through the ladder; source + note name WHICH mirror
    # served — a rotation makes the note load-bearing for debugging
    def fake_get(url, **kw):
        assert url.startswith(("https://safereddit.com/", "https://reddit.synth.download/"))
        assert not url.startswith("https://www.reddit.com")  # never reddit itself
        html = (
            "<html><head><title>T</title></head><body><article><h1>Thread</h1>"
            '<a href="/r/Python/comments/abc/title/">permalink</a>'
            "<p>A long enough comment body that trafilatura keeps it as real content "
            "rather than discarding it as boilerplate navigation or footer noise.</p>"
            "</article></body></html>"
        )
        return httpx.Response(200, html=html, request=httpx.Request("GET", url))

    monkeypatch.setattr(fetch.httpx, "get", fake_get)
    r = fetch.fetch("https://www.reddit.com/r/Python/comments/abc/title/")
    assert r.source == "reddit-mirror"
    assert "safereddit.com" in r.note  # substitution is visible/debuggable


def test_reddit_ladder_falls_through_a_dead_first_mirror(monkeypatch):
    # first mirror up-but-empty (churned/blocked/challenge page), second serves —
    # first non-empty EXTRACT wins, not first 200
    def fake_get(url, **kw):
        if url.startswith("https://safereddit.com/"):
            # a challenge page: 200, but nothing extractable (script-only body)
            return httpx.Response(
                200, html="<html><body><script>var x=1;</script></body></html>",
                request=httpx.Request("GET", url),
            )
        html = (
            "<html><head><title>T</title></head><body><article><h1>Thread</h1>"
            '<a href="/r/Python/comments/abc/title/">permalink</a>'
            "<p>A long enough comment body that trafilatura keeps it as real content "
            "rather than discarding it as boilerplate navigation or footer noise.</p>"
            "</article></body></html>"
        )
        return httpx.Response(200, html=html, request=httpx.Request("GET", url))

    monkeypatch.setattr(fetch.httpx, "get", fake_get)
    r = fetch.fetch("https://www.reddit.com/r/Python/comments/abc/title/")
    assert r.source == "reddit-mirror"
    assert "reddit.synth.download" in r.note  # the SECOND mirror served this one


def test_reddit_mirror_dead_falls_back_to_wayback_of_original(monkeypatch):
    # every mirror down → recover via Wayback of the ORIGINAL reddit url, never a mirror
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


def test_wikipedia_route_uses_extract_api(monkeypatch):
    # the api.php call, not the article HTML page — routing must never hit /wiki/ directly
    def fake_get(url, **kw):
        assert url == "https://en.wikipedia.org/w/api.php"
        assert kw["params"]["titles"] == "BYD Auto"  # underscore -> space, decoded from URL
        assert kw["params"]["explaintext"] == 1
        body = {"query": {"pages": {"123": {"extract": "BYD Auto is a car company.\n\n== History ==\nText."}}}}
        return httpx.Response(200, json=body, request=httpx.Request("GET", url))

    monkeypatch.setattr(fetch.httpx, "get", fake_get)
    r = fetch.fetch("https://en.wikipedia.org/wiki/BYD_Auto")
    assert r.source == "live" and "wikipedia extract" in r.note
    assert "== History ==" in r.text and "BYD Auto is a car company" in r.text


def test_wikipedia_namespace_pages_fall_through(monkeypatch):
    # Talk:/Category:/etc. aren't articles — must never reach the extract API
    calls = []

    def fake_get(url, **kw):
        calls.append(url)
        assert "api.php" not in url  # would mean we wrongly tried the extract API
        html = "<html><body><article><p>talk page content here, long enough.</p></article></body></html>"
        return httpx.Response(200, html=html, request=httpx.Request("GET", url))

    monkeypatch.setattr(fetch.httpx, "get", fake_get)
    fetch.fetch("https://en.wikipedia.org/wiki/Talk:BYD_Auto")
    assert calls  # the generic path ran instead


def test_wikipedia_missing_extract_falls_through_to_generic(monkeypatch):
    # extract API says the page doesn't exist -> generic path still gets a shot, not an error
    calls = []

    def fake_get(url, **kw):
        calls.append(url)
        if "api.php" in url:
            body = {"query": {"pages": {"-1": {"missing": True}}}}
            return httpx.Response(200, json=body, request=httpx.Request("GET", url))
        html = "<html><body><article><p>fallback page content, long enough to keep.</p></article></body></html>"
        return httpx.Response(200, html=html, request=httpx.Request("GET", url))

    monkeypatch.setattr(fetch.httpx, "get", fake_get)
    r = fetch.fetch("https://en.wikipedia.org/wiki/Some_Missing_Page")
    assert len(calls) == 2  # tried the extract API, then fell through
    assert r.source == "live" and "wikipedia extract" not in r.note


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


def test_rescue_prefers_the_browser_even_when_an_archive_answers_first(monkeypatch):
    import time

    monkeypatch.setattr(fetch.shutil, "which", lambda name: "/bin/agent-browser")
    monkeypatch.setenv("EXA_API_KEY", "k")

    def slow_browser(url):
        time.sleep(0.2)
        return fetch.Fetched("live page", "browser")

    monkeypatch.setattr(fetch, "_browser", slow_browser)
    monkeypatch.setattr(fetch, "_exa", lambda url: fetch.Fetched("exa copy", "exa"))
    monkeypatch.setattr(fetch, "_archive_is", lambda url: fetch.Fetched("old capture", "archive.is"))
    monkeypatch.setattr(fetch, "_wayback", lambda url, date: fetch.Fetched("older", "wayback"))
    r = fetch._rescue("https://example.com/x", None, dead=False, why="refused (403)")
    assert r.source == "browser"


def test_rescue_falls_through_failures_and_names_them_all(monkeypatch):
    monkeypatch.setattr(fetch.shutil, "which", lambda name: None)  # no agent-browser
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    monkeypatch.setattr(fetch, "_archive_is", lambda url: (_ for _ in ()).throw(RuntimeError("captcha")))
    monkeypatch.setattr(fetch, "_wayback", lambda url, date: fetch.Fetched("recovered", "wayback"))
    assert fetch._rescue("https://example.com/x", None, dead=False, why="w").source == "wayback"
    monkeypatch.setattr(fetch, "_wayback", lambda url, date: (_ for _ in ()).throw(RuntimeError("no capture")))
    with pytest.raises(RuntimeError, match=r"refused \(403\).*archive.today: captcha; wayback: no capture"):
        fetch._rescue("https://example.com/x", None, dead=False, why="refused (403)")


def test_fetch_cache_reuses_then_fresh_refetches(monkeypatch):
    calls = []

    def fake_get(url, **kw):
        calls.append(url)
        html = "<html><body><article><p>a page body long enough to extract.</p></article></body></html>"
        return httpx.Response(200, html=html, request=httpx.Request("GET", url))

    monkeypatch.setattr(fetch.httpx, "get", fake_get)
    first = fetch.fetch("https://example.com/c")
    again = fetch.fetch("https://example.com/c")
    assert len(calls) == 1 and again.text == first.text and again.note.startswith("cached 0m ago")
    fetch.fetch("https://example.com/c", fresh=True)
    assert len(calls) == 2


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


def test_wikipedia_ref_parses_the_host_strictly():
    """Host-parsed routing: the old regex captured the TAIL of long subdomains (nds-nl → nl,
    the wrong article) and matched wikipedia URLs embedded inside another URL."""
    assert fetch._wikipedia_ref("https://en.wikipedia.org/wiki/Dog") == ("en", "Dog")
    assert fetch._wikipedia_ref("en.wikipedia.org/wiki/Dog") == ("en", "Dog")  # scheme-less
    assert fetch._wikipedia_ref("https://en.m.wikipedia.org/wiki/Dog") == ("en", "Dog")  # mobile share URL
    assert fetch._wikipedia_ref("https://nds-nl.wikipedia.org/wiki/Grunn") == ("nds-nl", "Grunn")
    assert fetch._wikipedia_ref("https://simple.wikipedia.org/wiki/Dog") == ("simple", "Dog")
    assert fetch._wikipedia_ref("https://zh-min-nan.wikipedia.org/wiki/X") == ("zh-min-nan", "X")
    # subpage titles keep their slashes; query/fragment never leak into the title
    assert fetch._wikipedia_ref("https://en.wikipedia.org/wiki/A/B?x=1#s") == ("en", "A/B")


def test_wikipedia_ref_rejects_non_article_hosts_and_paths():
    # an embedded wikipedia URL must NOT hijack the outer fetch
    assert fetch._wikipedia_ref("https://example.com/r?u=en.wikipedia.org/wiki/Dog") is None
    assert fetch._wikipedia_ref("https://www.wikipedia.org/wiki/Dog") is None  # www is not a language
    assert fetch._wikipedia_ref("https://wikipedia.org/wiki/Dog") is None  # no lang subdomain
    assert fetch._wikipedia_ref("https://en.wikipedia.org/w/index.php?title=Dog") is None  # not /wiki/
    assert fetch._wikipedia_ref("https://en.wikipedia.org/wiki/") is None  # empty title
    assert fetch._wikipedia_ref("https://en.wikipedia.beevil.org/wiki/Dog") is None  # lookalike host


def test_live_fetch_sends_the_navigation_header_bundle(monkeypatch):
    # The whole bot-wall fix is these headers — a silent drop would 403 again with no test failing.
    seen = {}

    def fake_get(url, **kw):
        seen.update(kw.get("headers") or {})
        html = "<html><body><article><p>a page body long enough to extract.</p></article></body></html>"
        return httpx.Response(200, html=html, request=httpx.Request("GET", url))

    monkeypatch.setattr(fetch.httpx, "get", fake_get)
    fetch.fetch("https://example.com/page")
    assert seen["Sec-Fetch-Mode"] == "navigate"
    assert seen["Sec-Fetch-User"] == "?1"  # load-bearing: dropping it re-403s drive.com.au
    assert seen["Upgrade-Insecure-Requests"] == "1"
    assert seen["User-Agent"].startswith("kotools/")  # our own UA, not a spoofed Chrome
    assert "markdown" not in seen["Accept"]  # we no longer negotiate for it


def test_volunteered_markdown_passes_through_unextracted(monkeypatch):
    # We stopped ASKING for markdown; a server that sends it anyway has already extracted,
    # and trafilatura returns None on short markdown bodies (would fake a paywall).
    body = "# Title\n\nA short markdown doc, well under trafilatura's floor.\n"

    def fake_get(url, **kw):
        return httpx.Response(
            200,
            text=body,
            headers={"content-type": "text/markdown; charset=utf-8", "x-markdown-tokens": "17"},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(fetch.httpx, "get", fake_get)
    r = fetch.fetch("https://llmstxt.org/index.md")
    assert r.text == body and r.source == "live" and "17 tokens" in r.note


@pytest.mark.parametrize(
    "status,expect_archive_is",
    [(403, True), (429, True), (401, True), (503, True), (404, False), (410, False)],
)
def test_refused_tries_archive_is_first_but_gone_goes_straight_to_wayback(
    monkeypatch, status, expect_archive_is
):
    # A 403 means the page EXISTS and a wall said no — archive.today's JS capture is the
    # right first try. A 404 means gone, and Wayback is the historical archive.
    tried = []

    def fake_get(url, **kw):
        if "archive.ph" in url:
            tried.append("archive.is")
            raise httpx.ConnectError("nope")
        if "archive.org" in url:
            tried.append("wayback")
            raise httpx.ConnectError("nope")
        tried.append("live")
        return httpx.Response(status, text="blocked", request=httpx.Request("GET", url))

    monkeypatch.setattr(fetch.httpx, "get", fake_get)
    with pytest.raises(RuntimeError):
        fetch.fetch("https://www.drive.com.au/reviews/some-car/")
    assert ("archive.is" in tried) is expect_archive_is
    assert "wayback" in tried  # either way we still end at Wayback


def test_cli_fetch_json_keeps_order_and_reports_failures(monkeypatch):

    from typer.testing import CliRunner

    from ko import cli_web
    from ko.cli import app

    def fake(u, **kw):
        if "bad" in u:
            raise RuntimeError("unreachable")
        return cli_web.fetch_mod.Fetched(text="x" * 40, source="live")

    monkeypatch.setattr(cli_web.fetch_mod, "fetch", fake)
    r = CliRunner().invoke(app, ["fetch", "https://a.com", "https://bad.com", "--json"])
    assert r.exit_code == 0, r.output
    rows = json.loads(r.stdout)
    assert rows[0] == {"url": "https://a.com", "source": "live", "tokens": 10, "note": "", "text": "x" * 40}
    assert rows[1] == {"url": "https://bad.com", "error": "unreachable"}


_THREAD = (
    "<html><body><article><h1>Thread</h1>{link}"
    "<p>A long enough comment body that trafilatura keeps it as real content "
    "rather than discarding it as boilerplate navigation or footer noise.</p></article></body></html>"
)


def test_reddit_id_from_another_subreddit_is_an_error(monkeypatch):
    # r/LocalLLaMA/comments/1ft0000 came back as an r/Line6Helix thread (2026-10-27)
    html = _THREAD.format(link='<a href="/r/Line6Helix/comments/1ft0000/amp/">x</a>')
    monkeypatch.setattr(
        fetch.httpx, "get", lambda url, **kw: httpx.Response(200, html=html, request=httpx.Request("GET", url))
    )
    with pytest.raises(RuntimeError, match="is in r/Line6Helix, not r/LocalLLaMA"):
        fetch.fetch("https://www.reddit.com/r/LocalLLaMA/comments/1ft0000/x/", fresh=True)


def test_bot_wall_is_not_a_live_page(monkeypatch):
    wall = "<html><body><p>Please click the box below to let us know you're not a robot.</p></body></html>"
    monkeypatch.setattr(
        fetch.httpx, "get", lambda url, **kw: httpx.Response(200, html=wall, request=httpx.Request("GET", url))
    )
    seen = {}

    def fake_rescue(url, date, *, dead, why):
        seen.update(dead=dead, why=why)
        return fetch.Fetched(text="the article", source="browser")

    monkeypatch.setattr(fetch, "_rescue", fake_rescue)
    r = fetch.fetch("https://www.bloomberg.com/news/articles/x", fresh=True)
    assert r.source == "browser" and seen == {"dead": False, "why": "the live page is a bot check"}
    assert not fetch._walled("A long article about captcha design. " * 60)  # long pages never count


def test_fetch_json_is_an_array_even_when_every_url_fails(monkeypatch):
    def boom(u, **kw):
        raise RuntimeError("dead")

    monkeypatch.setattr(fetch, "fetch", boom)
    r = runner.invoke(app, ["fetch", "https://a.example", "--json"])
    assert r.exit_code == 1
    assert json.loads(r.stdout) == [{"url": "https://a.example", "error": "dead"}]


@pytest.fixture
def guarded(monkeypatch):
    """`public_only()` with the patch it makes undone after the test, and DNS under the test's control."""
    answers = {}

    def lookup(host, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (answers.get(host, host), 80))]

    monkeypatch.setattr(socket, "getaddrinfo", lookup)
    monkeypatch.setattr(fetch, "_public_only", False)
    fetch.public_only()
    yield answers
    # monkeypatch restores getaddrinfo and the flag, in that order, on teardown


@pytest.mark.parametrize(
    "address",
    ["127.0.0.1", "10.1.2.3", "192.168.1.1", "169.254.169.254", "100.100.1.1", "0.0.0.0", "224.0.0.1", "198.18.0.1",
     "::1", "::7f00:1", "fe80::1", "fc00::1", "fec0::1", "::ffff:127.0.0.1", "64:ff9b::7f00:1", "2002:7f00:1::"],
)
def test_public_only_refuses_a_private_address(address):
    assert not fetch._is_public(address)


@pytest.mark.parametrize("address", ["93.184.216.34", "8.8.8.8", "2606:4700:4700::1111"])
def test_public_only_allows_a_public_address(address):
    assert fetch._is_public(address)


def test_public_only_checks_what_a_name_resolves_to(guarded):
    guarded["rebound.example"] = "127.0.0.1"
    with pytest.raises(fetch.NotPublic, match="rebound.example"):
        socket.getaddrinfo("rebound.example", 80)
    guarded["fine.example"] = "93.184.216.34"
    assert socket.getaddrinfo("fine.example", 80)


def test_public_only_refusal_stops_the_rescue_ladder(guarded, monkeypatch, tmp_path):
    """A redirect to localhost is not a dead link: no archive or browser rung runs after it."""
    monkeypatch.setattr(fetch, "cache_dir", lambda: tmp_path)

    def get(url, **kw):
        socket.getaddrinfo("127.0.0.1", 80)
        raise AssertionError("unreachable")

    monkeypatch.setattr(fetch.httpx, "get", get)
    monkeypatch.setattr(fetch, "_rescue", lambda *a, **k: pytest.fail("rescued a refused address"))
    with pytest.raises(fetch.NotPublic):
        fetch.fetch("https://a.example/post")


def test_public_only_has_no_browser_rung(guarded, monkeypatch):
    monkeypatch.setattr(fetch.shutil, "which", lambda name: "/bin/agent-browser")
    names = [name for name, _ in fetch._rescuers("https://a.example/", None, dead=False)]
    assert "browser" not in names


def test_public_only_does_not_read_the_cache(guarded, monkeypatch, tmp_path):
    monkeypatch.setattr(fetch, "cache_dir", lambda: tmp_path)
    url = "https://a.example/page"
    path = fetch._cache_path(url, False, False, None)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"text": "from an unchecked run", "source": "live", "note": ""}))
    monkeypatch.setattr(fetch, "_fetch", lambda *a, **k: fetch.Fetched(text="fresh", source="live"))
    assert fetch.fetch(url).text == "fresh"


def test_public_only_saves_no_pdf(guarded, monkeypatch, tmp_path):
    monkeypatch.setattr(fetch, "cache_dir", lambda: tmp_path)
    seen = {}

    def fake(url, **kw):
        seen.update(kw)
        return fetch.Fetched(text="x", source="pdf")

    monkeypatch.setattr(fetch, "_fetch", fake)
    fetch.fetch("https://a.example/report.pdf", save=True)
    assert seen["save"] is False
