"""SubAgent spec: resolves axe-core violations into fix candidates using
`wcag-mcp` live. The remediation algorithm itself (rule -> WCAG SC mapping,
technique selection, anti-pattern checks, output schema) lives in the
`a11y-fixer` Skill, loaded on activation - this module only wires the role,
the scoped `wcag` MCP tools, and that skill together.
"""

from __future__ import annotations

from deepagents import SubAgent

from a11y_fixer import config
from a11y_fixer.adapters.mcp_clients import aget_tools

NAME = "compliance_planner"

SYSTEM_PROMPT = """You are the Compliance Planner for The A11y Fixer.

Given one axe-core violation (rule id, CSS selector, failing DOM node HTML,
page URL), produce a fix candidate. Follow the `a11y-fixer` skill's
remediation algorithm exactly: it defines how to map the rule to a WCAG
success criterion, query `wcag-mcp` for sufficient/advisory techniques and
known failure patterns, validate against framework-specific anti-patterns,
and construct the final JSON fix candidate. Never rely on static training
data for WCAG compliance - every violation must be resolved by a live
`wcag-mcp` call.

Leave the candidate's `score` field at 0. The QA Critic subagent evaluates
candidates; you only propose them.
"""


async def build() -> SubAgent:
    """Resolve this subagent's MCP tools and return its `SubAgent` spec."""
    tools = await aget_tools(["wcag"])
    return SubAgent(
        name=NAME,
        description=(
            "Resolves one axe-core WCAG violation into a fix candidate by "
            "querying wcag-mcp live for the relevant success criterion, "
            "techniques, and known failure patterns."
        ),
        system_prompt=SYSTEM_PROMPT,
        tools=tools,
        skills=[config.to_virtual_path(config.resolve_skill("a11y-fixer"))],
    )
