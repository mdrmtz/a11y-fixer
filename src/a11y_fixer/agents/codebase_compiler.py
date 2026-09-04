"""SubAgent spec: applies a fix candidate's code patch to the Angular fixture
and verifies it with `ng build`/`ng test`. Filesystem `permissions` enforce
the write scope in the architecture itself, not as a bolt-on check - first
matching rule wins, and an unmatched path defaults to allow, so the
allow-list must be followed by an explicit catch-all deny.

Build/test verification runs through the angular-cli MCP's `run_target` tool
rather than deepagents' native `execute` tool: `FilesystemMiddleware` does
not support `permissions` together with an execution-capable backend (raises
`NotImplementedError`), and the official MCP server already exposes exactly
this capability, so no bespoke shell execution is needed.

`RubricMiddleware` is Phase C's ToT DFS replacement: a grader sub-agent loops
over candidates (`max_iterations=3`, mirroring the plan's k=3 base sibling
count) until the rubric is satisfied. `domain.tot_search` still exists as a
pure, directly-testable algorithm - `evaluation/run_eval.py` uses it for
offline benchmark scoring, decoupled from any live LLM.

Worktree integration note: `build_from_tools()` is a sync factory that accepts
pre-resolved MCP tools so the expensive `aget_tools(["angular-cli"])` call
(npx process spawn) can be done once per benchmark run via `aresolve_tools()`
and reused across all 22 cases via `abuild_graph()`.
"""

from __future__ import annotations

from pathlib import Path
from deepagents import (
    FilesystemMiddleware,
    FilesystemPermission,
    RubricMiddleware,
    SubAgent,
)
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import tool

from a11y_fixer import config
from a11y_fixer.adapters.mcp_clients import aget_tools
from a11y_fixer.adapters.file_locator import (
    locate_selector_in_component as _locate_selector,
)

NAME = "codebase_compiler"

SYSTEM_PROMPT = """You are the Codebase Compiler for The A11y Fixer.

Task: Apply a fix to the Hallucinate.io Angular 22.1 fixture and verify it builds.
Fixture: Angular 22.1, standalone components, OnPush, @if/@for (no NgModules).
Constraint: Only write to allow-listed component files or src/index.html.

## CRITICAL: Always use locate_selector_in_component first

1. Extract selector from violation (e.g., "img[src$='maya.svg']")
2. Call locate_selector_in_component(selector, hint_text)
3. Read exact file at returned location
4. Apply minimal fix (one line change)
5. Call ng build to verify

Why? Deterministic file finding prevents 40-66% location failures.

## Workflow

- read_file -> locate_selector -> read exact file -> single edit -> ng build
- Preserve OnPush and imports[] array
- No over-engineering: add alt="..." or aria-label and done
- Be fast: time is limited (latency sensitive task)
"""


@tool
def locate_selector_in_component(
    selector: str,
    hint_text: str | None = None,
    codebase_root: str | None = None,
) -> list[dict]:
    """Locate component files matching a CSS selector.

    Uses deterministic file discovery (hint-text grepping + HTML regex matching)
    to find Angular component templates containing elements matching the provided
    CSS selector. Returns ranked results by confidence (0.0-1.0).

    Args:
        selector: CSS selector string (e.g., "img[src$='atlas-dashboard.svg']")
        hint_text: Specific text value to narrow search (e.g., "atlas-dashboard.svg")
        codebase_root: Root directory to search from (defaults to fixture root)

    Returns:
        List of dicts sorted by confidence (descending):
        {
            "file_path": str (path to component file),
            "line_number": int (1-indexed),
            "element_html": str (matched element),
            "confidence": float (0.0-1.0, higher = more certain)
        }
        Returns empty list if no matches found.
    """
    if codebase_root is None:
        codebase_root = config.fixture_path()
    else:
        codebase_root = Path(codebase_root)

    return _locate_selector(
        selector=selector,
        hint_text=hint_text,
        glob_pattern="src/**/*.component.html",
        codebase_root=codebase_root,
    )


