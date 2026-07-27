"""Gmail — read + attachments + drafts over the Gmail v1 API.

Concise by design (less verbose than the Gmail MCP): a list is one line per message
(id, date, from, subject, snippet); a view is headers + the plain-text body. Pass Gmail's
own search syntax verbatim — `from:`, `to:`, `subject:`, `newer_than:7d`, `is:unread`,
`has:attachment`. `view` reads one message; `thread` reads a whole conversation.

**Attachments** work under the read-only scope (list + download). **Drafts** (new / reply /
list / show / update / send) need `gmail.compose` — one re-auth per account. **Sending is
opt-in**: `draft send` refuses unless `[gmail] allow_send = true` / `KO_GMAIL_SEND=1`; the
draft-first workflow (agents draft, a human sends from Gmail) is the default.

Non-goals: labels / archive / mark-read (`gmail.modify`, later), re-attaching originals in
replies, inline/CID images, open tracking. Scheduled send has no API — do it in Gmail's UI.
"""

from __future__ import annotations

import base64
import html
import mimetypes
import re
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, getaddresses, parseaddr, parsedate_to_datetime
from pathlib import Path

from googleapiclient.errors import HttpError

from ko import config
from ko.google_auth import (
    GoogleError,
    get_credentials,
    get_gmail_service,
    raise_for_status,
)


@dataclass
class GmailMessage:
    id: str
    thread_id: str
    date: str  # "YYYY-MM-DD HH:MM" (local) if parseable, else the raw Date header
    from_: str
    to: str
    subject: str
    snippet: str
    unread: bool
    cc: str = ""
    bcc: str = ""  # only ever present on drafts — Gmail strips it from received mail


class GmailError(GoogleError):
    pass


def _handle(e: HttpError, context: str) -> None:
    # Gmail says 400 "Invalid id value" for malformed message/thread ids — speak
    # in ko's voice, not the API's ("no message with id 'X'", not "[400] Invalid id value")
    if e.resp.status == 400 and "invalid id" in (e.reason or "").lower():
        raise GmailError(f"no message or thread with id {context!r}") from e
    raise_for_status(
        e,
        context,
        not_found=GmailError,
        permission=GmailError,
        hint="Did you `ko gmail auth` (grants gmail.readonly)?",
    )


_META_HEADERS = ["From", "To", "Cc", "Subject", "Date"]


def _fmt_date(raw: str) -> str:
    """RFC-2822 Date header -> local 'YYYY-MM-DD HH:MM'; pass through if unparseable."""
    try:
        return parsedate_to_datetime(raw).astimezone().strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError):
        return raw


_INVISIBLE_CHARS_RE = re.compile(
    "[͏"          # U+034F COMBINING GRAPHEME JOINER (common ESP padding trick)
    "\u200b-‍"   # U+200B ZWSP, U+200C ZWNJ, U+200D ZWJ
    "﻿]"          # U+FEFF BOM / zero-width no-break space
)


def _clean_snippet(text: str) -> str:
    """Strip invisible padding characters inserted by marketing ESPs."""
    return _INVISIBLE_CHARS_RE.sub("", text)


def _message(m: dict) -> GmailMessage:
    """Build a GmailMessage from a messages.get response (metadata or full)."""
    h = {x["name"].lower(): x["value"] for x in m.get("payload", {}).get("headers", [])}
    return GmailMessage(
        id=m["id"],
        thread_id=m.get("threadId", ""),
        date=_fmt_date(h.get("date", "")),
        from_=h.get("from", ""),
        to=h.get("to", ""),
        subject=h.get("subject", "(no subject)"),
        cc=h.get("cc", ""),
        bcc=h.get("bcc", ""),
        snippet=_clean_snippet(html.unescape(m.get("snippet", ""))),
        unread="UNREAD" in m.get("labelIds", []),
    )


