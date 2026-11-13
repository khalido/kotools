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
    zwsp = "\u200b"  # ZERO WIDTH SPACE
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


def test_message_exposes_labels_and_list_unsubscribe():
    """--json triage fields: labelIds surfaced as `labels`, the List-Unsubscribe header captured
    (empty on human mail)."""
    newsletter = {
        "id": "n1",
        "threadId": "t",
        "snippet": "",
        "labelIds": ["INBOX", "CATEGORY_PROMOTIONS"],
        "payload": {"headers": [{"name": "List-Unsubscribe", "value": "<mailto:u@list.com>"}]},
    }
    msg = gmail._message(newsletter)
    assert msg.labels == ["INBOX", "CATEGORY_PROMOTIONS"]
    assert msg.list_unsubscribe == "<mailto:u@list.com>"

    human = {"id": "h1", "threadId": "t", "snippet": "", "labelIds": ["INBOX"], "payload": {"headers": []}}
    hmsg = gmail._message(human)
    assert hmsg.labels == ["INBOX"] and hmsg.list_unsubscribe == ""  # no header → empty


def test_mentions_attachment():
    """The send-guard's crude /attach/i check: catches 'attached'/'attachment'/'attach'; false
    on prose without them."""
    assert gmail.mentions_attachment("please find the report attached")
    assert gmail.mentions_attachment("See the attachment.")
    assert gmail.mentions_attachment("I'll attach the invoice")
    assert not gmail.mentions_attachment("the report is inline below")
    assert not gmail.mentions_attachment("")


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
        "bcc": "",  # preserved too — an update must not silently drop a Bcc
        "subject": "Old",
        "in_reply_to": "<m1@x>",
        "references": "<r0@x> <m1@x>",
        "thread_id": "t9",
    }
    assert gmail._preserved_fields(msg, "New")["subject"] == "New"  # override wins


def test_build_raw_with_attachments(tmp_path):
    """Attachments make the message multipart/mixed; filename and content-type survive."""
    import base64
    from email import message_from_bytes

    pdf = tmp_path / "invoice.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake invoice")
    raw = gmail._build_raw(
        to="a@x.com", subject="inv", body="see attached", html_body="<p>see attached</p>",
        attachments=[pdf],
    )
    msg = message_from_bytes(base64.urlsafe_b64decode(raw))
    assert msg.get_content_type() == "multipart/mixed"
    parts = list(msg.walk())
    atts = [p for p in parts if p.get_filename()]
    assert [p.get_filename() for p in atts] == ["invoice.pdf"]
    assert atts[0].get_content_type() == "application/pdf"
    assert atts[0].get_payload(decode=True) == b"%PDF-1.4 fake invoice"
    # body alternative intact: plain part first, then html
    subtypes = [p.get_content_type() for p in parts if p.get_content_type().startswith("text/")]
    assert subtypes == ["text/plain", "text/html"]

    with pytest.raises(gmail.GmailError, match="can't read attachment"):
        gmail._build_raw(to="a@x.com", subject="s", body="b", attachments=[tmp_path / "nope.pdf"])


def test_merge_cc_dedupes_against_to_and_cc():
    assert gmail._merge_cc("a@x.com", "b@x.com", "c@x.com") == "b@x.com, c@x.com"
    # already in To or Cc (case-insensitive) -> skipped
    assert gmail._merge_cc("a@x.com", "b@x.com", "A@X.com, b@x.com") == "b@x.com"
    # named form kept; empty extra is a no-op
    assert gmail._merge_cc("", "", "Nir <n@x.com>") == "Nir <n@x.com>"
    assert gmail._merge_cc("a@x.com", "b@x.com", "") == "b@x.com"


