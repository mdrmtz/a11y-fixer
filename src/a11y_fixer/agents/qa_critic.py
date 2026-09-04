"""SubAgent spec: scores one candidate fix against the 0-20 composite rubric
(`domain.rubric`). Reports raw measurements only - `domain.rubric.
score_candidate()` computes the weighted total, so scoring logic lives in one
place instead of being duplicated in a prompt.
"""

from __future__ import annotations

from deepagents import SubAgent
from langchain_core.tools import tool

from a11y_fixer.adapters.mcp_clients import aget_tools
from a11y_fixer.domain.rubric import RubricComponents, score_candidate

NAME = "qa_critic"

SYSTEM_PROMPT = """You are the QA Critic for The A11y Fixer.

Evaluate one candidate fix using the two-evaluator split:

- Deterministic heuristic: does `ng build` pass? Is the AST/template
  structurally valid? Is visual layout stable (CLS <= 0.05, bounding-box
  drift <= 2%), measured via the chrome-devtools MCP's `performance_start_
  trace`/`performance_stop_trace` and `take_snapshot` tools?
- LLM judge: does the fix genuinely satisfy the WCAG success criterion's
  *intent* (not just its letter)? Score this as a confidence in [0, 1].

You MUST call the `score_rubric` tool with your raw measurements (build_pass,
ast_valid, wcag_judge_score, cls, bbox_drift_pct) and report its returned
`total` verbatim as your final score. Never compute or guess the weighted
composite total yourself - `score_rubric` is the only source of truth for
that number, so the same scoring math is never duplicated between your
reasoning and the codebase.

IMPORTANT: Always provide the optional explanation fields when calling
score_rubric so downstream analysis can understand WHY each criterion passed
or failed:
- build_error: brief reason if build failed (e.g., "Missing import statement")
- ast_error: brief reason if AST check failed (e.g., "Unclosed div tag")
- wcag_judge_comment: explain your WCAG confidence score (e.g., "Detects 2/3 images")
- visual_error: brief reason if visual stability check failed
"""


@tool
def score_rubric(
    build_pass: bool,
    ast_valid: bool,
    wcag_judge_score: float,
    cls: float | None = None,
    bbox_drift_pct: float | None = None,
    build_error: str | None = None,
    ast_error: str | None = None,
    wcag_judge_comment: str | None = None,
    visual_error: str | None = None,
) -> dict:
    """Compute the deterministic 0-20 composite rubric score from raw measurements.

    Always call this with your own raw measurements and report the returned
    `total` verbatim - never invent or estimate the weighted total yourself.

    Optional error/comment fields provide explanation for downstream analysis:
    - build_error: if build_pass=False, explain why (e.g., "missing import")
    - ast_error: if ast_valid=False, explain why (e.g., "unclosed tag")
    - wcag_judge_comment: brief explanation of the LLM judge's confidence score
    - visual_error: if visual stability check failed, explain why
    """
    components = RubricComponents(
        build_pass=build_pass,
        ast_valid=ast_valid,
        wcag_judge_score=wcag_judge_score,
        cls=cls,
        bbox_drift_pct=bbox_drift_pct,
    )
    result = score_candidate(components)

    # Build detailed breakdown for observability
    criteria = [
        {
            "name": "Build Pass",
            "max_points": 8.0,
            "awarded": result.components.get("build_pass", 0.0),
            "passed": build_pass,
            "reason": build_error if not build_pass else "Build succeeded",
        },
        {
            "name": "AST Valid",
            "max_points": 4.0,
            "awarded": result.components.get("ast_valid", 0.0),
            "passed": ast_valid,
            "reason": ast_error if not ast_valid else "AST valid",
        },
        {
            "name": "WCAG Compliance",
            "max_points": 5.0,
            "awarded": result.components.get("wcag_compliance", 0.0),
            "passed": wcag_judge_score >= 0.8,
            "reason": wcag_judge_comment
            or f"LLM judge confidence: {wcag_judge_score:.1%}",
        },
        {
            "name": "Visual Stability",
            "max_points": 3.0,
            "awarded": result.components.get("visual_stability", 0.0),
            "passed": result.visual_stability_measured
            and result.components.get("visual_stability", 0.0) > 0,
            "reason": (
                visual_error
                if not result.visual_stability_measured
                else f"CLS={cls}, bbox_drift={bbox_drift_pct}%"
            ),
        },
    ]

    return {
        "total": result.total,
        "max_total": result.max_total,
        "components": result.components,
        "visual_stability_measured": result.visual_stability_measured,
        "criteria_breakdown": criteria,  # NEW: detailed per-criterion info
    }


async def build() -> SubAgent:
    """Resolve this subagent's MCP tools and return its `SubAgent` spec."""
    tools = await aget_tools(["chrome-devtools"])
    return SubAgent(
        name=NAME,
        description=(
            "Scores a candidate fix against the 0-20 rubric using a "
            "deterministic build/test/CLS check plus an LLM WCAG-compliance judge."
        ),
        system_prompt=SYSTEM_PROMPT,
        tools=[*tools, score_rubric],
    )