def search(query: str, n: int = 10) -> list[GmailMessage]:
    """Messages matching a Gmail query string (verbatim Gmail syntax). One metadata fetch per hit."""
    svc = get_gmail_service()
    try:
        # maxResults is a single-page cap (Gmail tops out at 500); we don't paginate — this is a
        # "first n" tool, so n stays small. Then one metadata fetch per hit for headers + snippet.
        resp = svc.users().messages().list(userId="me", q=query, maxResults=n).execute(num_retries=3)
        out = []
        for ref in resp.get("messages", []):
            m = (
                svc.users()
                .messages()
                .get(userId="me", id=ref["id"], format="metadata", metadataHeaders=_META_HEADERS)
                .execute(num_retries=3)
            )
            out.append(_message(m))
        return out
    except HttpError as e:
        _handle(e, f"search {query!r}")
        return []  # unreachable: _handle always raises (keeps the return type honest)


def recent(n: int = 10, unread: bool = False, query: str = "in:inbox") -> list[GmailMessage]:
    """Recent inbox messages (newest first), optionally only unread."""
    return search(query + (" is:unread" if unread else ""), n)


def from_sender(who: str, n: int = 10, unread: bool = False) -> list[GmailMessage]:
    """Messages from a person — a convenience wrapper over `from:<who>`."""
    return search(f"from:{who}" + (" is:unread" if unread else ""), n)


def _decode(data: str) -> str:
    pad = "=" * (-len(data) % 4)  # Gmail strips base64 padding
    return base64.urlsafe_b64decode(data + pad).decode("utf-8", "replace")


def _find_mime(payload: dict, mime: str) -> str:
    """First body of `mime` type anywhere in the (possibly nested) payload, decoded."""
    if payload.get("mimeType") == mime:
        data = payload.get("body", {}).get("data")
        return _decode(data) if data else ""
    for part in payload.get("parts", []):
        found = _find_mime(part, mime)
        if found:
            return found
    return ""


def _message_body(m: dict) -> str:
    """Plain-text body of a full message dict (falls back to text/html)."""
    payload = m.get("payload", {})
    return _find_mime(payload, "text/plain") or _find_mime(payload, "text/html")


def get_message(msg_id: str) -> tuple[GmailMessage, str]:
    """A single message: (metadata, plain-text body). Falls back to text/html if no plain part."""
    svc = get_gmail_service()
    try:
        m = svc.users().messages().get(userId="me", id=msg_id, format="full").execute(num_retries=3)
        return _message(m), _message_body(m)
    except HttpError as e:
        _handle(e, msg_id)
        raise  # unreachable: _handle always raises


def get_thread(thread_id: str) -> list[tuple[GmailMessage, str]]:
    """A whole conversation: every message as (metadata, plain-text body), oldest first.
    Pass a thread id (a message's thread_id, or the same id Gmail shows for single-message threads)."""
    svc = get_gmail_service()
    try:
        t = svc.users().threads().get(userId="me", id=thread_id, format="full").execute(num_retries=3)
        return [(_message(m), _message_body(m)) for m in t.get("messages", [])]
    except HttpError as e:
        _handle(e, thread_id)
        raise  # unreachable: _handle always raises


# --- attachments (read-only scope) -------------------------------------------------------


@dataclass
class Attachment:
    index: int  # 1-based, in payload walk order — the number `ko gmail attach <id> N` takes
    filename: str
    mime_type: str
    size: int  # bytes, as Gmail reports
    attachment_id: str


def _headers(m: dict) -> dict[str, str]:
    """Lowercased header name -> value for a message dict (last wins on duplicates)."""
    return {h["name"].lower(): h["value"] for h in m.get("payload", {}).get("headers", [])}


def _decode_bytes(data: str) -> bytes:
    """base64url (Gmail strips padding) -> raw bytes."""
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _walk_attachments(payload: dict) -> list[tuple[str, str, int, str]]:
    """Every attachment part anywhere in a (nested) payload: (filename, mimeType, size, attachmentId).
    An attachment is any part with a filename AND an attachmentId (inline parts without a filename
    — e.g. the alternative text/html body — are skipped)."""
    out: list[tuple[str, str, int, str]] = []

    def rec(part: dict) -> None:
        body = part.get("body", {})
        filename = part.get("filename") or ""
        att_id = body.get("attachmentId")
        if filename and att_id:
            out.append((filename, part.get("mimeType", ""), body.get("size", 0) or 0, att_id))
        for p in part.get("parts", []):
            rec(p)

    rec(payload)
    return out


