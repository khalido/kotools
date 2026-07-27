"""Offline tests for ko.gmail pure helpers (no live auth needed)."""

from __future__ import annotations

import base64
from email import message_from_bytes

import pytest

from ko import gmail


def _b64(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode()


def _orig(headers: dict[str, str], thread_id: str = "t1") -> dict:
    """A minimal message dict (threadId + payload headers) for reply-derivation tests."""
    return {
        "threadId": thread_id,
        "payload": {"headers": [{"name": k, "value": v} for k, v in headers.items()]},
    }


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


def test_message_body_plain_then_html():
    plain = {"payload": {"mimeType": "text/plain", "body": {"data": _b64("the body")}}}
    assert gmail._message_body(plain) == "the body"
    html_only = {"payload": {"mimeType": "text/html", "body": {"data": _b64("<p>hi</p>")}}}
    assert gmail._message_body(html_only) == "<p>hi</p>"  # falls back to html
    assert gmail._message_body({}) == ""


def test_short_from_helper():
    from ko.cli import _short_from

    assert _short_from("Alice Smith <alice@x.com>") == "Alice Smith"
    assert _short_from("<bob@x.com>") == "bob@x.com"
    assert _short_from("plain@x.com") == "plain@x.com"


def test_clean_snippet_strips_invisible_chars():
    """Invisible ESP padding chars are removed from snippets."""
    cgj = "͏"   # COMBINING GRAPHEME JOINER — the main offender
    zwsp = "​"  # ZERO WIDTH SPACE
    zwnj = "‌"  # ZERO WIDTH NON-JOINER
    zwj = "‍"   # ZERO WIDTH JOINER
    bom = "﻿"   # BOM / zero-width no-break space

    padded = f"Hello{cgj}{zwsp}{zwnj}{zwj}{bom} world"
    assert gmail._clean_snippet(padded) == "Hello world"
    assert gmail._clean_snippet("plain text") == "plain text"  # no-op on clean text


def test_message_snippet_is_cleaned():
    """_message() strips invisible chars from snippets in addition to html-unescaping."""
    cgj = "͏"
    m = {
        "id": "x",
        "threadId": "t",
        "snippet": f"Limited time offer{cgj}{cgj} &amp; deal",
        "payload": {"headers": []},
    }
    msg = gmail._message(m)
    assert "͏" not in msg.snippet
    assert "&amp;" not in msg.snippet  # html-unescape still works
    assert "Limited time offer & deal" == msg.snippet


# --- reply derivation --------------------------------------------------------------------

_BASE = {"From": "Alice <alice@x.com>", "Subject": "Budget", "Message-ID": "<m1@x>"}


def test_derive_reply_normal():
    r = gmail.derive_reply(_orig({**_BASE, "References": "<r0@x>"}), "me@x.com")
    assert r.to == "Alice <alice@x.com>"  # From when no Reply-To
    assert r.subject == "Re: Budget"  # Re: prefixed once
    assert r.thread_id == "t1"  # threaded
    assert r.cc == ""  # no --all
    assert r.in_reply_to == "<m1@x>"
    assert r.references == "<r0@x> <m1@x>"  # original References + Message-ID


def test_derive_reply_prefixes_re_once():
    r = gmail.derive_reply(_orig({**_BASE, "Subject": "RE: Budget"}), "me@x.com")
    assert r.subject == "RE: Budget"  # existing Re:/RE: not doubled


def test_derive_reply_uses_reply_to_over_from():
    r = gmail.derive_reply(_orig({**_BASE, "Reply-To": "Bob <bob@x.com>"}), "me@x.com")
    assert r.to == "Bob <bob@x.com>"


def test_derive_reply_no_message_id_errors():
    with pytest.raises(gmail.GmailError, match="Message-ID"):
        gmail.derive_reply(_orig({"From": "a@x.com", "Subject": "hi"}), "me@x.com")


def test_derive_reply_subject_override_drops_thread_keeps_rfc_headers():
    r = gmail.derive_reply(_orig(_BASE), "me@x.com", subject_override="A new topic")
    assert r.subject == "A new topic"
    assert r.thread_id == ""  # different subject => new thread
    assert r.in_reply_to == "<m1@x>" and r.references.endswith("<m1@x>")  # RFC headers kept


def test_derive_reply_subject_override_matching_keeps_thread():
    r = gmail.derive_reply(_orig(_BASE), "me@x.com", subject_override="Re: Budget")
    assert r.thread_id == "t1"


def test_derive_reply_all_ccs_others_minus_self():
    orig = _orig({**_BASE, "To": "me@x.com, Carol <carol@x.com>", "Cc": "Dave <dave@x.com>"})
    r = gmail.derive_reply(orig, "me@x.com", reply_all=True)
    assert "carol@x.com" in r.cc and "dave@x.com" in r.cc
    assert "me@x.com" not in r.cc  # our own address excluded
    assert "alice@x.com" not in r.cc  # the To recipient isn't also cc'd


# --- draft update field preservation -----------------------------------------------------


def test_preserved_fields_carries_omitted_and_overrides_subject():
    msg = _orig(
        {
            "To": "a@x.com",
            "Cc": "c@x.com",
            "Subject": "Old",
            "In-Reply-To": "<m1@x>",
            "References": "<r0@x> <m1@x>",
        },
        thread_id="t9",
    )
    keep = gmail._preserved_fields(msg, None)
    assert keep == {
        "to": "a@x.com",
        "cc": "c@x.com",
        "subject": "Old",
        "in_reply_to": "<m1@x>",
        "references": "<r0@x> <m1@x>",
        "thread_id": "t9",
    }
    assert gmail._preserved_fields(msg, "New")["subject"] == "New"  # override wins


# --- MIME building -----------------------------------------------------------------------


def test_build_raw_plain_is_text_plain():
    raw = gmail._build_raw(to="a@x.com", subject="Hi", body="just text")
    msg = message_from_bytes(base64.urlsafe_b64decode(raw))
    assert not msg.is_multipart()
    assert msg.get_content_type() == "text/plain"
    assert msg["To"] == "a@x.com" and msg["Subject"] == "Hi"


def test_build_raw_md_is_multipart_alternative_plain_first():
    raw = gmail._build_raw(to="a@x.com", subject="Hi", body="**bold**", html_body="<p><b>bold</b></p>")
    msg = message_from_bytes(base64.urlsafe_b64decode(raw))
    assert msg.get_content_type() == "multipart/alternative"
    parts = msg.get_payload()
    assert parts[0].get_content_type() == "text/plain"  # plain FIRST
    assert parts[1].get_content_type() == "text/html"
    for p in parts:
        assert p["Content-Transfer-Encoding"] == "quoted-printable"
        assert p.get_content_charset() == "utf-8"


def test_build_raw_sets_threading_headers():
    raw = gmail._build_raw(
        to="a@x.com", subject="Re: X", body="b", in_reply_to="<m1@x>", references="<r0@x> <m1@x>"
    )
    msg = message_from_bytes(base64.urlsafe_b64decode(raw))
    assert msg["In-Reply-To"] == "<m1@x>" and msg["References"] == "<r0@x> <m1@x>"


def test_build_raw_rejects_header_newlines():
    with pytest.raises(gmail.GmailError, match="newline"):
        gmail._build_raw(to="a@x.com\r\nBcc: evil@x.com", subject="Hi", body="b")
    with pytest.raises(gmail.GmailError, match="newline"):
        gmail._build_raw(to="a@x.com", subject="Hi\nX-Injected: 1", body="b")


# --- attachments -------------------------------------------------------------------------


def test_walk_attachments_finds_nested_and_skips_bodies():
    payload = {
        "mimeType": "multipart/mixed",
        "parts": [
            {"mimeType": "text/plain", "body": {"data": _b64("body")}},  # no filename -> skipped
            {"filename": "a.pdf", "mimeType": "application/pdf", "body": {"attachmentId": "A1", "size": 12}},
            {
                "mimeType": "multipart/related",
                "parts": [
                    {"filename": "b.png", "mimeType": "image/png", "body": {"attachmentId": "A2", "size": 34}}
                ],
            },
        ],
    }
    atts = gmail._walk_attachments(payload)
    assert [a[0] for a in atts] == ["a.pdf", "b.png"]  # nested found, body part skipped
    assert atts[0][3] == "A1" and atts[1][3] == "A2"


def test_unique_path_suffixes_collisions(tmp_path):
    p = tmp_path / "report.pdf"
    assert gmail._unique_path(p) == p  # free -> unchanged
    p.write_bytes(b"x")
    assert gmail._unique_path(p) == tmp_path / "report-1.pdf"
    (tmp_path / "report-1.pdf").write_bytes(b"x")
    assert gmail._unique_path(p) == tmp_path / "report-2.pdf"


# --- send gate ---------------------------------------------------------------------------


def test_send_off_by_default(monkeypatch):
    monkeypatch.setattr(gmail.config, "setting", lambda *a, **k: None)  # nothing configured
    assert gmail.send_allowed() is False


def test_send_gate_env_values(monkeypatch):
    for val, want in [("1", True), ("true", True), ("YES", True), ("0", False), ("", False)]:
        monkeypatch.setattr(gmail.config, "setting", lambda *a, _v=val, **k: _v)
        assert gmail.send_allowed() is want
