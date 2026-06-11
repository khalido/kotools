"""Smoke tests for ko.gsheets. Live tests skip unless a cached OAuth token exists."""

from __future__ import annotations

import pytest

from ko import google_auth
from ko import gsheets


def test_error_types_exist():
    # these are imported/referenced by callers; verify the public surface
    assert issubclass(gsheets.SheetsNotFound, gsheets.SheetsError)
    assert issubclass(gsheets.SheetsPermissionDenied, gsheets.SheetsError)


@pytest.mark.skipif(
    not google_auth.TOKEN_FILE.exists(),
    reason="no cached google token; run `ko gsheets auth` first for live tests",
)
def test_get_info_on_public_sheet():
    # Google's public sample sheet — the SDK uses this in its tutorials
    SAMPLE_ID = "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms"
    info = gsheets.get_info(SAMPLE_ID)
    assert info.id == SAMPLE_ID
    assert info.title
    assert isinstance(info.tabs, list)
