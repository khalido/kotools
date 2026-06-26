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


def test_normalize_http_and_stdio():
    http = mcp_client._normalize("a", {"url": "http://x/mcp", "headers": {"H": "v"}})
    assert http == {"transport": "http", "name": "a", "url": "http://x/mcp", "headers": {"H": "v"}}
    stdio = mcp_client._normalize("b", {"command": "npx", "args": ["-y", "srv"]})
    assert stdio["transport"] == "stdio" and stdio["command"] == "npx" and stdio["args"] == ["-y", "srv"]
    with pytest.raises(mcp_client.MCPTestError):
        mcp_client._normalize("c", {})  # neither url nor command


def test_resolve_url_passthrough_and_unknown(monkeypatch, tmp_path):
    monkeypatch.setenv("KO_CONFIG_DIR", str(tmp_path))  # no mcp.json -> empty registry
    spec = mcp_client.resolve("http://localhost:5180/mcp", {"Authorization": "Bearer x"})
    assert spec["transport"] == "http" and spec["headers"] == {"Authorization": "Bearer x"}
    with pytest.raises(mcp_client.MCPTestError, match="unknown server"):
        mcp_client.resolve("not-a-url-or-name")


def test_load_servers_expands_env(monkeypatch, tmp_path):
    monkeypatch.setenv("KO_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("MY_TOKEN", "secret123")
    (tmp_path / "mcp.json").write_text(
        '{"mcpServers": {"s": {"url": "http://x/mcp", "headers": {"Authorization": "Bearer ${MY_TOKEN}"}}}}'
    )
    servers = mcp_client.load_servers()
    assert servers["s"]["headers"]["Authorization"] == "Bearer secret123"  # secret never in the file


def test_load_and_resolve_by_name_with_header_override(monkeypatch, tmp_path):
    monkeypatch.setenv("KO_CONFIG_DIR", str(tmp_path))
    (tmp_path / "mcp.json").write_text(
        '{"mcpServers": {"dev": {"url": "http://x/mcp", "headers": {"A": "1", "B": "2"}}}}'
    )
    assert set(mcp_client.load_servers()) == {"dev"}
    spec = mcp_client.resolve("dev", {"B": "override"})  # -H overrides config
    assert spec["url"] == "http://x/mcp" and spec["headers"] == {"A": "1", "B": "override"}
