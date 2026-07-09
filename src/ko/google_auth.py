"""Google OAuth for a personal CLI — multi-account.

First run opens a browser for consent; refresh tokens cache locally, one file per
account. No service accounts — this tool runs as *you*, against *your* Google account(s).

**Accounts.** Pick the active one with `--account`/`-a` (on any `ko gsheets` command),
the `KO_GOOGLE_ACCOUNT` env var, or `[google] account` in config.toml; the default is
`"default"`. Each account caches its own token. A per-account OAuth *client* JSON is
optional — you only need one when a single client can't authorize the account (e.g. a
Workspace "Internal" consent screen won't authorize a personal Gmail; give that account
its own `google_client_<account>.json`).

Scopes are per-API: `ko` requests Sheets + Docs + Calendar (read or read+write) + Gmail (read-only)
+ Drive `drive.file`. It deliberately does NOT request a broad Drive scope (`drive`/`drive.readonly`),
so it still can't browse your whole Drive — only the docs/sheets you address by ID. The narrow
`drive.file` scope grants access *only to files `ko` itself creates or that you open by ID*, which is
exactly what the Markdown↔Doc round-trip and comment-reading need (those are Drive-API features the
Docs API can't do) without widening the blast radius. One token per account covers them all. Adding a
scope means one re-consent (`ko gsheets auth` again). README.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import NoReturn

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import Resource, build
from googleapiclient.errors import HttpError

from . import config
from .dirs import config_dir, state_dir, state_file

# drive.file is a single read+write scope scoped to app-created/opened files only — it appears in
# BOTH lists because even "read-only" ko needs it to export markdown / read comments from docs it made.
SCOPES_READONLY = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/documents.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/drive.file",  # per-file Drive: only files ko creates/opens
]
SCOPES_READWRITE = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.readonly",  # list calendars + read events
    "https://www.googleapis.com/auth/gmail.readonly",  # Gmail is read-only in ko (no send/modify)
    "https://www.googleapis.com/auth/drive.file",  # per-file Drive: only files ko creates/opens
]

DEFAULT_ACCOUNT = "default"


class AuthError(RuntimeError):
    pass


def _write_token(tf: Path, creds: Credentials) -> None:
    """Persist a credentials JSON with owner-only perms — it holds a refresh token."""
    tf.parent.mkdir(parents=True, exist_ok=True)
    tf.write_text(creds.to_json())
    try:
        tf.chmod(0o600)
    except OSError:
        pass  # e.g. a filesystem without POSIX perms — best-effort


# --- shared API helpers (errors, HTTP handling, id extraction) ---
# gsheets / gdocs / gcal / gmail all hit Google APIs the same way: a 403/404 needs the same
# mapping + an auth hint, and several take a URL-or-bare-ID. That spine lives here so each
# domain module stays a thin wrapper. Modules subclass GoogleError so callers can catch a
# specific type (`except SheetsError`) or anything Google (`except GoogleError`).


class GoogleError(RuntimeError):
    """Base for every ko Google-API error — catch this to catch any."""


class GoogleNotFound(GoogleError):
    pass


class GooglePermissionDenied(GoogleError):
    pass


def raise_for_status(e: HttpError, context: str, *, not_found, permission, hint: str) -> NoReturn:
    """Map a Google HttpError to a domain error: 403 -> `permission` (+ `hint`), 404 -> `not_found`,
    anything else re-raised. `not_found`/`permission` are the calling module's error classes."""
    status = e.resp.status
    if status == 403:
        raise permission(f"Permission denied for {context}. {hint}") from e
    if status == 404:
        raise not_found(f"Not found: {context}") from e
    raise e


_ID_RE = {
    "spreadsheets": re.compile(r"/spreadsheets/d/([a-zA-Z0-9_-]+)"),
    "document": re.compile(r"/document/d/([a-zA-Z0-9_-]+)"),
    "folder": re.compile(r"/folders/([a-zA-Z0-9_-]+)"),
}


def id_from_url(value: str, kind: str) -> str:
    """A Google file ID from a URL (`/spreadsheets/d/<id>`, `/document/d/<id>`) or a bare ID."""
    m = _ID_RE[kind].search(value)
    return m.group(1) if m else value.strip()


def active_account() -> str:
    """The Google account to use: `--account` (via KO_GOOGLE_ACCOUNT) → config
    `[google] account` → 'default'."""
    return config.setting("KO_GOOGLE_ACCOUNT", "google", "account", DEFAULT_ACCOUNT)


def client_file(account: str | None = None) -> Path:
    """OAuth client JSON for an account. KO_GOOGLE_CLIENT_FILE wins; else a per-account
    `google_client_<account>.json` if it exists; else the shared `google_client.json`."""
    if override := os.environ.get("KO_GOOGLE_CLIENT_FILE"):
        return Path(override)
    account = account or active_account()
    if account != DEFAULT_ACCOUNT:
        specific = config_dir() / f"google_client_{account}.json"
        if specific.exists():
            return specific
    return config_dir() / "google_client.json"