RUBRIC_SYSTEM_PROMPT = """Grade the fix against these criteria (all must pass):

1. build_passes: Did ng build exit successfully (code 0)?
2. wcag_lexical: Does alt text describe semantic intent, not appearance?
3. no_regression: Does the fix break any other violations?

Respond satisfied=true only if all three pass.
"""


def _permissions(virtual_fixture: str) -> list[FilesystemPermission]:
    return [
        FilesystemPermission(
            operations=["read"], paths=[f"{virtual_fixture}/**"], mode="allow"
        ),
        FilesystemPermission(
            operations=["write"],
            paths=[
                f"{virtual_fixture}/src/app/**/*.component.html",
                f"{virtual_fixture}/src/app/**/*.component.ts",
                f"{virtual_fixture}/src/app/**/*.component.scss",
                f"{virtual_fixture}/src/index.html",
            ],
            mode="allow",
        ),
        FilesystemPermission(operations=["write"], paths=["/**"], mode="deny"),
    ]


def build_from_tools(
    mcp_tools: list,
    model: str | BaseChatModel,
    *,
    fixture_path: Path | None = None,
) -> SubAgent:
    """Sync factory — no network I/O.

    Constructs a `codebase_compiler` SubAgent from pre-resolved MCP tools.
    Called by `abuild_graph()` per benchmark case so the angular-cli npx
    process is only spawned once (in `aresolve_tools()`), not 22 times.

    Args:
        mcp_tools: Pre-resolved angular-cli MCP tools (list_projects already
            filtered out; locate_selector_in_component NOT in this list —
            this function re-adds it so the closure captures the correct
            per-case fixture path).
        model: LLM model spec string or BaseChatModel, forwarded to
            RubricMiddleware.
        fixture_path: Path to the fixture root (defaults to
            `config.fixture_path()`). Pass a worktree path to scope
            permissions and locate_selector_in_component's default root
            to the isolated worktree copy.
    """
    resolved_fixture = fixture_path or config.fixture_path()
    virtual_fixture = config.to_virtual_path(resolved_fixture)
    return SubAgent(
        name=NAME,
        description=(
            "Applies a fix candidate's code patch to the Angular fixture and "
            "verifies it builds and passes tests, using angular-cli-mcp for "
            "version-aware guidance."
        ),
        system_prompt=SYSTEM_PROMPT,
        tools=[*mcp_tools, locate_selector_in_component],
        skills=[config.to_virtual_path(config.resolve_skill("angular-cli-mcp"))],
        permissions=_permissions(virtual_fixture),
        middleware=[
            FilesystemMiddleware(
                tools=["read_file", "write_file", "edit_file"],
                _permissions=_permissions(virtual_fixture),
            ),
            RubricMiddleware(
                model=model, system_prompt=RUBRIC_SYSTEM_PROMPT, max_iterations=2
            ),
        ],
    )


async def build(
    model: str | BaseChatModel,
    *,
    fixture_path: Path | None = None,
) -> SubAgent:
    """Resolve this subagent's MCP tools and return its `SubAgent` spec.

    Expensive: spawns npx for the angular-cli MCP. For benchmark runs, prefer
    `aresolve_tools()` + `build_from_tools()` to avoid 22 npx spawns.

    Args:
        model: LLM model spec string or BaseChatModel.
        fixture_path: Path to the fixture root (defaults to
            `config.fixture_path()`). Forwarded to `build_from_tools()`.
    """
    mcp_tools = await aget_tools(["angular-cli"])
    # Filter out list_projects tool: it returns invalid structured content (missing parsingErrors field)
    mcp_tools = [t for t in mcp_tools if getattr(t, "name", None) != "list_projects"]
    return build_from_tools(mcp_tools, model, fixture_path=fixture_path)