def list_attachments(msg_id: str) -> list[Attachment]:
    """Attachments on a message, 1-based in payload order. Works under gmail.readonly."""
    svc = get_gmail_service()
    try:
        m = svc.users().messages().get(userId="me", id=msg_id, format="full").execute(num_retries=3)
    except HttpError as e:
        _handle(e, msg_id)
        raise  # unreachable
    return [
        Attachment(i, name, mime, size, att_id)
        for i, (name, mime, size, att_id) in enumerate(_walk_attachments(m.get("payload", {})), 1)
    ]


def _unique_path(path: Path) -> Path:
    """A non-colliding path: `report.pdf` -> `report.pdf`, then `report-1.pdf`, `report-2.pdf`, …
    (checks the filesystem, so it also dodges collisions within one download batch)."""
    if not path.exists():
        return path
    i = 1
    while True:
        cand = path.with_name(f"{path.stem}-{i}{path.suffix}")
        if not cand.exists():
            return cand
        i += 1


def download_attachments(msg_id: str, index: int | None = None, out_dir: Path | None = None) -> list[Path]:
    """Download attachment `index` (1-based; None = all) to `out_dir` (default cwd). Returns the
    saved Path(s). Never overwrites — appends -1, -2, …. Works under gmail.readonly."""
    out_dir = Path(out_dir) if out_dir is not None else Path.cwd()
    atts = list_attachments(msg_id)
    if not atts:
        raise GmailError(f"message {msg_id!r} has no attachments")
    if index is not None:
        match = next((a for a in atts if a.index == index), None)
        if match is None:
            raise GmailError(
                f"no attachment {index} on {msg_id!r} — it has {len(atts)} "
                f"(1-{len(atts)}); run `ko gmail attach {msg_id}` to list them"
            )
        atts = [match]
    out_dir.mkdir(parents=True, exist_ok=True)
    svc = get_gmail_service()
    saved = []
    for a in atts:
        try:
            body = (
                svc.users()
                .messages()
                .attachments()
                .get(userId="me", messageId=msg_id, id=a.attachment_id)
                .execute(num_retries=3)
            )
        except HttpError as e:
            _handle(e, msg_id)
            raise  # unreachable
        name = Path(a.filename).name or "attachment"  # strip any path components in the filename
        path = _unique_path(out_dir / name)
        path.write_bytes(_decode_bytes(body["data"]))
        saved.append(path)
    return saved


# --- drafts (needs gmail.compose) --------------------------------------------------------

COMPOSE_SCOPE = "https://www.googleapis.com/auth/gmail.compose"


def _compose_service():
    """A Gmail service for write ops, first checking the cached token actually granted
    gmail.compose — a read-only token can't create drafts, so fail loud with a re-auth hint
    instead of a raw 403 (mirrors google_auth's _WRITE_MARKER_SCOPE precedent)."""
    creds = get_credentials(readonly=True)  # one token/account; readonly reuses it without a browser
    if COMPOSE_SCOPE not in (creds.scopes or []):
        raise GmailError(
            "re-auth needed: this Gmail token can't create drafts (missing gmail.compose). "
            "Run `ko gmail auth --logout` then `ko gmail auth`."
        )
    return get_gmail_service()  # built from the same (compose-capable) token


def _no_newlines(name: str, value: str) -> None:
    """Reject CR/LF in a header value — header injection guard (a newline could smuggle
    extra headers into the raw MIME)."""
    if value and ("\r" in value or "\n" in value):
        raise GmailError(f"newline not allowed in {name} header value")


def _re_subject(subject: str) -> str:
    """Prefix 'Re: ' once (case-insensitive) — 'Budget' -> 'Re: Budget', 'RE: Budget' unchanged."""
    s = subject.strip()
    return s if s[:3].lower() == "re:" else f"Re: {s}"


_TABLE_CSS = "border:1px solid #ddd;padding:3px 8px;"


def _style_tables(html_out: str) -> str:
    """Inline faint-gray borders onto table/th/td — Gmail ignores <style> blocks, so
    email-safe styling must ride on the tags. Merges with the text-align style GFM
    column alignment already puts on th/td. Pure."""
    html_out = html_out.replace("<table>", '<table style="border-collapse:collapse">')
    for tag in ("th", "td"):
        # aligned cells (style="text-align:…") first, then bare ones — this order can't double-inject
        html_out = html_out.replace(f'<{tag} style="', f'<{tag} style="{_TABLE_CSS}')
        html_out = html_out.replace(f"<{tag}>", f'<{tag} style="{_TABLE_CSS}">')
    return html_out


