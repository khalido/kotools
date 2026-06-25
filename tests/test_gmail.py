"""Offline tests for ko.gmail pure helpers (no live auth needed)."""

from __future__ import annotations

import base64

from ko import gmail


def _b64(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode()


def test_error_type():
    assert issubclass(gmail.GmailError, RuntimeError)


def test_decode_base64url_unpadded():
    raw = "Héllo + world / data?"
    data = base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")  # Gmail strips padding
    assert gmail._decode(data) == raw


def test_fmt_date():
    assert gmail._fmt_date("not a date") == "not a date"  # passthrough
    out = gmail._fmt_date("Wed, 25 Jun 2026 14:30:00 +1000")
    assert "2026" in out and len(out) == 16 and out[10] == " "  # "YYYY-MM-DD HH:MM", tz-agnostic


def test_find_mime_prefers_plain_and_recurses():
    payload = {
        "mimeType": "multipart/alternative",
        "parts": [
            {"mimeType": "text/plain", "body": {"data": _b64("plain body")}},
            {"mimeType": "text/html", "body": {"data": _b64("<p>html</p>")}},
        ],
    }
    assert gmail._find_mime(payload, "text/plain") == "plain body"
    assert gmail._find_mime(payload, "text/html") == "<p>html</p>"
    assert gmail._find_mime({"mimeType": "text/plain", "body": {}}, "text/plain") == ""


def test_message_from_metadata():
    m = {
        "id": "abc",
        "threadId": "t1",
        "labelIds": ["INBOX", "UNREAD"],
        "snippet": "hi &amp; bye",
        "payload": {
            "headers": [
                {"name": "From", "value": "Alice <alice@x.com>"},
                {"name": "Subject", "value": "Hello"},
                {"name": "Date", "value": "Wed, 25 Jun 2026 14:30:00 +1000"},
            ]
        },
    }
    msg = gmail._message(m)
    assert msg.id == "abc" and msg.from_ == "Alice <alice@x.com>" and msg.subject == "Hello"
    assert msg.unread is True and msg.snippet == "hi & bye"  # html-unescaped
    assert "2026" in msg.date


def test_short_from_helper():
    from ko.cli import _short_from

    assert _short_from("Alice Smith <alice@x.com>") == "Alice Smith"
    assert _short_from("<bob@x.com>") == "bob@x.com"
    assert _short_from("plain@x.com") == "plain@x.com"
