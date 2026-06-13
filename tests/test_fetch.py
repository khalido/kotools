"""Offline tests for ko.fetch — routing logic + extraction, no network."""

import httpx
import pytest

from ko import fetch


def test_arxiv_id_detection():
    assert fetch._arxiv_id("https://arxiv.org/abs/2412.20138") == "2412.20138"
    assert fetch._arxiv_id("https://arxiv.org/pdf/2412.20138v2") == "2412.20138"
    assert fetch._arxiv_id("https://example.com/paper.pdf") is None


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