def md_to_html(md: str) -> str:
    """Markdown -> HTML via markdown-it-py: CommonMark + GFM tables/strikethrough,
    tables styled inline (faint borders) so they read as tables in Gmail.
    (Tables are a GFM extension — the bare CommonMark preset renders them as literal
    pipe text, which is how an agent's invoice table once went out.)"""
    from markdown_it import MarkdownIt

    out = MarkdownIt("commonmark").enable(["table", "strikethrough"]).render(md)
    return _style_tables(out)


def _build_raw(
    *,
    to: str,
    subject: str,
    body: str,
    html_body: str | None = None,
    cc: str = "",
    bcc: str = "",
    in_reply_to: str = "",
    references: str = "",
    attachments: list[Path] | None = None,
) -> str:
    """A base64url raw MIME message for drafts.create/update.

    Plain `body` -> a simple text/plain message. With `html_body` -> multipart/alternative,
    the plain part FIRST then html, both quoted-printable / utf-8. Threading headers
    (In-Reply-To / References) are set when `in_reply_to` is given. `attachments` are
    read from disk and added (multipart/mixed) — Gmail caps a message around 25 MB."""
    for hname, val in (
        ("To", to),
        ("Cc", cc),
        ("Bcc", bcc),
        ("Subject", subject),
        ("In-Reply-To", in_reply_to),
        ("References", references),
    ):
        _no_newlines(hname, val)
    msg = EmailMessage()
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    if bcc:
        msg["Bcc"] = bcc
    msg["Subject"] = subject
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = references or in_reply_to
    msg.set_content(body, subtype="plain", cte="quoted-printable")
    if html_body is not None:
        msg.add_alternative(html_body, subtype="html", cte="quoted-printable")
    for path in attachments or []:
        try:
            data = path.read_bytes()
        except OSError as e:
            raise GmailError(f"can't read attachment {path}: {e}") from e
        ctype, _ = mimetypes.guess_type(path.name)
        maintype, _, subtype = (ctype or "application/octet-stream").partition("/")
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=path.name)
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()


@dataclass
class ReplyDraft:
    to: str
    cc: str
    subject: str
    thread_id: str  # "" when a subject override breaks threading
    in_reply_to: str
    references: str


def _email(addr: str) -> str:
    return parseaddr(addr)[1].lower()


def derive_reply(
    orig: dict, my_addr: str, *, reply_all: bool = False, subject_override: str | None = None
) -> ReplyDraft:
    """Reply fields from the original message dict. `to` = Reply-To if present else From;
    `--all` adds the original To+Cc as Cc, minus our own address. `Re: ` prefixed once;
    References = original References + its Message-ID. A subject override that differs from the
    threaded subject drops threadId (keeping the RFC headers). No Message-ID -> can't thread."""
    h = _headers(orig)
    msg_id = h.get("message-id")
    if not msg_id:
        raise GmailError("original message has no Message-ID header — can't thread a reply")
    to = h.get("reply-to") or h.get("from", "")
    cc = ""
    if reply_all:
        mine = _email(my_addr)
        seen, keep = {mine, _email(to)}, []
        for _name, email in getaddresses([h.get("to", ""), h.get("cc", "")]):
            e = email.lower()
            if e and e not in seen:
                seen.add(e)
                keep.append(formataddr((_name, email)))
        cc = ", ".join(keep)
    threaded_subject = _re_subject(h.get("subject", ""))
    thread_id = orig.get("threadId", "")
    if subject_override is not None and subject_override.strip().lower() != threaded_subject.lower():
        subject = subject_override
        thread_id = ""  # a different subject is a new thread — drop threadId, keep RFC headers
    else:
        subject = subject_override if subject_override is not None else threaded_subject
    references = (h.get("references", "").strip() + " " + msg_id).strip()
    return ReplyDraft(to, cc, subject, thread_id, msg_id, references)


