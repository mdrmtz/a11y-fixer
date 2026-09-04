"""Composition root: builds the A11y Fixer deep agent graph.

Superseded design note: Phase A built a custom `Orchestrator` class; this
module replaces it entirely per the plan's Phase B migration - `deepagents.
create_deep_agent()` is the sole orchestration layer, and every injected
dependency (model, tools, subagents, skills, memory, permissions,
interrupt_on) is a keyword argument to a single call.

Phase worktree integration note: `aresolve_tools()` / `abuild_graph()` split
the old monolithic `abuild_agent()` into two parts so that the expensive MCP
setup (npx process spawns) runs once per benchmark run, while the fast graph
wiring runs per benchmark case with a per-case fixture path (worktree path).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from deepagents import FilesystemPermission
from deepagents.backends import BackendProtocol, FilesystemBackend
from langchain.agents.middleware import InterruptOnConfig
from langchain.agents.structured_output import ToolStrategy
from langchain_core.tools import tool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel, Field

from a11y_fixer import config
from a11y_fixer.adapters.audit_runner import AxeAuditRunner
from a11y_fixer.adapters.mcp_clients import aget_tools
from a11y_fixer.adapters.retrieval import wiki_pipeline
from a11y_fixer.agents import (
    audit_crawler,
    codebase_compiler,
    compliance_planner,
    qa_critic,
)


class ViolationResponse(BaseModel):
    """Structured output for one resolved (or escalated) axe-core violation."""

    rule: str
    wcag: str
    selector: str
    technique_id: str
    technique_type: Literal["sufficient", "advisory"]
    code: str
    rationale: str
    score: float = Field(ge=0.0, le=20.0)
    route: Literal["auto", "human"]
    scoring_details: dict | None = None  # Per-criterion breakdown from qa_critic


@tool
async def run_axe_audit() -> dict:
    """Run a full axe-core WCAG 2.2 AA audit against the configured fixture
    (starts `ng serve`, discovers routes, runs `@axe-core/cli`, tears the
    server back down) and return the normalized violation report.
    """
    runner = AxeAuditRunner(fixture_path=config.fixture_path())
    return await audit_crawler.discover_and_audit(runner)


TOP_LEVEL_SYSTEM_PROMPT = """You are The A11y Fixer: an autonomous WCAG 2.2 AA
remediation agent for the Hallucinate.io Angular SPA.

You MUST NOT call `ls`, `read_file`, `glob`, `grep`, `write_file`, or
`edit_file` yourself - those tools are reserved for your subagents.
Investigating or editing the codebase directly instead of delegating is a
failure of your job, even if delegating seems slower.

For each axe-core violation you are given, delegate through all three
subagents in order - never skip a subagent and never produce a final answer
before all three have run:
1. Delegate to `compliance_planner` to produce a fix candidate.
2. Delegate to `codebase_compiler` to apply and verify the patch.
3. Delegate to `qa_critic` to score the candidate against the rubric.
4. Combine the QA Critic's rubric score with your own confidence to decide
   whether the fix should be delivered automatically (`route: "auto"`) or
   escalated to a human (`route: "human"`) - never assume auto-delivery for
   a rule you are not fully confident about.

Before you return a final `ViolationResponse`, count your own `task()` tool
calls in this conversation: if it is fewer than 3 - one each to
`compliance_planner`, `codebase_compiler`, and `qa_critic` - you are NOT
done. Stopping after only `compliance_planner` and fabricating a `score`
is a critical failure, even if the plan looks correct - the score MUST come
from `qa_critic`'s real rubric evaluation, never a guess.

