"""Gmail — read + attachments + drafts over the Gmail v1 API.

Thread-first, like Gmail itself (a lone email is a one-message thread): a list is one line per
thread (id, latest date, who, count, subject, latest snippet); `thread` reads a conversation;
`view` reads one message. Replies go to a thread (its latest message, as Gmail's Reply does)
or to one message in it. Pass Gmail's own search syntax verbatim — `from:`, `to:`, `subject:`,
`newer_than:7d`, `is:unread`, `has:attachment`. The message-level `search`/`recent` stay for
`ko brief`.

**Attachments** work under the read-only scope (list + download). **Drafts** (new / reply /
list / show / update / send) need `gmail.compose` — one re-auth per account. **Sending is
opt-in**: `draft send` refuses unless `[gmail] allow_send = true` / `KO_GMAIL_SEND=1`; the
draft-first workflow (agents draft, a human sends from Gmail) is the default.

Non-goals: labels / archive / mark-read (`gmail.modify`, later), re-attaching originals in
replies, open tracking. Inline images: a local `![alt](shot.png)` in `--md` is embedded
(multipart/related, `cid:`); http(s) image URLs stay remote. Scheduled send has no API — do it in Gmail's UI.
"""

from __future__ import annotations

import base64
import html
import mimetypes
import re
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import (
    formataddr,
    getaddresses,
    make_msgid,
    parseaddr,
    parsedate_to_datetime,
)
from pathlib import Path
from typing import NoReturn
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

from googleapiclient.errors import HttpError

from ko import config
from ko.google_auth import (
    GoogleError,
    active_account,
    get_credentials,
    raise_for_status,
    service,
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
    labels: list[str] = field(default_factory=list)  # Gmail labelIds (INBOX, UNREAD, CATEGORY_*, …)
    list_unsubscribe: str = ""  # the List-Unsubscribe header — set on newsletters, empty on human mail


class GmailError(GoogleError):
    pass


def _handle(e: HttpError, context: str, lookup: bool = False) -> NoReturn:
    # Gmail says 400 "Invalid id value" for malformed message/thread ids — speak
    # in ko's voice, not the API's ("no message with id 'X'", not "[400] Invalid id value")
    # Message/thread ids are per-account, so a not-found on one (`lookup`) names the account —
    # the usual cause is a missing -a.
    if lookup and (e.resp.status == 404 or (e.resp.status == 400 and "invalid id" in (e.reason or "").lower())):
        raise GmailError(
            f"no message or thread with id {context!r} in account '{active_account()}' — wrong -a?"
        ) from e
    raise_for_status(
        e,
        context,
        not_found=GmailError,
        permission=GmailError,
        hint="Did you `ko google auth` (grants gmail.readonly)?",
    )


_META_HEADERS = ["From", "To", "Cc", "Subject", "Date", "List-Unsubscribe"]


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
        labels=m.get("labelIds", []),
        list_unsubscribe=h.get("list-unsubscribe", ""),
    )


def search(query: str, n: int = 10, account: str | None = None) -> list[GmailMessage]:
    """Messages matching a Gmail query string (verbatim Gmail syntax). One metadata fetch per hit."""
    svc = service("gmail", account=account)
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


def recent(
    n: int = 10, unread: bool = False, query: str = "in:inbox", account: str | None = None
) -> list[GmailMessage]:
    """Recent inbox messages (newest first), optionally only unread."""
    return search(query + (" is:unread" if unread else ""), n, account)


@dataclass
class GmailThread:
    id: str
    date: str  # the latest message's
    subject: str  # the first message's
    people: list[str]  # distinct senders' From values, oldest first
    count: int
    unread: bool  # any message unread
    snippet: str  # the latest message's
    last_id: str  # the latest message's id
    labels: list[str] = field(default_factory=list)  # union over the thread's messages
    list_unsubscribe: str = ""  # first one found — set on newsletters, empty on human mail


def _sent(msgs: list[dict]) -> list[dict]:
    """A thread's messages minus unsent drafts (Gmail returns those too) — unless that's all."""
    return [m for m in msgs if "DRAFT" not in m.get("labelIds", [])] or msgs


