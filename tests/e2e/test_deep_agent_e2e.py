"""Real end-to-end test: builds the full deep agent graph, wiring all 6 MCP
servers (3 stdio subprocess-spawned, 3 remote HTTP) and all 4 subagents.
Does not invoke the graph (no LLM call) - only verifies construction
succeeds. Skipped by default; run explicitly with `pytest tests/e2e/ -m e2e`.
"""

from __future__ import annotations

import pytest
from langgraph.graph.state import CompiledStateGraph

from a11y_fixer.deep_agent import build_agent

pytestmark = pytest.mark.e2e


def test_build_agent_compiles_the_full_graph() -> None:
    graph = build_agent()
    assert isinstance(graph, CompiledStateGraph)
