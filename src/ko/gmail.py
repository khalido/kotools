"""Gmail reader — read-only search / list / view over the Gmail v1 API.

Concise by design (less verbose than the Gmail MCP): a list is one line per message
(id, date, from, subject, snippet); a view is headers + the plain-text body. Pass Gmail's
own search syntax verbatim — `from:`, `to:`, `subject:`, `newer_than:7d`, `is:unread`,
`has:attachment`. Read-only: sending, labels, and drafts are the web client's job.
"""

from __future__ import annotations

import base64
import html
from dataclasses import dataclass
from email.utils import parsedate_to_datetime

from googleapiclient.errors import HttpError

from ko.google_auth import GoogleError, get_gmail_service, raise_for_status


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


class GmailError(GoogleError):
    pass


def _handle(e: HttpError, context: str) -> None:
    raise_for_status(
        e,
        context,
        not_found=GmailError,
        permission=GmailError,
        hint="Did you `ko gmail auth` (grants gmail.readonly)?",
    )


_META_HEADERS = ["From", "To", "Subject", "Date"]


def _fmt_date(raw: str) -> str:
    """RFC-2822 Date header -> local 'YYYY-MM-DD HH:MM'; pass through if unparseable."""
    try:
        return parsedate_to_datetime(raw).astimezone().strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError):
        return raw


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
        snippet=html.unescape(m.get("snippet", "")),
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


def get_message(msg_id: str) -> tuple[GmailMessage, str]:
    """A single message: (metadata, plain-text body). Falls back to text/html if no plain part."""
    svc = get_gmail_service()
    try:
        m = svc.users().messages().get(userId="me", id=msg_id, format="full").execute(num_retries=3)
        payload = m.get("payload", {})
        body = _find_mime(payload, "text/plain") or _find_mime(payload, "text/html")
        return _message(m), body
    except HttpError as e:
        _handle(e, msg_id)
        raise  # unreachable: _handle always raises