def _thread(t: dict) -> GmailThread:
    msgs = [_message(m) for m in _sent(t.get("messages", []))]
    first, last = msgs[0], msgs[-1]
    people: list[str] = []
    for m in msgs:
        if m.from_ and email_of(m.from_) not in {email_of(p) for p in people}:
            people.append(m.from_)
    return GmailThread(
        id=t["id"],
        date=last.date,
        subject=first.subject,
        people=people,
        count=len(msgs),
        unread=any(m.unread for m in msgs),
        snippet=last.snippet,
        last_id=last.id,
        labels=sorted({lab for m in msgs for lab in m.labels}),
        list_unsubscribe=next((m.list_unsubscribe for m in msgs if m.list_unsubscribe), ""),
    )


def search_threads(query: str, n: int = 10) -> list[GmailThread]:
    """Threads with any message matching a Gmail query, newest activity first. One metadata
    fetch per thread."""
    svc = service("gmail")
    try:
        resp = svc.users().threads().list(userId="me", q=query, maxResults=n).execute(num_retries=3)
        return [
            _thread(
                svc.users()
                .threads()
                .get(userId="me", id=ref["id"], format="metadata", metadataHeaders=_META_HEADERS)
                .execute(num_retries=3)
            )
            for ref in resp.get("threads", [])
        ]
    except HttpError as e:
        _handle(e, f"search {query!r}")
        return []  # unreachable


def recent_threads(n: int = 10, unread: bool = False) -> list[GmailThread]:
    """Inbox threads, newest activity first, optionally only those with unread mail."""
    return search_threads("in:inbox" + (" is:unread" if unread else ""), n)


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
    svc = service("gmail")
    try:
        m = svc.users().messages().get(userId="me", id=msg_id, format="full").execute(num_retries=3)
        return _message(m), _message_body(m)
    except HttpError as e:
        _handle(e, msg_id, lookup=True)


def _full_thread(svc, ref: str) -> dict:
    """A thread (format=full) from a thread id OR the id of any message in it. Gmail gives a
    thread its first message's id, so a later message's id needs a lookup of its threadId."""
    try:
        return svc.users().threads().get(userId="me", id=ref, format="full").execute(num_retries=3)
    except HttpError as e:
        if e.resp.status not in (400, 404):
            _handle(e, ref, lookup=True)
    try:
        m = svc.users().messages().get(userId="me", id=ref, format="minimal").execute(num_retries=3)
        return svc.users().threads().get(userId="me", id=m["threadId"], format="full").execute(num_retries=3)
    except HttpError as e:
        _handle(e, ref, lookup=True)


def get_thread(ref: str) -> list[tuple[GmailMessage, str]]:
    """A whole conversation: every message as (metadata, plain-text body), oldest first.
    `ref` is a thread id or the id of any message in the thread."""
    t = _full_thread(service("gmail"), ref)
    return [(_message(m), _message_body(m)) for m in t.get("messages", [])]


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
    svc = service("gmail")
    try:
        m = svc.users().messages().get(userId="me", id=msg_id, format="full").execute(num_retries=3)
    except HttpError as e:
        _handle(e, msg_id, lookup=True)
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
    svc = service("gmail")
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
            _handle(e, msg_id, lookup=True)
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
            f"Run `ko google auth {active_account()}` to re-consent."
        )
    return service("gmail")  # built from the same (compose-capable) token


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


_INLINE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}  # what Gmail shows inline


def _inline_image_type(path: Path) -> str:
    """The image's MIME type, or a GmailError when Gmail can't show it inline (svg, heic, non-images)."""
    ctype, _ = mimetypes.guess_type(path.name)
    if ctype not in _INLINE_TYPES:
        raise GmailError(f"can't show {path.name} inline ({ctype or 'unknown type'}); use png, jpeg, gif or webp")
    return ctype


def _local_images_to_file_uris(tokens: list, base_dir: Path) -> None:
    """Point every local image (no URL scheme) at an absolute file:// URI, resolved against
    `base_dir` — `_build_raw` embeds those as inline parts. A missing file is an error naming it."""
    for tok in tokens:
        for child in tok.children or []:
            if child.type != "image":
                continue
            src = child.attrs.get("src", "")
            url = urlparse(src)
            if not src or url.scheme or url.netloc:  # http(s), //host, cid:, data: … stay as written
                continue
            path = (base_dir / Path(unquote(src)).expanduser()).resolve()
            if not path.is_file():
                raise GmailError(f"image not found: {src} (looked for {path})")
            _inline_image_type(path)
            child.attrs["src"] = path.as_uri()


