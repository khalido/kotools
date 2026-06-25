"""Offline tests for the shared Google-API helpers (errors, http mapping, id extraction)."""

from __future__ import annotations

import pytest

from ko import google_auth as g


class _Resp:
    def __init__(self, status):
        self.status = status


class _FakeHttpError(Exception):
    def __init__(self, status):
        self.resp = _Resp(status)


def test_id_from_url_both_kinds_and_bare():
    assert g.id_from_url("https://docs.google.com/spreadsheets/d/AB-1_2/edit#gid=0", "spreadsheets") == "AB-1_2"
    assert g.id_from_url("https://docs.google.com/document/d/XY_9/edit", "document") == "XY_9"
    assert g.id_from_url("  BARE_id  ", "spreadsheets") == "BARE_id"  # bare id, trimmed


def test_raise_for_status_maps_status():
    nf, pd = g.GoogleNotFound, g.GooglePermissionDenied
    with pytest.raises(pd):
        g.raise_for_status(_FakeHttpError(403), "x", not_found=nf, permission=pd, hint="h")
    with pytest.raises(nf):
        g.raise_for_status(_FakeHttpError(404), "x", not_found=nf, permission=pd, hint="h")
    with pytest.raises(_FakeHttpError):  # any other status re-raised unchanged
        g.raise_for_status(_FakeHttpError(500), "x", not_found=nf, permission=pd, hint="h")


def test_error_hierarchy():
    assert issubclass(g.GoogleNotFound, g.GoogleError)
    assert issubclass(g.GooglePermissionDenied, g.GoogleError)
    assert issubclass(g.GoogleError, RuntimeError)
