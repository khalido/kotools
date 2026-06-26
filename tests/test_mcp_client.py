"""Offline tests for ko.mcp_client pure helpers (no live server)."""

from __future__ import annotations

import pytest

from ko import mcp_client


def test_parse_headers_ok_and_bad():
    assert mcp_client.parse_headers(["Authorization: Bearer x", "X-Foo:bar"]) == {
        "Authorization": "Bearer x",
        "X-Foo": "bar",  # tolerates missing space after colon
    }
    assert mcp_client.parse_headers(None) == {}
    with pytest.raises(mcp_client.MCPTestError):
        mcp_client.parse_headers(["no-colon-here"])


def test_unwrap_peels_exception_groups():
    leaf = ValueError("boom")
    grp = ExceptionGroup("outer", [ExceptionGroup("inner", [leaf])])
    assert mcp_client._unwrap(grp) is leaf
    assert mcp_client._unwrap(leaf) is leaf  # non-group passes through


def test_error_type():
    assert issubclass(mcp_client.MCPTestError, RuntimeError)
