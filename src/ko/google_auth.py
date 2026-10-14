"""Google OAuth for a personal CLI — multi-account.

One way in: `ko google auth <name>` signs an account in (browser consent); refresh tokens
cache locally, one file per account. The name is a short tag you pick — `home`, `sfl`, `ne` —
and `-a <name>` on any Google command uses it. A **service-account profile** is the
deliberate exception, for automation and docs your own accounts can't reach: `ko google auth
<name> --service-account <key.json>` makes that name authenticate as the SA (no browser, no
cached token — the key is the credential). Share the doc/sheet with the SA's client_email.

**Active account:** `--account`/`-a` → `KO_GOOGLE_ACCOUNT` → `ko google use <name>` (stored in
state/settings.json, per machine) → `[google] account` in config.toml → `"default"`. A name
with no token is an error listing the signed-in ones — a typo never opens a browser to sign
up a new account. A per-account OAuth *client* JSON is optional — you only need one when a
single client can't authorize the account (e.g. a Workspace "Internal" consent screen won't
authorize a personal Gmail; give that account its own `google_client_<name>.json`).

Scopes are per-API: `ko` requests Sheets + Docs + Calendar (read or read+write) + Gmail (read
+ `gmail.compose` for drafts, no `gmail.modify`) + YouTube (read-only) + Drive (`drive.readonly` on the read token,
`drive` on the write token — widened from the per-file `drive.file` on 2026-09-10 so `ko drive`
can mirror folders ko didn't create; WORKLOG has the trade). Per-folder scoping is a
service-account profile: share the folder with its client_email, run with `-a <name>`. One token
per account covers them all. Adding a scope means one re-consent (`ko google auth` again). README.
"""

from __future__ import annotations

import json
import os
import re
import socket
import ssl
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import NoReturn

import httplib2
from google.auth.exceptions import TransportError
from google.auth.transport.requests import Request
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import Resource, build
from googleapiclient.errors import HttpError

from . import config
from .dirs import config_dir, state_dir, state_file

# Drive is the broad scope: `drive` is a Google "restricted" scope, fine for a Testing or
# Workspace-Internal app; publishing an External app with it triggers Google's verification.
SCOPES_READONLY = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/documents.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/drive.readonly",  # list/read any Drive file (ko drive ls/pull)
    "https://www.googleapis.com/auth/youtube.readonly",  # your own subscriptions/playlists (public data uses YOUTUBE_API_KEY)
]
SCOPES_READWRITE = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.readonly",  # list calendars + read events
    "https://www.googleapis.com/auth/gmail.readonly",  # read Gmail (compose alone can't read)
    "https://www.googleapis.com/auth/gmail.compose",  # create/update/send drafts (NOT gmail.modify)
    "https://www.googleapis.com/auth/drive",  # full Drive (ko drive push, gdocs push into any folder)
    "https://www.googleapis.com/auth/youtube.readonly",  # read-only on both tokens: ko never writes to YouTube
]

DEFAULT_ACCOUNT = "default"

# What a flaky network raises through httplib2 — an SSL drop, reset, timeout, DNS miss. Named so
# callers catch these and not every OSError (a full disk during a download isn't a network blip).
# httplib2 wraps a DNS miss as ServerNotFoundError; google-auth wraps a failed refresh as TransportError.
NETWORK_ERRORS = (
    ssl.SSLError, ConnectionError, TimeoutError, socket.gaierror, httplib2.ServerNotFoundError, TransportError,
)


class AuthError(RuntimeError):
    """No usable Google credential. `fix` is the command that resolves it, when there is one —
    `--json` errors carry it as a field so an agent needn't parse it out of the prose."""

    def __init__(self, msg: str, fix: str | None = None):
        super().__init__(f"{msg} — run `{fix}`" if fix else msg)
        self.msg, self.fix = msg, fix


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
    """Persist a credentials JSON with owner-only perms — it holds a refresh token. Keeps the
    `email` ko stashed alongside (google-auth ignores keys it doesn't know)."""
    data = json.loads(creds.to_json())
    try:
        data["email"] = json.loads(tf.read_text()).get("email")
    except (OSError, ValueError):
        pass
    _write_secret(tf, json.dumps(data))


# --- who is signed in: per-account status (the `accounts` listing + `ko doctor`) ---

# api → (read scope, write scope), by suffix; what the token can do is derived from these.
_API_SCOPES = {
    "sheets": ("spreadsheets.readonly", "spreadsheets"),
    "docs": ("documents.readonly", "documents"),
    "calendar": ("calendar.readonly", "calendar.events"),
    "gmail": ("gmail.readonly", "gmail.compose"),
    "drive": ("drive.readonly", "drive"),
    "youtube": ("youtube.readonly", "youtube"),  # write scope never requested: always reads as read-only
}


