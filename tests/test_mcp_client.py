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


def test_decode_jwt_claims():
    # payload {"aud":"wrong-aud","iss":"me","scope":"read","exp":123}
    token = "eyJhbGciOiJIUzI1NiJ9.eyJhdWQiOiJ3cm9uZy1hdWQiLCJpc3MiOiJtZSIsInNjb3BlIjoicmVhZCIsImV4cCI6MTIzfQ.x"
    claims = mcp_client.decode_jwt_claims(token)
    assert claims["aud"] == "wrong-aud" and claims["scope"] == "read" and claims["exp"] == 123
    assert mcp_client.decode_jwt_claims("not-a-jwt") is None  # not 3 parts


def test_resource_metadata_url():
    www = 'Bearer realm="everx-mcp", resource_metadata="http://localhost:5180/.well-known/oauth-protected-resource/mcp"'
    assert mcp_client._resource_metadata_url(www) == "http://localhost:5180/.well-known/oauth-protected-resource/mcp"
    assert mcp_client._resource_metadata_url("Bearer realm=x") is None
    assert mcp_client._resource_metadata_url(None) is None


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


def test_probe_replaces_html_body_with_terse_note(monkeypatch):
    """_probe should NOT quote raw HTML into the error — it's useless noise.
    Instead it emits a terse '(HTML page, not an MCP endpoint?)' hint."""
    import httpx2 as httpx

    html_body = "<!DOCTYPE html><html><head><title>App</title></head><body><h1>Not found</h1></body></html>"

    class FakeResponse:
        status_code = 404
        text = html_body
        headers = {"content-type": "text/html; charset=utf-8"}

        def get(self, key, default=""):
            return self.headers.get(key, default)

    monkeypatch.setattr(httpx, "post", lambda *a, **kw: FakeResponse())
    result = mcp_client._probe("http://example.com/mcp", {})
    assert "HTML page" in result
    assert "DOCTYPE" not in result  # raw HTML must not appear
    assert "404" in result  # status code is preserved


def test_probe_keeps_json_error_snippet(monkeypatch):
    """Non-HTML error bodies (JSON payloads) should keep the current 300-char quote."""
    import httpx2 as httpx

    json_body = '{"error": "endpoint not configured", "code": "not_found"}'

    class FakeResponse:
        status_code = 503
        text = json_body
        headers = {"content-type": "application/json"}

        def get(self, key, default=""):
            return self.headers.get(key, default)

    monkeypatch.setattr(httpx, "post", lambda *a, **kw: FakeResponse())
    result = mcp_client._probe("http://example.com/mcp", {})
    assert "endpoint not configured" in result  # JSON snippet preserved
    assert "503" in result


class _Reply:
    def __init__(self, status: int, text: str):
        self.status_code, self.text, self.headers = status, text, {"content-type": "application/json"}


def _serve(monkeypatch, replies: dict[str, _Reply]) -> list[dict]:
    """httpx.post answering by JSON-RPC method; returns the list of calls it saw."""
    import httpx2 as httpx

    seen: list[dict] = []

    def post(url, json, headers, timeout):
        seen.append({"method": json["method"], "headers": headers})
        return replies[json["method"]]

    monkeypatch.setattr(httpx, "post", post)
    return seen


def test_probe_stops_at_a_modern_server(monkeypatch):
    found = '{"result": {"supportedVersions": ["2026-07-28"]}}'
    seen = _serve(monkeypatch, {"server/discover": _Reply(200, found)})
    assert "supportedVersions" in mcp_client._probe("http://x/mcp", {})
    assert [c["method"] for c in seen] == ["server/discover"]
    assert seen[0]["headers"]["Mcp-Method"] == "server/discover"
    assert seen[0]["headers"]["MCP-Protocol-Version"] == mcp_client._MODERN


def test_probe_falls_back_to_initialize_on_an_older_server(monkeypatch):
    seen = _serve(
        monkeypatch,
        {
            "server/discover": _Reply(200, '{"error": {"code": -32601, "message": "not found"}}'),
            "initialize": _Reply(200, '{"result": {"protocolVersion": "2025-11-25"}}'),
        },
    )
    assert "2025-11-25" in mcp_client._probe("http://x/mcp", {})
    assert [c["method"] for c in seen] == ["server/discover", "initialize"]
    assert "Mcp-Method" not in seen[1]["headers"]


def test_probe_reports_a_refusal_without_retrying(monkeypatch):
    seen = _serve(monkeypatch, {"server/discover": _Reply(401, '{"error": "login first"}')})
    assert "HTTP 401" in mcp_client._probe("http://x/mcp", {})
    assert len(seen) == 1


def test_probe_versions_match_the_installed_sdk():
    """The probe hard-codes these so ko needn't import the SDK; this says when they go stale."""
    version = pytest.importorskip("mcp_types.version")
    assert mcp_client._MODERN == version.LATEST_MODERN_VERSION
    assert mcp_client._HANDSHAKE == version.LATEST_HANDSHAKE_VERSION


def test_load_and_resolve_by_name_with_header_override(monkeypatch, tmp_path):
    monkeypatch.setenv("KO_CONFIG_DIR", str(tmp_path))
    (tmp_path / "mcp.json").write_text(
        '{"mcpServers": {"dev": {"url": "http://x/mcp", "headers": {"A": "1", "B": "2"}}}}'
    )
    assert set(mcp_client.load_servers()) == {"dev"}
    spec = mcp_client.resolve("dev", {"B": "override"})  # -H overrides config
    assert spec["url"] == "http://x/mcp" and spec["headers"] == {"A": "1", "B": "override"}


def test_explain_blames_server_only_when_it_failed(monkeypatch):
    """A probe that comes back 2xx means the server is fine — show our own error, not 'Server said'."""
    spec = {"transport": "http", "url": "https://x.test/mcp", "headers": {}}
    monkeypatch.setattr(mcp_client, "_probe", lambda url, headers: "HTTP 200: {...}")
    assert mcp_client._explain(spec, ValueError("Unknown model: basic")) == "ValueError: Unknown model: basic"
    monkeypatch.setattr(mcp_client, "_probe", lambda url, headers: "HTTP 401: denied")
    assert mcp_client._explain(spec, RuntimeError("vague")) == "RuntimeError. Server said: HTTP 401: denied"


def test_explain_stdio_shows_stderr_tail():
    spec = {"transport": "stdio", "command": "x"}
    msg = mcp_client._explain(spec, RuntimeError("Connection closed"), "a\nb\nnot found in registry\n")
    assert msg.startswith("RuntimeError: Connection closed") and "server stderr:" in msg
    assert msg.endswith("a | b | not found in registry")
    assert mcp_client._explain(spec, RuntimeError("boom")) == "RuntimeError: boom"


def test_as_text_keeps_plain_output_plain():
    assert mcp_client._as_text("hi") == "hi"
    assert mcp_client._as_text(["line a", "line b"]) == "line a\nline b"  # not a JSON array
    assert mcp_client._as_text({"a": 1}) == '{\n  "a": 1\n}'

    class BinaryImage:
        media_type = "image/png"

    assert mcp_client._as_text(BinaryImage()) == "[BinaryImage image/png]"
    assert mcp_client._as_text(["x", BinaryImage()]) == '[\n  "x",\n  "[BinaryImage image/png]"\n]'
