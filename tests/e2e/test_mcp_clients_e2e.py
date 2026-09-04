"""Real end-to-end test against the live wcag-mcp HTTP server (no subprocess
spawn needed, unlike the stdio-based angular-cli/chrome-devtools/playwright
servers). Skipped by default; run explicitly with `pytest tests/e2e/ -m e2e`.
"""

from __future__ import annotations

import pytest

from a11y_fixer.adapters.mcp_clients import aget_tools

pytestmark = pytest.mark.e2e


async def test_fetches_real_tools_from_wcag_mcp() -> None:
    tools = await aget_tools(["wcag"])

    assert len(tools) > 0
    tool_names = {tool.name for tool in tools}
    assert "get-full-criterion-context" in tool_names