def describe_scopes(scopes: list[str]) -> str:
    """A granted-scope list → one human line: what's read+write, what's read-only, what's
    missing or stale (a pre-2026-09 `drive.file` token). Pure; unit-tested."""
    have = {s.rsplit("/", 1)[-1] for s in scopes}
    rw = [api for api, (_, w) in _API_SCOPES.items() if w in have]
    ro = [api for api, (r, w) in _API_SCOPES.items() if r in have and w not in have]
    missing = [api for api, (r, w) in _API_SCOPES.items() if r not in have and w not in have]
    parts = []
    if rw:
        parts.append("read+write: " + ", ".join("gmail (read + drafts)" if a == "gmail" else a for a in rw))
    if ro:
        parts.append("read-only: " + ", ".join(ro))
    if "drive.file" in have:
        missing = [m for m in missing if m != "drive"]
        parts.append("drive: per-file only (pre-2026-09 token)")
    if missing:
        parts.append("missing: " + ", ".join(missing))
    if not rw or "drive.file" in have:
        parts.append("re-consent with `ko google auth` before writes")
    elif missing:
        parts.append("re-consent with `ko google auth` to add them")
    return " · ".join(parts) or "no scopes"


@dataclass
class AccountStatus:
    name: str
    kind: str  # "oauth" | "service-account" | "none"
    email: str | None
    scopes: list[str]
    path: Path | None


def account_status(account: str, lookup: bool = True) -> AccountStatus:
    """What one account is signed in as — never opens a browser. The address comes from the token
    file; a token without one (pre-2026-09) is looked up via the API unless `lookup=False`."""
    sa = sa_file(account)
    if sa.exists():
        try:
            email = json.loads(sa.read_text()).get("client_email")
        except (OSError, ValueError):
            email = None
        return AccountStatus(account, "service-account", email, SCOPES_READWRITE, sa)
    tf = token_file(account)
    if not tf.exists():
        return AccountStatus(account, "none", None, [], None)
    try:
        data = json.loads(tf.read_text())
    except (OSError, ValueError):
        data = {}
    email = data.get("email") or (account_email(account) if lookup else None)
    return AccountStatus(account, "oauth", email, data.get("scopes") or [], tf)


def _lookup_email(creds) -> str | None:
    """Ask Drive (any drive scope) who the token belongs to, else Gmail; None if neither can."""
    try:
        svc = build("drive", "v3", credentials=creds, cache_discovery=False)
        return svc.about().get(fields="user(emailAddress)").execute()["user"]["emailAddress"]
    except Exception:
        pass
    try:
        svc = build("gmail", "v1", credentials=creds, cache_discovery=False)
        return svc.users().getProfile(userId="me").execute()["emailAddress"]
    except Exception:
        return None


def account_email(account: str) -> str | None:
    """The Google address behind an OAuth token, learned once from the API and stashed in the
    token file (Google's token JSON doesn't carry it). Uses the cached token only — a missing
    or stale token returns None rather than starting a consent flow."""
    tf = token_file(account)
    try:
        data = json.loads(tf.read_text())
    except (OSError, ValueError):
        return None
    if data.get("email"):
        return data["email"]
    try:
        creds = _load_cached(readonly=True, account=account)
    except NETWORK_ERRORS:
        return None  # offline: the address is a nicety, not worth failing a listing over
    if creds is None:
        return None
    email = _lookup_email(creds)
    if email:
        data = json.loads(tf.read_text())  # re-read: the refresh above may have rewritten it
        data["email"] = email
        _write_secret(tf, json.dumps(data))
    return email


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
    """The Google account to use: `--account` (via KO_GOOGLE_ACCOUNT) → `ko google use`
    (settings.json) → config `[google] account` → 'default'."""
    # the name becomes part of a token filename — `-a ../x` must not reach the filesystem
    return check_name(config.setting("KO_GOOGLE_ACCOUNT", "google", "account", DEFAULT_ACCOUNT))


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
    get_credentials.cache_clear()
    service.cache_clear()


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


_NAME_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")


def check_name(name: str) -> str:
    """Account names are short tags that become filenames: lowercase letters, digits, - and _."""
    if not _NAME_RE.fullmatch(name):
        raise AuthError(
            f"bad account name {name!r} — use a short tag like `home` or `sfl` "
            "(lowercase letters, digits, - and _)"
        )
    return name


def use_account(name: str) -> None:
    """Make `name` the default account on this machine (state/settings.json)."""
    if name not in list_accounts():
        raise unknown_account(name)
    config.local_set("google", "account", name)


def rename_account(old: str, new: str) -> None:
    """Rename an account's tag: moves its token, service-account key and per-account OAuth
    client, and makes `new` the default here if `old` was. Refuses to overwrite `new`."""
    check_name(new)
    if old not in list_accounts():
        raise unknown_account(old)
    if new in list_accounts():
        raise AuthError(f"account {new!r} already exists", fix=f"ko google logout {new}")
    moves = [(token_file(old), token_file(new)), (sa_file(old), sa_file(new))]
    if old != DEFAULT_ACCOUNT:  # 'default' has no per-account client; the shared one stays put
        moves.append((config_dir() / f"google_client_{old}.json", config_dir() / f"google_client_{new}.json"))
    for src, dst in moves:
        if src.exists():
            src.rename(dst)
    # follow along wherever `old` was the default here: settings.json's google + cal targets, or
    # config.toml's default when settings.json doesn't override it
    saved = config.local_get("google", "account")
    if saved == old or (saved is None and config.get("google", "account") == old):
        config.local_set("google", "account", new)
    if config.local_get("cal", "account") == old:
        config.local_set("cal", "account", new)
    _clear_service_caches()


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
_DRIVE_RW = "https://www.googleapis.com/auth/drive"
_DRIVE_RO = "https://www.googleapis.com/auth/drive.readonly"


