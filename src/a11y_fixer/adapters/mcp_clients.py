"""MCP tool wiring via the official `langchain-mcp-adapters` client.

Mirrors the server set in `cmu-capstone/.vscode/mcp.json` so the agent's
runtime tool set stays in lockstep with the human-facing dev environment.
Each subagent requests only the servers it needs; `build_client` constructs
a client scoped to just that subset so a subagent never spawns (or waits on)
a stdio process it has no use for.
"""

from __future__ import annotations

import asyncio

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.sessions import Connection

MCP_SERVERS: dict[str, Connection] = {
    "angular-cli": {
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@angular/cli", "mcp"],
    },
    "chrome-devtools": {
        "transport": "stdio",
        "command": "npx",
        "args": ["chrome-devtools-mcp@latest"],
    },
    "playwright": {
        "transport": "stdio",
        "command": "npx",
        "args": ["@playwright/mcp@latest"],
    },
    "wcag": {"transport": "streamable_http", "url": "https://wcag-mcp.netlify.app/mcp"},
    "docs-langchain": {
        "transport": "streamable_http",
        "url": "https://docs.langchain.com/mcp",
    },
    "reference-langchain": {
        "transport": "streamable_http",
        "url": "https://reference.langchain.com/mcp",
    },
}


def build_client(server_names: list[str] | None = None) -> MultiServerMCPClient:
    """Build a client scoped to `server_names` (default: every known server)."""
    names = server_names if server_names is not None else list(MCP_SERVERS)
    unknown = sorted(set(names) - set(MCP_SERVERS))
    if unknown:
        msg = f"Unknown MCP server(s): {unknown}. Known servers: {sorted(MCP_SERVERS)}"
        raise ValueError(msg)
    return MultiServerMCPClient({name: MCP_SERVERS[name] for name in names})


async def aget_tools(server_names: list[str] | None = None) -> list[BaseTool]:
    """Fetch every tool exposed by `server_names` (async)."""
    client = build_client(server_names)
    return await client.get_tools()


def get_tools(server_names: list[str] | None = None) -> list[BaseTool]:
    """Sync wrapper around `aget_tools`, for callers outside an event loop (e.g. `cli.py`)."""
    return asyncio.run(aget_tools(server_names))
