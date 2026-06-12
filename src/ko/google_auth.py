"""Google OAuth for a personal CLI.

First run opens a browser for consent; refresh token is cached locally. No
service accounts — this tool runs as *you*, against *your* Google account.

Scopes are per-API, not per-folder. Drive's read-only scope lets the tool see
anything your Google account can see. If that's too broad, use a service
account in a separate tool and share specific files with it.

Setup (one-off):
1. Create a Google Cloud project and enable the Sheets API + Drive API
2. APIs & Services → Credentials → Create Credentials → OAuth client ID → Desktop app
3. Download the JSON and save it to ~/.config/ko/google_client.json
   (or set KO_GOOGLE_CLIENT_FILE=<path>)
4. Run `ko gsheets auth` — browser opens, approve, done
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import Resource, build

from .dirs import config_dir, state_file


CONFIG_DIR = config_dir()
CLIENT_FILE = Path(
    os.environ.get("KO_GOOGLE_CLIENT_FILE") or (CONFIG_DIR / "google_client.json")
)
# token is state, not config — lives in ~/.local/state/ko (auto-migrated)
TOKEN_FILE = state_file("google_token.json")

SCOPES_READONLY = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]
SCOPES_READWRITE = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


class AuthError(RuntimeError):
    pass


def _scopes(readonly: bool) -> list[str]:
    return SCOPES_READONLY if readonly else SCOPES_READWRITE


def _load_cached(readonly: bool) -> Credentials | None:
    if not TOKEN_FILE.exists():
        return None
    try:
        creds = Credentials.from_authorized_user_file(
            str(TOKEN_FILE), _scopes(readonly)
        )
    except Exception:
        return None
    if creds.valid:
        return creds
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            TOKEN_FILE.write_text(creds.to_json())
            return creds
        except Exception:
            return None
    return None


def _run_flow(readonly: bool) -> Credentials:
    if not CLIENT_FILE.exists():
        raise AuthError(
            f"Google OAuth client file not found at {CLIENT_FILE}.\n"
            f"Create an OAuth 2.0 Desktop client at "
            f"https://console.cloud.google.com/apis/credentials and save the JSON "
            f"there, or set KO_GOOGLE_CLIENT_FILE=<path>. See README for full setup."
        )
    flow = InstalledAppFlow.from_client_secrets_file(
        str(CLIENT_FILE), _scopes(readonly)
    )
    creds = flow.run_local_server(port=0)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(creds.to_json())
    return creds


@lru_cache
def get_credentials(readonly: bool = True) -> Credentials:
    creds = _load_cached(readonly)
    if creds is not None:
        return creds
    return _run_flow(readonly)


@lru_cache
def get_sheets_service(readonly: bool = True) -> Resource:
    return build(
        "sheets", "v4", credentials=get_credentials(readonly), cache_discovery=False
    )


@lru_cache
def get_drive_service(readonly: bool = True) -> Resource:
    return build(
        "drive", "v3", credentials=get_credentials(readonly), cache_discovery=False
    )


def logout() -> bool:
    """Remove cached token. Returns True if a token was removed."""
    if TOKEN_FILE.exists():
        TOKEN_FILE.unlink()
        get_credentials.cache_clear()
        get_sheets_service.cache_clear()
        get_drive_service.cache_clear()
        return True
    return False