def md_to_html(md: str, base_dir: Path | None = None) -> str:
    """Markdown -> HTML via markdown-it-py: CommonMark + GFM tables/strikethrough,
    tables styled inline (faint borders) so they read as tables in Gmail.
    (Tables are a GFM extension — the bare CommonMark preset renders them as literal
    pipe text, which is how an agent's invoice table once went out.)
    With `base_dir`, local images become file:// URIs that `_build_raw` embeds inline."""
    from markdown_it import MarkdownIt

    parser = MarkdownIt("commonmark").enable(["table", "strikethrough"])
    tokens = parser.parse(md)
    if base_dir is not None:
        _local_images_to_file_uris(tokens, base_dir)
    out = parser.renderer.render(tokens, parser.options, {})
    return _style_tables(out)


_FILE_IMG_RE = re.compile(r'(<img\b[^>]*?(?<![-\w])src=")(file://[^"]+)(")')  # not data-src


def _embed_file_images(html_body: str) -> tuple[str, list[tuple[str, Path]]]:
    """Swap each `<img src="file://…">` for a `cid:` reference. Returns the new HTML and the
    (cid, path) pairs to add as related parts; one part per file, however often it's used.
    Raw HTML in the Markdown that writes `<img src="file://…">` itself is embedded the same way.
    Paths are resolved, so a symlink is judged by the file it points at."""
    cids: dict[Path, str] = {}

    def swap(m: re.Match) -> str:
        path = Path(url2pathname(urlparse(html.unescape(m.group(2))).path)).resolve()
        if path not in cids:
            cids[path] = make_msgid(domain="ko")[1:-1]  # strip the <>
        return f"{m.group(1)}cid:{cids[path]}{m.group(3)}"

    out = _FILE_IMG_RE.sub(swap, html_body)
    return out, [(cid, path) for path, cid in cids.items()]


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
    (In-Reply-To / References) are set when `in_reply_to` is given. `<img src="file://…">`
    in the html is embedded inline (the html part becomes multipart/related). `attachments` are
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
        html_body, inline = _embed_file_images(html_body)
        msg.add_alternative(html_body, subtype="html", cte="quoted-printable")
        html_part = msg.get_payload()[-1]
        for cid, path in inline:
            ctype = _inline_image_type(path)
            try:
                data = path.read_bytes()
            except OSError as e:
                raise GmailError(f"can't read image {path}: {e}") from e
            maintype, _, subtype = ctype.partition("/")
            # no filename on purpose: _walk_attachments skips it, so `draft update` doesn't refuse
            html_part.add_related(data, maintype=maintype, subtype=subtype, cid=f"<{cid}>", disposition="inline")
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


def email_of(addr: str) -> str:
    return parseaddr(addr)[1].lower()


def derive_reply(
    orig: dict, my_addr: str, *, reply_all: bool = False, subject_override: str | None = None
) -> ReplyDraft:
    """Reply fields from the original message dict. `to` = Reply-To if present else From — or,
    replying to our own message, its original To (as Gmail does); `--all` adds the original
    To+Cc as Cc, minus our own address. `Re: ` prefixed once;
    References = original References + its Message-ID. A subject override that differs from the
    threaded subject drops threadId (keeping the RFC headers). No Message-ID -> can't thread."""
    h = _headers(orig)
    msg_id = h.get("message-id")
    if not msg_id:
        raise GmailError("original message has no Message-ID header — can't thread a reply")
    from_me = bool(my_addr) and email_of(h.get("from", "")) == email_of(my_addr)
    to = h.get("to", "") if from_me else (h.get("reply-to") or h.get("from", ""))
    cc = ""
    if reply_all:
        mine = email_of(my_addr)
        seen, keep = {mine} | {a.lower() for _, a in getaddresses([to]) if a}, []
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


def pick_reply_target(msgs: list[dict], msg_id: str | None = None) -> dict:
    """Which message in a thread a reply answers: `msg_id` if given (must be in the thread),
    else the latest non-draft — as Gmail's own Reply does. If that one is mine, derive_reply
    addresses its recipients, not me."""
    if msg_id:
        for m in msgs:
            if m["id"] == msg_id:
                return m
        raise GmailError(f"message {msg_id!r} isn't in this thread — `ko gmail thread` lists its ids")
    return _sent(msgs)[-1]


