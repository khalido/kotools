"""Offline tests for ko.brief — gather() pipeline, best-effort error handling.

All external calls are monkeypatched. No network, no Google auth, no LLM calls.
"""

from __future__ import annotations

from ko import brief


def test_gather_returns_four_sections(monkeypatch) -> None:
    """gather() always returns exactly four (title, text) pairs."""
    monkeypatch.setattr(brief, "_today_events", lambda: "9:00  Stand-up\n10:00  Code review")
    monkeypatch.setattr(brief, "_unread_email", lambda: "  2026-07-09  Alice  Subject here\n    Snippet")
    monkeypatch.setattr(brief, "_hn_top", lambda: "  500pts  Story title  https://news.ycombinator.com/item?id=1")
    monkeypatch.setattr(brief, "_hf_top", lambda: "  42▲  Attention Is All You Need")

    sections = brief.gather()
    assert len(sections) == 4
    titles = [t for t, _ in sections]
    assert "Today" in titles
    assert "Unread email" in titles
    assert "Hacker News" in titles
    assert "AI papers (HF)" in titles


def test_gather_one_failure_becomes_note(monkeypatch) -> None:
    """A failing source produces a one-liner note, not an exception.
    Other sections still return their real content."""
    monkeypatch.setattr(brief, "_today_events", lambda: "9:00  Stand-up")
    monkeypatch.setattr(brief, "_unread_email", lambda: (_ for _ in ()).throw(RuntimeError("not authed")))
    monkeypatch.setattr(brief, "_hn_top", lambda: "  500pts  Story")
    monkeypatch.setattr(brief, "_hf_top", lambda: "  10▲  Some Paper")

    # Generator trick for raising inside a lambda — use a real function instead
    def _fail():
        raise RuntimeError("not authed")

    monkeypatch.setattr(brief, "_unread_email", _fail)

    sections = brief.gather()
    assert len(sections) == 4

    section_map = dict(sections)
    # Today succeeded — real content
    assert "Stand-up" in section_map["Today"]
    # Unread email failed — should be a note line
    email_text = section_map["Unread email"]
    assert "not authed" in email_text
    # Single line (the error note)
    assert "\n" not in email_text
    # HN and HF still returned real content
    assert "Story" in section_map["Hacker News"]
    assert "Paper" in section_map["AI papers (HF)"]


def test_gather_all_failures_returns_notes(monkeypatch) -> None:
    """When every source raises, gather() returns four note lines (one per section)."""
    def _fail():
        raise RuntimeError("all down")

    monkeypatch.setattr(brief, "_today_events", _fail)
    monkeypatch.setattr(brief, "_unread_email", _fail)
    monkeypatch.setattr(brief, "_hn_top", _fail)
    monkeypatch.setattr(brief, "_hf_top", _fail)

    sections = brief.gather()
    assert len(sections) == 4
    for title, text in sections:
        # Every section is a note line
        assert text.startswith(f"({title}:")


def test_render_raw_formats_sections() -> None:
    """render_raw() joins sections as ## Title + body text blocks."""
    sections = [
        ("Today", "9:00  Stand-up"),
        ("Hacker News", "  500pts  Story"),
    ]
    out = brief.render_raw(sections)
    assert "## Today" in out
    assert "Stand-up" in out
    assert "## Hacker News" in out
    assert "Story" in out


def test_try_catches_exceptions() -> None:
    """_try() wraps a callable and on exception returns a note line, not an exception."""
    def _boom():
        raise ValueError("test error")

    title, text = brief._try("MySection", _boom)
    assert title == "MySection"
    assert "MySection" in text
    assert "test error" in text


def test_try_returns_fn_result_on_success() -> None:
    """_try() passes through the return value on success."""
    title, text = brief._try("HN", lambda: "great content")
    assert title == "HN"
    assert text == "great content"