def create_draft(
    to: str,
    subject: str,
    body: str,
    *,
    cc: str = "",
    html_body: str | None = None,
    attachments: list[Path] | None = None,
) -> str:
    """Create a new draft; returns its (stable) draft id."""
    raw = _build_raw(
        to=to, subject=subject, body=body, cc=cc, html_body=html_body, attachments=attachments
    )
    svc = _compose_service()
    try:
        d = svc.users().drafts().create(userId="me", body={"message": {"raw": raw}}).execute(num_retries=3)
    except HttpError as e:
        _handle(e, "create draft")
        raise  # unreachable
    return d["id"]


def _merge_cc(to: str, cc: str, extra: str) -> str:
    """Append extra cc addresses to `cc`, skipping any already in To or Cc (by email,
    case-insensitive). Returns the merged cc string."""
    if not extra:
        return cc
    seen = {a.lower() for _, a in getaddresses([to, cc]) if a}
    merged = [cc] if cc else []
    for name, addr in getaddresses([extra]):
        if addr and addr.lower() not in seen:
            merged.append(formataddr((name, addr)) if name else addr)
            seen.add(addr.lower())
    return ", ".join(merged)


def create_reply_draft(
    msg_id: str,
    body: str,
    *,
    html_body: str | None = None,
    reply_all: bool = False,
    subject_override: str | None = None,
    extra_cc: str = "",
    attachments: list[Path] | None = None,
) -> str:
    """Create a reply draft threaded onto message `msg_id`; returns its draft id.
    `extra_cc` adds cc addresses on top of whatever reply/reply-all derived (deduped)."""
    svc = _compose_service()
    try:
        orig = svc.users().messages().get(userId="me", id=msg_id, format="full").execute(num_retries=3)
    except HttpError as e:
        _handle(e, msg_id)
        raise  # unreachable
    r = derive_reply(
        orig,
        my_address() if reply_all else "",  # only --all filters self out of To+Cc
        reply_all=reply_all,
        subject_override=subject_override,
    )
    raw = _build_raw(
        to=r.to,
        cc=_merge_cc(r.to, r.cc, extra_cc),
        subject=r.subject,
        body=body,
        html_body=html_body,
        in_reply_to=r.in_reply_to,
        references=r.references,
        attachments=attachments,
    )
    message: dict = {"raw": raw}
    if r.thread_id:
        message["threadId"] = r.thread_id
    try:
        d = svc.users().drafts().create(userId="me", body={"message": message}).execute(num_retries=3)
    except HttpError as e:
        _handle(e, "create reply draft")
        raise  # unreachable
    return d["id"]


@dataclass
class DraftInfo:
    id: str
    to: str
    subject: str
    cc: str = ""


def list_drafts(n: int = 10) -> list[DraftInfo]:
    """Recent drafts (id, to, subject, cc). Keyed on draft id — stable across updates."""
    svc = _compose_service()
    try:
        resp = svc.users().drafts().list(userId="me", maxResults=n).execute(num_retries=3)
        out = []
        for ref in resp.get("drafts", []):
            # drafts.get takes only `format` — no metadataHeaders filter (that's messages.get)
            d = (
                svc.users()
                .drafts()
                .get(userId="me", id=ref["id"], format="metadata")
                .execute(num_retries=3)
            )
            h = _headers(d.get("message", {}))
            out.append(
                DraftInfo(
                    d["id"], h.get("to", ""), h.get("subject", "(no subject)"), h.get("cc", "")
                )
            )
        return out
    except HttpError as e:
        _handle(e, "list drafts")
        raise  # unreachable


def get_draft(draft_id: str) -> tuple[str, GmailMessage, str]:
    """A draft as (draft_id, metadata, plain-text body)."""
    svc = _compose_service()
    try:
        d = svc.users().drafts().get(userId="me", id=draft_id, format="full").execute(num_retries=3)
    except HttpError as e:
        _handle(e, draft_id)
        raise  # unreachable
    m = d.get("message", {})
    return d["id"], _message(m), _message_body(m)


def draft_summary(draft_id: str) -> dict:
    """A draft's send-preview facts: {to, cc, subject, attachments} — body deliberately omitted."""
    svc = _compose_service()
    try:
        d = svc.users().drafts().get(userId="me", id=draft_id, format="full").execute(num_retries=3)
    except HttpError as e:
        _handle(e, draft_id)
        raise  # unreachable
    m = d.get("message", {})
    h = _headers(m)
    return {
        "to": h.get("to", ""),
        "cc": h.get("cc", ""),
        "bcc": h.get("bcc", ""),
        "subject": h.get("subject", "(no subject)"),
        "attachments": len(_walk_attachments(m.get("payload", {}))),
    }