def create_reply_draft(
    ref: str,
    body: str,
    *,
    msg_id: str | None = None,
    html_body: str | None = None,
    reply_all: bool = False,
    subject_override: str | None = None,
    extra_cc: str = "",
    attachments: list[Path] | None = None,
) -> tuple[str, ReplyDraft]:
    """Create a reply draft in thread `ref` (a thread id or any message id in it), answering
    `msg_id` or else the thread's latest message. Returns (draft id, the
    derived reply fields). `extra_cc` adds cc addresses on top of reply/reply-all's (deduped)."""
    svc = _compose_service()
    thread = _full_thread(svc, ref)
    me = my_address()
    orig = pick_reply_target(thread.get("messages", []), msg_id)
    r = derive_reply(orig, me, reply_all=reply_all, subject_override=subject_override)
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
    return d["id"], r


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


def get_draft(draft_id: str) -> tuple[str, GmailMessage, str, list[str]]:
    """A draft as (draft_id, metadata, plain-text body, attachment filenames). The filenames let
    `show` confirm what's attached — after `--attach` there's otherwise no way to verify it stuck."""
    svc = _compose_service()
    try:
        d = svc.users().drafts().get(userId="me", id=draft_id, format="full").execute(num_retries=3)
    except HttpError as e:
        _handle(e, draft_id)
    m = d.get("message", {})
    names = [name for name, _mime, _size, _att_id in _walk_attachments(m.get("payload", {}))]
    return d["id"], _message(m), _message_body(m), names


_ATTACH_RE = re.compile(r"attach", re.IGNORECASE)


def mentions_attachment(body: str) -> bool:
    """Does the plain-text body mention an attachment (attach / attached / attachment)?
    A deliberately crude /attach/i substring — the guard's whole job is to catch 'please find
    attached' with nothing attached, so over-matching is fine (a human passes --yes to override)."""
    return bool(_ATTACH_RE.search(body or ""))


def draft_summary(draft_id: str) -> dict:
    """A draft's send-preview facts: {to, cc, subject, attachments, mentions_attachment}. The
    body text is deliberately omitted; `mentions_attachment` is a derived flag off it (the plain
    part) so the send path can catch a 'see attached' email with zero attachments."""
    svc = _compose_service()
    try:
        d = svc.users().drafts().get(userId="me", id=draft_id, format="full").execute(num_retries=3)
    except HttpError as e:
        _handle(e, draft_id)
    m = d.get("message", {})
    h = _headers(m)
    return {
        "to": h.get("to", ""),
        "cc": h.get("cc", ""),
        "bcc": h.get("bcc", ""),
        "subject": h.get("subject", "(no subject)"),
        "attachments": len(_walk_attachments(m.get("payload", {}))),
        "mentions_attachment": mentions_attachment(_message_body(m)),
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
    return d["id"]


def send_allowed() -> bool:
    """Is sending enabled? `[gmail] allow_send = true` in config.toml or KO_GMAIL_SEND=1.
    Off by default — drafts are the workflow, a human sends from Gmail."""
    val = config.setting("KO_GMAIL_SEND", "gmail", "allow_send", None)
    return val is not None and str(val).strip().lower() in ("1", "true", "yes", "on")


def send_draft(draft_id: str) -> str:
    """Send an existing draft; returns the sent message id. Refuses unless send_allowed(), so
    no caller can skip the gate; the CLI adds the confirm."""
    if not send_allowed():
        raise GmailError("sending is disabled: set `[gmail] allow_send = true` in config.toml or KO_GMAIL_SEND=1")
    svc = _compose_service()
    try:
        sent = svc.users().drafts().send(userId="me", body={"id": draft_id}).execute(num_retries=3)
    except HttpError as e:
        _handle(e, draft_id)
    return sent.get("id", "")


_PROFILE: dict[str, str] = {}  # account tag -> its address, per run


def my_address() -> str:
    """The authed account's own email (users.getProfile — works under readonly). Cached per
    account per run: `-a` switches account, and the address must follow."""
    account = active_account()
    if account not in _PROFILE:
        svc = service("gmail")
        try:
            _PROFILE[account] = svc.users().getProfile(userId="me").execute(num_retries=3)["emailAddress"]
        except HttpError as e:
            _handle(e, "get profile")
    return _PROFILE[account]