def test_update_draft_refuses_to_drop_attachments(monkeypatch):
    """drafts.update replaces the whole MIME message — a draft holding attachments (e.g. added
    in Gmail's UI) must not be silently stripped by a body polish. force=True proceeds."""
    existing = {
        "message": {
            "payload": {
                "parts": [
                    {"mimeType": "text/plain", "body": {"data": ""}},
                    {
                        "filename": "contract.pdf",
                        "mimeType": "application/pdf",
                        "body": {"attachmentId": "att1", "size": 100},
                    },
                ]
            },
            "threadId": "t1",
        }
    }

    class _Svc:
        def users(self):
            return self

        def drafts(self):
            return self

        def get(self, userId, id, format):
            return self

        def update(self, userId, id, body):
            self.updated = body
            return self

        def execute(self, num_retries=0):
            return existing if not hasattr(self, "updated") else {"id": "d1"}

    monkeypatch.setattr(gmail, "_compose_service", lambda: _Svc())
    with pytest.raises(gmail.GmailError, match="attachment"):
        gmail.update_draft("d1", "new body")
    assert gmail.update_draft("d1", "new body", force=True) == "d1"


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


def test_draft_send_attach_guard(monkeypatch):
    """`draft send` refuses when the body says 'attached' but nothing is attached; --yes overrides;
    a no-mention draft skips the guard (and falls through to the normal non-TTY confirm gate)."""
    from typer.testing import CliRunner

    from ko import cli_google
    from ko.cli import app

    monkeypatch.setattr(cli_google.gmail_mod, "send_allowed", lambda: True)  # gate open for the test
    summary = {
        "to": "a@x.com", "cc": "", "bcc": "", "subject": "Invoice",
        "attachments": 0, "mentions_attachment": True,
    }
    monkeypatch.setattr(cli_google.gmail_mod, "draft_summary", lambda did: summary)
    sent: list[str] = []
    monkeypatch.setattr(
        cli_google.gmail_mod, "send_draft", lambda did: (sent.append(did), "sent1")[1]
    )
    runner = CliRunner()

    # says 'attached', 0 attachments, no --yes → refuse (exit 2), never sends
    r = runner.invoke(app, ["gmail", "draft", "send", "d1"])
    assert r.exit_code == 2 and "attached" in r.output.lower() and not sent

    # --yes overrides the guard → sends
    r2 = runner.invoke(app, ["gmail", "draft", "send", "d1", "--yes"])
    assert r2.exit_code == 0 and sent == ["d1"]

    # no mention → guard doesn't fire; non-TTY confirm gate refuses with a *different* message
    sent.clear()
    monkeypatch.setattr(
        cli_google.gmail_mod, "draft_summary", lambda did: {**summary, "mentions_attachment": False}
    )
    r3 = runner.invoke(app, ["gmail", "draft", "send", "d1"])
    assert r3.exit_code == 2 and "TTY" in r3.output and not sent  # fell through, not the attach guard


def test_get_draft_returns_attachment_names(monkeypatch):
    """`draft show` needs the attached filenames to confirm an --attach stuck — get_draft walks
    them out of the same fetched payload (no extra API call)."""
    draft = {
        "id": "d1",
        "message": {
            "id": "m1",
            "payload": {
                "headers": [{"name": "To", "value": "a@x.com"}, {"name": "Subject", "value": "Inv"}],
                "parts": [
                    {"mimeType": "text/plain", "body": {"data": _b64("see attached")}},
                    {"filename": "invoice.pdf", "mimeType": "application/pdf",
                     "body": {"attachmentId": "A1", "size": 10}},
                    {"filename": "terms.pdf", "mimeType": "application/pdf",
                     "body": {"attachmentId": "A2", "size": 20}},
                ],
            }
        },
    }

    class _Svc:
        def users(self):
            return self

        def drafts(self):
            return self

        def get(self, userId, id, format):
            return self

        def execute(self, num_retries=0):
            return draft

    monkeypatch.setattr(gmail, "_compose_service", lambda: _Svc())
    _id, meta, _body, atts = gmail.get_draft("d1")
    assert _id == "d1" and meta.to == "a@x.com"
    assert atts == ["invoice.pdf", "terms.pdf"]  # both files, in payload order

    # a draft with no attachments -> empty list (show prints no Attachments line)
    draft["message"]["payload"]["parts"] = draft["message"]["payload"]["parts"][:1]
    assert gmail.get_draft("d1")[3] == []


