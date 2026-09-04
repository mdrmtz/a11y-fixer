from __future__ import annotations

import pytest

from a11y_fixer.adapters.mcp_clients import MCP_SERVERS, build_client


def test_default_client_includes_every_known_server() -> None:
    client = build_client()
    assert set(client.connections.keys()) == set(MCP_SERVERS.keys())


def test_scoped_client_includes_only_requested_servers() -> None:
    client = build_client(["wcag", "angular-cli"])
    assert set(client.connections.keys()) == {"wcag", "angular-cli"}


def test_unknown_server_name_raises() -> None:
    with pytest.raises(ValueError, match="Unknown MCP server"):
        build_client(["not-a-real-server"])


def test_known_servers_match_vscode_mcp_json() -> None:
    # Keep in lockstep with cmu-capstone/.vscode/mcp.json's agent-relevant servers.
    assert set(MCP_SERVERS) == {
        "angular-cli",
        "chrome-devtools",
        "playwright",
        "wcag",
        "docs-langchain",
        "reference-langchain",
    }


def test_stdio_servers_use_npx() -> None:
    for name in ("angular-cli", "chrome-devtools", "playwright"):
        assert MCP_SERVERS[name]["transport"] == "stdio"
        assert MCP_SERVERS[name]["command"] == "npx"


def test_http_servers_use_streamable_http() -> None:
    for name in ("wcag", "docs-langchain", "reference-langchain"):
        assert MCP_SERVERS[name]["transport"] == "streamable_http"
        assert MCP_SERVERS[name]["url"].startswith("https://")
