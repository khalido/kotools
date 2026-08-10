"""Google OAuth for a personal CLI — multi-account.

First run opens a browser for consent; refresh tokens cache locally, one file per
account. This tool normally runs as *you*, against *your* Google account(s); a
**service-account profile** is the deliberate exception, for automation and docs your
own accounts can't reach: register a key with `ko gsheets -a <name> auth
--service-account <key.json>` and that account authenticates as the SA (no browser, no
cached token — the key is the credential). Share the doc/sheet with the SA's
client_email and drive it with `-a <name>`.

**Accounts.** Pick the active one with `--account`/`-a` (on any `ko gsheets` command),
the `KO_GOOGLE_ACCOUNT` env var, or `[google] account` in config.toml; the default is
`"default"`. Each account caches its own token. A per-account OAuth *client* JSON is
optional — you only need one when a single client can't authorize the account (e.g. a
Workspace "Internal" consent screen won't authorize a personal Gmail; give that account
its own `google_client_<account>.json`).

Scopes are per-API: `ko` requests Sheets + Docs + Calendar (read or read+write) + Gmail (read
+ `gmail.compose` for drafts, no `gmail.modify`) + Drive `drive.file`. It deliberately does NOT request a broad Drive scope (`drive`/`drive.readonly`),
so it still can't browse your whole Drive — only the docs/sheets you address by ID. The narrow
`drive.file` scope grants access *only to files `ko` itself creates or that you open by ID*, which is
exactly what the Markdown↔Doc round-trip and comment-reading need (those are Drive-API features the
Docs API can't do) without widening the blast radius. One token per account covers them all. Adding a
scope means one re-consent (`ko gsheets auth` again). README.
"""

from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import NoReturn

from google.auth.transport.requests import Request
from google.oauth2 import service_account
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
    "https://www.googleapis.com/auth/gmail.readonly",  # read Gmail (compose alone can't read)
    "https://www.googleapis.com/auth/gmail.compose",  # create/update/send drafts (NOT gmail.modify)
    "https://www.googleapis.com/auth/drive.file",  # per-file Drive: only files ko creates/opens
]

DEFAULT_ACCOUNT = "default"


class AuthError(RuntimeError):
    pass


def _write_secret(path: Path, text: str) -> None:
    """Write a credential file that is owner-only FROM CREATION. A write-then-chmod leaves it
    world-readable under a default umask for the length of the write; os.open with mode 0o600
    closes that window. Falls back to plain write where POSIX perms don't apply."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    except (OSError, AttributeError):
        path.write_text(text)
        return
    with os.fdopen(fd, "w") as fh:
        fh.write(text)


def _write_token(tf: Path, creds: Credentials) -> None:
    """Persist a credentials JSON with owner-only perms — it holds a refresh token."""
    _write_secret(tf, creds.to_json())


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


def sa_file(account: str | None = None) -> Path:
    """Service-account key path for an account — its presence makes the account an SA profile.

    STATE, not config: config is the dotfile-synced dir, and unlike `google_client.json` (a
    Desktop OAuth client, useless without a human consent flow) an SA key is a standalone
    bearer credential — it authenticates by itself on any machine. That's the same class as
    `google_token_<name>.json`, which already lives here. Still portable on purpose: copy the
    file or share a `KO_STATE_DIR`, like the OAuth token."""
    return state_file(f"google_sa_{account or active_account()}.json")


def _clear_service_caches() -> None:
    for fn in (
        get_credentials, get_sheets_service, get_docs_service,
        get_calendar_service, get_gmail_service, get_drive_service,
    ):
        fn.cache_clear()


def register_service_account(key_path: Path, account: str | None = None) -> str:
    """Copy a service-account key JSON in as the account's `google_sa_<name>.json` (owner-only)
    and return its client_email — the identity to share docs/sheets with. Never returns or logs
    key material."""
    try:
        info = json.loads(Path(key_path).read_text())
    except (OSError, ValueError) as e:
        raise AuthError(f"can't read service-account key {key_path}: {e}") from e
    if info.get("type") != "service_account" or not info.get("client_email"):
        raise AuthError(
            f"{key_path} is not a service-account key JSON "
            "(want type='service_account' with a client_email) — download one from the "
            "service account's Keys tab in the Cloud console."
        )
    _write_secret(sa_file(account), json.dumps(info))
    _clear_service_caches()
    return info["client_email"]


def list_accounts() -> list[str]:
    """Account names that have a cached token or a registered service-account key, sorted."""
    out: list[str] = []
    for p in state_dir().glob("google_token*.json"):
        stem = p.stem
        if stem == "google_token":
            out.append(DEFAULT_ACCOUNT)
        elif stem.startswith("google_token_"):
            out.append(stem[len("google_token_") :])
    for p in state_dir().glob("google_sa_*.json"):
        out.append(p.stem[len("google_sa_") :])
    return sorted(set(out))


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
def get_credentials(
    readonly: bool = True, account: str | None = None
) -> Credentials | service_account.Credentials:
    account = account or active_account()
    sa = sa_file(account)
    if sa.exists():
        # An SA profile: the key is the credential — no browser flow, no token cache. Same
        # scope sets as OAuth; what it can reach is whatever's shared with its client_email.
        return service_account.Credentials.from_service_account_file(
            str(sa), scopes=_scopes(readonly)
        )
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
    """De-authenticate an account (default: the active one): remove its cached OAuth token AND
    any registered service-account key — leaving the key behind would report "signed out" on an
    account that still authenticates. Returns True if anything was removed."""
    removed = False
    for path in (token_file(account), sa_file(account)):
        if path.exists():
            path.unlink()
            removed = True
    if removed:
        _clear_service_caches()
    return removed