def test_search_json_carries_labels(monkeypatch):
    """Regression for a field report: `search --json` must carry `labels`. The path does a
    metadata get() per hit (which returns labelIds), so `_message` picks them up — verify it
    survives all the way to the dataclass the CLI serialises."""
    listed = {"messages": [{"id": "m1"}]}
    full = {
        "id": "m1",
        "threadId": "t1",
        "labelIds": ["INBOX", "IMPORTANT"],
        "snippet": "hi",
        "payload": {"headers": [{"name": "Subject", "value": "Hello"}]},
    }

    class _Svc:
        def users(self):
            return self

        def messages(self):
            return self

        def list(self, **kw):
            self._ret = listed
            return self

        def get(self, **kw):
            self._ret = full
            return self

        def execute(self, num_retries=0):
            return self._ret

    monkeypatch.setattr(gmail, "service", lambda api, **k: _Svc())
    msgs = gmail.search("is:unread")
    assert len(msgs) == 1 and msgs[0].labels == ["INBOX", "IMPORTANT"]


def test_cli_draft_show_lists_attachments(monkeypatch):
    """`draft show` prints an `Attachments: … (N)` line when the draft has files, nothing when not."""
    from typer.testing import CliRunner

    from ko import cli_google
    from ko.cli import app

    meta = gmail.GmailMessage(
        id="d1", thread_id="t1", date="", from_="", to="a@x.com", subject="Inv", snippet="", unread=False
    )
    monkeypatch.setattr(
        cli_google.gmail_mod, "get_draft", lambda did: ("d1", meta, "body", ["invoice.pdf", "terms.pdf"])
    )
    r = CliRunner().invoke(app, ["gmail", "draft", "show", "d1"])
    assert r.exit_code == 0 and "Attachments: invoice.pdf, terms.pdf (2)" in r.stdout

    monkeypatch.setattr(cli_google.gmail_mod, "get_draft", lambda did: ("d1", meta, "body", []))
    r = CliRunner().invoke(app, ["gmail", "draft", "show", "d1"])
    assert r.exit_code == 0 and "Attachments:" not in r.stdout  # none -> no line


def test_md_to_html_renders_gfm_tables():
    """Tables are GFM, not CommonMark — an agent's invoice table must become a styled
    <table> (inline faint borders; Gmail ignores <style> blocks), not literal pipe text."""
    html_out = gmail.md_to_html("| a | b |\n|---|--:|\n| 1 | 2 |\n\n~~gone~~ **bold**")
    assert '<table style="border-collapse:collapse">' in html_out
    assert f'<td style="{gmail._TABLE_CSS}">1</td>' in html_out
    # right-aligned column: border css merged IN FRONT of the alignment, no double-inject
    assert f'<td style="{gmail._TABLE_CSS}text-align:right">2</td>' in html_out
    assert html_out.count(gmail._TABLE_CSS) == 4  # exactly one injection per cell (2 th + 2 td)
    assert "<s>gone</s>" in html_out and "<strong>bold</strong>" in html_out
    assert "|" not in html_out.replace("</", "").replace("<", "")  # no raw pipes survive


# --- threads -----------------------------------------------------------------------------


def _msg(mid, frm, *, to="me@x.com", date="Mon, 21 Sep 2026 10:00:00 +1000", unread=False, **h):
    headers = {"From": frm, "To": to, "Subject": "Budget", "Date": date, "Message-ID": f"<{mid}@x>", **h}
    return {
        "id": mid,
        "threadId": "t1",
        "labelIds": ["INBOX"] + (["UNREAD"] if unread else []),
        "snippet": f"snip {mid}",
        "payload": {"headers": [{"name": k, "value": v} for k, v in headers.items()]},
    }


_THREAD = {
    "id": "t1",
    "messages": [
        _msg("t1", "Alice <alice@x.com>"),
        _msg("m2", "Me <me@x.com>", to="Alice <alice@x.com>, Bob <bob@x.com>"),
        _msg("m3", "Alice <alice@x.com>", unread=True),
    ],
}


def test_thread_summary_latest_date_first_subject_distinct_people():
    t = gmail._thread(_THREAD)
    assert (t.id, t.count, t.last_id, t.snippet) == ("t1", 3, "m3", "snip m3")
    assert t.people == ["Alice <alice@x.com>", "Me <me@x.com>"]  # deduped by address
    assert t.unread and "UNREAD" in t.labels