def _preserved_fields(msg: dict, subject_override: str | None) -> dict:
    """Fields to carry over on a draft update (the API replaces the whole MIME message, so
    anything omitted must be re-supplied): to / cc / subject / thread_id + the RFC threading
    headers. `subject_override` (when not None) wins over the existing subject."""
    h = _headers(msg)
    return {
        "to": h.get("to", ""),
        "cc": h.get("cc", ""),
        "bcc": h.get("bcc", ""),
        "subject": subject_override if subject_override is not None else h.get("subject", ""),
        "in_reply_to": h.get("in-reply-to", ""),
        "references": h.get("references", ""),
        "thread_id": msg.get("threadId", ""),
    }


def update_draft(
    draft_id: str,
    body: str,
    *,
    html_body: str | None = None,
    subject_override: str | None = None,
    to_override: str | None = None,
    cc_override: str | None = None,
    force: bool = False,
) -> str:
    """Replace a draft's body (and optionally subject/to/cc — the repair case: fix a recipient
    list without retyping the rest). Re-fetches the draft first and preserves
    to/cc/subject/threadId for anything not overridden. Draft id is stable; returns it.

    The API replaces the WHOLE MIME message: attachments added to the draft — e.g. in
    Gmail's UI — cannot be carried over and would be dropped, so a draft with attachments
    is refused unless force=True. To/cc/bcc/subject/threading are preserved."""
    svc = _compose_service()
    try:
        existing = svc.users().drafts().get(userId="me", id=draft_id, format="full").execute(num_retries=3)
    except HttpError as e:
        _handle(e, draft_id)
        raise  # unreachable
    n_att = len(_walk_attachments(existing.get("message", {}).get("payload", {})))
    if n_att and not force:
        raise GmailError(
            f"refusing: draft {draft_id} has {n_att} attachment(s) this update would drop "
            "(the API replaces the whole message) — pass --force to update anyway"
        )
    f = _preserved_fields(existing.get("message", {}), subject_override)
    if to_override is not None:
        f["to"] = to_override
    if cc_override is not None:
        f["cc"] = cc_override
    raw = _build_raw(
        to=f["to"],
        cc=f["cc"],
        bcc=f["bcc"],
        subject=f["subject"],
        body=body,
        html_body=html_body,
        in_reply_to=f["in_reply_to"],
        references=f["references"],
    )
    message: dict = {"raw": raw}
    if f["thread_id"]:
        message["threadId"] = f["thread_id"]
    try:
        d = (
            svc.users()
            .drafts()
            .update(userId="me", id=draft_id, body={"message": message})
            .execute(num_retries=3)
        )
    except HttpError as e:
        _handle(e, draft_id)
        raise  # unreachable
    return d["id"]


def send_allowed() -> bool:
    """Is sending enabled? `[gmail] allow_send = true` in config.toml or KO_GMAIL_SEND=1.
    Off by default — drafts are the workflow, a human sends from Gmail."""
    val = config.setting("KO_GMAIL_SEND", "gmail", "allow_send", None)
    return val is not None and str(val).strip().lower() in ("1", "true", "yes", "on")


def send_draft(draft_id: str) -> str:
    """Send an existing draft; returns the sent message id. Gate + confirm live in the CLI."""
    svc = _compose_service()
    try:
        sent = svc.users().drafts().send(userId="me", body={"id": draft_id}).execute(num_retries=3)
    except HttpError as e:
        _handle(e, draft_id)
        raise  # unreachable
    return sent.get("id", "")


@dataclass
class _ProfileCache:
    addr: str | None = None


_PROFILE = _ProfileCache()


def my_address() -> str:
    """The authed account's own email (users.getProfile — works under readonly). Cached per run."""
    if _PROFILE.addr is None:
        svc = get_gmail_service()
        try:
            _PROFILE.addr = svc.users().getProfile(userId="me").execute(num_retries=3)["emailAddress"]
        except HttpError as e:
            _handle(e, "get profile")
            raise  # unreachable
    return _PROFILE.addr