def token_file(account: str | None = None) -> Path:
    """Cached-token path for an account (state, not config). 'default' keeps the legacy
    `google_token.json`; named accounts get `google_token_<account>.json`."""
    account = account or active_account()
    name = "google_token.json" if account == DEFAULT_ACCOUNT else f"google_token_{account}.json"
    return state_file(name)


def list_accounts() -> list[str]:
    """Account names that have a cached token, sorted."""
    out: list[str] = []
    for p in state_dir().glob("google_token*.json"):
        stem = p.stem
        if stem == "google_token":
            out.append(DEFAULT_ACCOUNT)
        elif stem.startswith("google_token_"):
            out.append(stem[len("google_token_") :])
    return sorted(out)


def _scopes(readonly: bool) -> list[str]:
    return SCOPES_READONLY if readonly else SCOPES_READWRITE


# Present only in the read+write set, so its presence in a token's granted scopes = "can write".
_WRITE_MARKER_SCOPE = "https://www.googleapis.com/auth/spreadsheets"


def _load_cached(readonly: bool, account: str) -> Credentials | None:
    tf = token_file(account)
    if not tf.exists():
        return None
    try:
        # Load with the token's OWN granted scopes (omit `scopes=`), NOT a readonly/readwrite-derived
        # list. Requesting a *narrower* set than was granted makes the refresh fail with "scope has
        # changed" and silently triggers a fresh browser auth — so one read+write token must be able
        # to serve read-only reads (a superset covers the subset).
        creds = Credentials.from_authorized_user_file(str(tf))
    except Exception:
        return None
    # A write op can't run on a token granted only read-only scopes — force a re-auth to upgrade.
    if not readonly and _WRITE_MARKER_SCOPE not in (creds.scopes or []):
        return None
    if creds.valid:
        return creds
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _write_token(tf, creds)
            return creds
        except Exception:
            return None
    return None


def _run_flow(readonly: bool, account: str) -> Credentials:
    cf = client_file(account)
    if not cf.exists():
        want = "google_client.json" if account == DEFAULT_ACCOUNT else (
            f"google_client_{account}.json (or the shared google_client.json)"
        )
        raise AuthError(
            f"Google OAuth client file not found for account {account!r} (looked at {cf}).\n"
            f"Create an OAuth 2.0 Desktop client at "
            f"https://console.cloud.google.com/apis/credentials and save the JSON to "
            f"~/.config/ko/{want}, or set KO_GOOGLE_CLIENT_FILE=<path>. See README for full setup."
        )
    flow = InstalledAppFlow.from_client_secrets_file(str(cf), _scopes(readonly))
    creds = flow.run_local_server(port=0)
    _write_token(token_file(account), creds)
    return creds


@lru_cache
def get_credentials(readonly: bool = True, account: str | None = None) -> Credentials:
    account = account or active_account()
    creds = _load_cached(readonly, account)
    if creds is not None:
        return creds
    return _run_flow(readonly, account)


@lru_cache
def get_sheets_service(readonly: bool = True, account: str | None = None) -> Resource:
    return build(
        "sheets", "v4", credentials=get_credentials(readonly, account), cache_discovery=False
    )


@lru_cache
def get_docs_service(readonly: bool = True, account: str | None = None) -> Resource:
    return build(
        "docs", "v1", credentials=get_credentials(readonly, account), cache_discovery=False
    )


@lru_cache
def get_calendar_service(readonly: bool = True, account: str | None = None) -> Resource:
    return build(
        "calendar", "v3", credentials=get_credentials(readonly, account), cache_discovery=False
    )


@lru_cache
def get_gmail_service(readonly: bool = True, account: str | None = None) -> Resource:
    return build(
        "gmail", "v1", credentials=get_credentials(readonly, account), cache_discovery=False
    )


@lru_cache
def get_drive_service(readonly: bool = True, account: str | None = None) -> Resource:
    # drive.file is the same scope read or write, so `readonly` only steers which cached token
    # we reuse — it doesn't narrow Drive access (the scope itself is already per-file).
    return build(
        "drive", "v3", credentials=get_credentials(readonly, account), cache_discovery=False
    )


def logout(account: str | None = None) -> bool:
    """Remove an account's cached token (default: the active account). Returns True if removed."""
    tf = token_file(account)
    if tf.exists():
        tf.unlink()
        get_credentials.cache_clear()
        get_sheets_service.cache_clear()
        get_docs_service.cache_clear()
        get_calendar_service.cache_clear()
        get_gmail_service.cache_clear()
        get_drive_service.cache_clear()
        return True
    return False