def test_reply_target_defaults_to_latest_and_msg_picks_one():
    msgs = _THREAD["messages"]
    assert gmail.pick_reply_target(msgs)["id"] == "m3"
    assert gmail.pick_reply_target(msgs, "m2")["id"] == "m2"
    with pytest.raises(gmail.GmailError, match="isn't in this thread"):
        gmail.pick_reply_target(msgs, "zzz")


def test_derive_reply_to_my_own_message_goes_to_its_recipients():
    mine = _THREAD["messages"][1]
    r = gmail.derive_reply(mine, "me@x.com", reply_all=True)
    assert "alice@x.com" in r.to and "bob@x.com" in r.to and "me@x.com" not in r.to
    assert r.cc == ""  # its To isn't repeated as cc


class _HttpErr(Exception):
    def __init__(self, status):
        self.resp = type("R", (), {"status": status})()
        self.reason = "Not Found"


def test_full_thread_resolves_a_mid_thread_message_id(monkeypatch):
    """Gmail's thread id is its first message's id, so a later message id needs a lookup."""
    monkeypatch.setattr(gmail, "HttpError", _HttpErr)
    calls = []

    class _Svc:
        def users(self):
            return self

        def threads(self):
            self._kind = "thread"
            return self

        def messages(self):
            self._kind = "msg"
            return self

        def get(self, **kw):
            calls.append((self._kind, kw["id"]))
            self._args = (self._kind, kw["id"])
            return self

        def execute(self, num_retries=0):
            kind, i = self._args
            if kind == "thread":
                if i != "t1":
                    raise _HttpErr(404)
                return _THREAD
            return {"id": i, "threadId": "t1"}

    assert gmail._full_thread(_Svc(), "m3")["id"] == "t1"
    assert calls == [("thread", "m3"), ("msg", "m3"), ("thread", "t1")]