Return your final answer as the structured `ViolationResponse` schema.
"""


def _default_permissions(virtual_fixture: str) -> list[FilesystemPermission]:
    """Read-only at the top level; only `codebase_compiler`'s own permission
    override (a narrower allow-list) may write fixture files.
    """
    return [
        FilesystemPermission(
            operations=["read"], paths=[f"{virtual_fixture}/**"], mode="allow"
        ),
        FilesystemPermission(operations=["write"], paths=["/**"], mode="deny"),
    ]


@dataclass
class ResolvedTools:
    """Pre-resolved tools and subagents for per-case graph construction.

    `aresolve_tools()` populates this once; `abuild_graph()` consumes it per
    benchmark case — separating the slow MCP setup (npx spawns) from the fast
    graph wiring so 22 benchmark cases don't each pay the MCP startup cost.

    `other_subagents` stores [compliance_planner, qa_critic, audit_crawler]
    in that order; `abuild_graph()` inserts a freshly wired `codebase_compiler`
    at index 1 to preserve the original [cp, cc, qc, ac] subagent order.
    """

    model_spec: str
    top_level_tools: list
    cc_mcp_tools: list  # angular-cli MCP tools, cached to avoid npx respawn
    other_subagents: list = field(default_factory=list)  # [cp, qc, ac]


async def aresolve_tools() -> ResolvedTools:
    """Connect to MCP servers once and return ResolvedTools for abuild_graph().

    Expensive: spawns npx for the angular-cli MCP. Call once per benchmark run,
    then pass the result to abuild_graph() for each case.
    """
    config.configure_model_providers()
    model_spec = config.selected_llm_backend().model
    wiki_pipeline.init_wiki(config.wiki_dir())

    top_level_tools, (cp_subagent, cc_subagent, qc_subagent, ac_subagent) = (
        await asyncio.gather(
            aget_tools(["docs-langchain", "reference-langchain"]),
            asyncio.gather(
                compliance_planner.build(),
                codebase_compiler.build(model_spec),
                qa_critic.build(),
                audit_crawler.build(),
            ),
        )
    )
    # Extract angular-cli MCP tools from cc_subagent (SubAgent is a TypedDict).
    # Strip locate_selector_in_component: build_from_tools() re-adds it so it
    # captures the per-case fixture path rather than the default one.
    cc_mcp_tools = [
        t for t in cc_subagent["tools"]
        if getattr(t, "name", None) != "locate_selector_in_component"
    ]
    return ResolvedTools(
        model_spec=model_spec,
        top_level_tools=list(top_level_tools),
        cc_mcp_tools=cc_mcp_tools,
        other_subagents=[cp_subagent, qc_subagent, ac_subagent],
    )


def abuild_graph(
    resolved: ResolvedTools,
    *,
    fixture_path: Path | None = None,
    backend: BackendProtocol | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
) -> CompiledStateGraph:
    """Sync, fast graph construction — no network I/O.

    Called per evaluation case. Pass `fixture_path` to scope permissions and
    path mappings to an isolated worktree copy of the repo.
    """
    from deepagents import (
        create_deep_agent,
    )  # noqa: PLC0415 - deferred: keeps module import side-effect-free for tests

    resolved_fixture = fixture_path or config.fixture_path()
    virtual_fixture = config.to_virtual_path(resolved_fixture)
    resolved_backend = backend or FilesystemBackend(root_dir=str(config.repo_root()))

    cc_subagent = codebase_compiler.build_from_tools(
        resolved.cc_mcp_tools, resolved.model_spec, fixture_path=resolved_fixture
    )
    # Preserve original subagent order: compliance_planner, codebase_compiler,
    # qa_critic, audit_crawler — order matters for how create_deep_agent
    # exposes them to the top-level agent's task() tool.
    all_subagents = [
        resolved.other_subagents[0],  # compliance_planner
        cc_subagent,                   # codebase_compiler (per-case fixture path)
        resolved.other_subagents[1],  # qa_critic
        resolved.other_subagents[2],  # audit_crawler
    ]

    return create_deep_agent(
        model=resolved.model_spec,
        tools=[run_axe_audit, *resolved.top_level_tools],
        system_prompt=TOP_LEVEL_SYSTEM_PROMPT,
        subagents=all_subagents,
        skills=[
            config.to_virtual_path(config.resolve_skill("a11y-fixer")),
            config.to_virtual_path(config.resolve_skill("cmu-capstone-docs")),
        ],
        memory=[
            config.to_virtual_path(wiki_pipeline.memory_file_path(config.wiki_dir()))
        ],
        permissions=_default_permissions(virtual_fixture),
        interrupt_on={
            "write_file": InterruptOnConfig(allowed_decisions=["approve", "reject"]),
            "edit_file": InterruptOnConfig(allowed_decisions=["approve", "reject"]),
        },
        # ToolStrategy (structured output emitted as a tool call), not the
        # bare schema (which lets create_deep_agent auto-pick ProviderStrategy
        # per-model) - ProviderStrategy's native JSON-schema mode reproducibly
        # returns an empty response for openrouter:meta-llama/llama-3.3-70b-
        # instruct; ToolStrategy is far more broadly compatible across providers.
        response_format=ToolStrategy(schema=ViolationResponse),
        backend=resolved_backend,
        # This graph is invoked directly (a root graph, not a subgraph), so
        # `checkpointer=True` is invalid here - LangGraph requires a real
        # BaseCheckpointSaver instance to persist state across HITL interrupts.
        checkpointer=checkpointer or InMemorySaver(),
    )


async def abuild_agent(
    *,
    backend: BackendProtocol | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
) -> CompiledStateGraph:
    """Async composition root: resolves every MCP-backed tool/subagent, then
    calls `create_deep_agent()` exactly once.

    Thin wrapper over `aresolve_tools()` + `abuild_graph()` for callers that
    don't need the per-case split (CLI, one-shot runs).
    """
    resolved = await aresolve_tools()
    return abuild_graph(resolved, backend=backend, checkpointer=checkpointer)


def build_agent(
    *,
    backend: BackendProtocol | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
) -> CompiledStateGraph:
    """Sync wrapper around `abuild_agent`, for callers outside an event loop (`cli.py`)."""
    return asyncio.run(abuild_agent(backend=backend, checkpointer=checkpointer))
