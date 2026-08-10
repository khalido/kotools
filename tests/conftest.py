"""Shared test safety nets.

The suite is offline by default; live tests opt in explicitly (and skip without a key).
The one failure mode that isn't a plain error is Google OAuth: `InstalledAppFlow.
run_local_server()` opens a browser and then blocks on a localhost socket **forever**, so a
test that accidentally reaches real auth doesn't fail — it hangs the whole run with no clue
which test did it. This turns that into an ordinary, named failure.
"""

from __future__ import annotations

import os

import pytest

from ko import google_auth


@pytest.fixture(autouse=True)
def _no_real_oauth(monkeypatch):
    """Any test reaching the interactive OAuth flow fails loudly instead of hanging."""

    def _boom(readonly: bool, account: str):
        raise AssertionError(
            f"test tried to run the interactive Google OAuth flow (account={account!r}). "
            "Mock the service/credentials, or set KO_STATE_DIR to a tmp_path."
        )

    monkeypatch.setattr(google_auth, "_run_flow", _boom)


@pytest.fixture(autouse=True)
def _clean_google_caches():
    """Credential/service singletons are module-level lru_caches, so one test's mock (or a
    cache_clear from an auth test) leaks into the next. Clear on both sides of every test."""
    google_auth._clear_service_caches()
    yield
    google_auth._clear_service_caches()


@pytest.fixture(autouse=True)
def _isolate_active_google_account():
    """`ko gsheets -a <name>` sets KO_GOOGLE_ACCOUNT in os.environ directly (right for a
    one-shot CLI, leaky in-process): without this, one CliRunner invoke silently re-points
    every later test — including live ones — at that account."""
    before = os.environ.get("KO_GOOGLE_ACCOUNT")
    yield
    if before is None:
        os.environ.pop("KO_GOOGLE_ACCOUNT", None)
    else:
        os.environ["KO_GOOGLE_ACCOUNT"] = before