def test_cli_lists_threads_with_account(monkeypatch, tmp_path):
    import json

    from typer.testing import CliRunner

    from ko import cli_google
    from ko.cli import app

    monkeypatch.setenv("KO_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("KO_CONFIG_DIR", str(tmp_path / "cfg"))
    (tmp_path / "google_token_sfl.json").write_text(json.dumps({"scopes": [], "email": "me@x.com"}))
    monkeypatch.setattr(cli_google.gmail_mod, "recent_threads", lambda n, unread: [gmail._thread(_THREAD)])
    r = CliRunner().invoke(app, ["gmail", "-a", "sfl"])
    assert r.exit_code == 0, r.output
    assert "# gmail: sfl · me@x.com" in r.stderr  # which account, on stderr
    assert "● t1" in r.stdout and "Alice, me (3)  —  Budget" in r.stdout
    r = CliRunner().invoke(app, ["gmail", "-a", "sfl", "--json"])
    assert json.loads(r.stdout)[0]["account"] == "sfl"


def test_cli_draft_reply_passes_thread_and_msg(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from ko import cli_google
    from ko.cli import app

    monkeypatch.setenv("KO_STATE_DIR", str(tmp_path))
    seen = {}

    def _fake(ref, text, **kw):
        seen.update(ref=ref, text=text, **kw)
        return "d1", gmail.ReplyDraft("Alice <alice@x.com>", "", "Re: Budget", "t1", "<m3@x>", "<m3@x>")

    monkeypatch.setattr(cli_google.gmail_mod, "create_reply_draft", _fake)
    r = CliRunner().invoke(app, ["gmail", "draft", "reply", "t1", "--msg", "m2", "--body", "ok"])
    assert r.exit_code == 0, r.output
    assert r.stdout.strip() == "d1"
    assert (seen["ref"], seen["msg_id"], seen["text"]) == ("t1", "m2", "ok")
    assert "reply draft to Alice <alice@x.com>" in r.stderr


def test_unsent_drafts_are_not_reply_targets_or_the_latest():
    draft = {**_msg("d9", "Me <me@x.com>"), "labelIds": ["DRAFT"]}
    msgs = [*_THREAD["messages"], draft]
    assert gmail.pick_reply_target(msgs)["id"] == "m3"
    t = gmail._thread({"id": "t1", "messages": msgs})
    assert t.last_id == "m3" and t.count == 3


def test_not_found_names_the_account_only_for_id_lookups(monkeypatch):
    monkeypatch.setattr(gmail, "active_account", lambda: "sfl")
    with pytest.raises(gmail.GmailError, match="in account 'sfl'"):
        gmail._handle(_HttpErr(404), "m1", lookup=True)
    with pytest.raises(gmail.GmailError) as e:
        gmail._handle(_HttpErr(404), "r-123")  # a draft id: plain not-found
    assert "wrong -a" not in str(e.value)


# --- inline images -----------------------------------------------------------------------


def test_md_inline_image_becomes_related_part(tmp_path):
    """A local `![](shot.png)` is embedded: alternative[plain, related[html, image]], the html
    points at the part's Content-ID, one part per file; remote image URLs stay as written."""
    import base64
    from email import message_from_bytes

    (tmp_path / "my shot.png").write_bytes(b"\x89PNG fake")
    md = "Look:\n\n![the shot](my%20shot.png)\n\n![again](<my shot.png>) ![r](https://x.com/a.png)"
    html_body = gmail.md_to_html(md, base_dir=tmp_path)
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF")
    raw = gmail._build_raw(to="a@x.com", subject="s", body=md, html_body=html_body, attachments=[pdf])
    msg = message_from_bytes(base64.urlsafe_b64decode(raw))
    assert msg.get_content_type() == "multipart/mixed"
    types = [p.get_content_type() for p in msg.walk()]
    assert types == [
        "multipart/mixed", "multipart/alternative", "text/plain",
        "multipart/related", "text/html", "image/png", "application/pdf",
    ]
    img = next(p for p in msg.walk() if p.get_content_type() == "image/png")
    assert img.get_payload(decode=True) == b"\x89PNG fake"
    assert img.get_content_disposition() == "inline"
    cid = img["Content-ID"].strip("<>")
    html_out = next(p for p in msg.walk() if p.get_content_type() == "text/html").get_payload(decode=True).decode()
    assert html_out.count(f'src="cid:{cid}"') == 2
    assert 'src="https://x.com/a.png"' in html_out
    assert "file://" not in html_out


def test_md_inline_image_errors(tmp_path):
    with pytest.raises(gmail.GmailError, match="image not found: nope.png"):
        gmail.md_to_html("![x](nope.png)", base_dir=tmp_path)
    for name in ("notes.txt", "logo.svg"):
        (tmp_path / name).write_text("hi")
        with pytest.raises(gmail.GmailError, match=f"can't show {name} inline"):
            gmail.md_to_html(f"![x]({name})", base_dir=tmp_path)
    # without base_dir (no --md file), local srcs are left alone
    assert 'src="nope.png"' in gmail.md_to_html("![x](nope.png)")


def test_inline_image_symlink_judged_by_its_target(tmp_path):
    """A `.png` symlink to a non-image must not smuggle that file into the draft."""
    (tmp_path / "notes.txt").write_text("not an image")
    (tmp_path / "link.png").symlink_to(tmp_path / "notes.txt")
    raw_html = f'<p><img alt="x" src="{(tmp_path / "link.png").as_uri()}"></p>'
    with pytest.raises(gmail.GmailError, match="can't show notes.txt inline"):
        gmail._build_raw(to="a@x.com", subject="s", body="b", html_body=raw_html)
    with pytest.raises(gmail.GmailError, match="can't show notes.txt inline"):
        gmail.md_to_html("![x](link.png)", base_dir=tmp_path)


def test_raw_html_img_with_src_not_first_is_embedded(tmp_path):
    import base64
    from email import message_from_bytes

    (tmp_path / "shot.png").write_bytes(b"\x89PNG fake")
    raw_html = f'<img alt="x" width="200" src="{(tmp_path / "shot.png").as_uri()}">'
    raw = gmail._build_raw(to="a@x.com", subject="s", body="b", html_body=raw_html)
    msg = message_from_bytes(base64.urlsafe_b64decode(raw))
    assert [p.get_content_type() for p in msg.walk()].count("image/png") == 1
    html_out = next(p for p in msg.walk() if p.get_content_type() == "text/html").get_payload(decode=True).decode()
    assert "file://" not in html_out and 'src="cid:' in html_out
    # a scheme-relative URL is remote, not a local path
    assert 'src="//cdn.example.com/logo.png"' in gmail.md_to_html("![l](//cdn.example.com/logo.png)", base_dir=tmp_path)