def _has_drive_scope(granted: list[str], readonly: bool) -> bool:
    """Does a token's granted set cover the Drive access we now request? A pre-2026-09 token
    carries only `drive.file`, which is silently 403 on any listing — so it must re-consent."""
    return _DRIVE_RW in granted or (readonly and _DRIVE_RO in granted)


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
    granted = creds.scopes or []
    if not readonly and _WRITE_MARKER_SCOPE not in granted:
        return None
    if not _has_drive_scope(granted, readonly):
        return None  # old drive.file-only token — one re-consent upgrades it
    if creds.valid:
        return creds
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except TransportError:
            raise  # offline — not a dead token; never answer a network blip with a browser
        except Exception:
            return None
        _write_token(tf, creds)
        return creds
    return None


def _has_tty() -> bool:
    """Can this process talk to a human? /dev/tty, not stdin — a human piping markdown into
    `gdocs push -` still has a terminal; an agent's shell has none (ENXIO)."""
    try:
        with open("/dev/tty"):
            return True
    except OSError:
        return False


def _consent_reason(readonly: bool, account: str) -> str:
    """Why the cached token won't do — the one line a user (or agent) needs to act on."""
    tf = token_file(account)
    if not tf.exists():
        return f"'{account}' isn't signed in"
    try:
        scopes = json.loads(tf.read_text()).get("scopes") or []
    except (OSError, ValueError):
        return f"'{account}' has an unreadable token"
    if not readonly and _WRITE_MARKER_SCOPE not in scopes:
        return f"'{account}' has a read-only grant and this is a write"
    if not _has_drive_scope(scopes, readonly):
        return f"'{account}' has a pre-2026-09 token (per-file Drive only)"
    return f"'{account}' has an expired token that couldn't refresh"


def _auth_cmd(account: str) -> str:
    return f"ko google auth {account}"


def _run_flow(readonly: bool, account: str, *, explicit: bool = False) -> Credentials:
    """Browser consent. Reached implicitly (a command found no usable token) it refuses without
    a terminal — an agent would otherwise hang on a callback nobody will click — and says why
    on stderr before opening the browser when there is one. `explicit` is the `auth` command:
    the user asked, so it always runs."""
    if not explicit:
        reason = _consent_reason(readonly, account)
        if not _has_tty():
            raise AuthError(f"{reason} — re-consent needs a browser (in a terminal)", fix=_auth_cmd(account))
        grant = "read-only" if readonly else "read+write"
        print(f"{reason} — opening a browser to re-consent ({grant})…", file=sys.stderr)
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
    account_email(account)  # learn + stash the address while the token is fresh
    return creds


def authenticate(readonly: bool = True, account: str | None = None) -> Credentials:
    """The explicit `auth` command: a sufficient cached token is reused, else the consent flow
    runs — terminal or not, the user asked for it."""
    account = account or active_account()
    creds = _load_cached(readonly, account)
    if creds is None or not set(_scopes(readonly)) <= set(creds.scopes or []):
        creds = _run_flow(readonly, account, explicit=True)  # missing any scope ko asks for: re-consent
    _clear_service_caches()
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
    if not token_file(account).exists():
        raise unknown_account(account)
    return _run_flow(readonly, account)


def unknown_account(account: str) -> AuthError:
    """A name with no token or key: say who IS signed in rather than opening a browser — a
    typo'd `-a` must never quietly start signing up a new account."""
    known = list_accounts()
    if not known:
        return AuthError("no Google account is signed in yet", fix="ko google auth <tag>")
    return AuthError(
        f"no Google account named {account!r} (signed in: {', '.join(known)})",
        fix=f"ko google auth {account}",
    )


# name → (discovery API, version) for every Google API ko speaks
_APIS = {
    "sheets": ("sheets", "v4"),
    "docs": ("docs", "v1"),
    "calendar": ("calendar", "v3"),
    "gmail": ("gmail", "v1"),
    "drive": ("drive", "v3"),
}


@lru_cache
def service(api: str, readonly: bool = True, account: str | None = None) -> Resource:
    """A Google API client for `api` (a key of `_APIS`) on an account — cached per args."""
    name, version = _APIS[api]
    return build(name, version, credentials=get_credentials(readonly, account), cache_discovery=False)


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
        name = account or active_account()
        for section in ("google", "cal"):
            if config.local_get(section, "account") == name:
                config.local_set(section, "account", None)
                if section == "cal":
                    config.local_set("cal", "calendar", None)
    return removed
