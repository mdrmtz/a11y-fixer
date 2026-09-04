"""Pure 0-20 composite rubric scorer for candidate accessibility fixes.

Three dimensions are deterministic pass/fail gates (build, AST validity,
visual stability); the WCAG compliance dimension is a continuous LLM-judge
score in [0, 1]. This split matches the Phase D "two-evaluator" design: a
deterministic heuristic evaluator (build/test/CLS, 15 pts) plus an LLM-judge
WCAG-compliance check (5 pts).
"""

from __future__ import annotations

from dataclasses import dataclass, field

BUILD_PASS_WEIGHT: float = 8.0
AST_VALID_WEIGHT: float = 4.0
WCAG_COMPLIANCE_WEIGHT: float = 5.0
VISUAL_STABILITY_WEIGHT: float = 3.0
MAX_TOTAL: float = (
    BUILD_PASS_WEIGHT
    + AST_VALID_WEIGHT
    + WCAG_COMPLIANCE_WEIGHT
    + VISUAL_STABILITY_WEIGHT
)

CLS_THRESHOLD: float = 0.05
BBOX_DRIFT_PCT_THRESHOLD: float = 2.0


@dataclass(frozen=True)
class RubricComponents:
    """Raw, unweighted measurements for one candidate fix."""

    build_pass: bool
    ast_valid: bool
    wcag_judge_score: float  # LLM judge confidence in [0, 1]
    cls: float | None = None  # Cumulative Layout Shift, measured via chrome-devtools
    bbox_drift_pct: float | None = (
        None  # bounding-box drift %, measured via chrome-devtools
    )


@dataclass(frozen=True)
class RubricScore:
    """The weighted composite score plus a per-dimension breakdown."""

    total: float
    max_total: float
    components: dict[str, float] = field(default_factory=dict)
    visual_stability_measured: bool = True


def _visual_stability_points(components: RubricComponents) -> tuple[float, bool]:
    """Full credit if both CLS and bbox drift are within threshold; 0 if not measured."""
    if components.cls is None or components.bbox_drift_pct is None:
        return 0.0, False
    stable = (
        components.cls <= CLS_THRESHOLD
        and components.bbox_drift_pct <= BBOX_DRIFT_PCT_THRESHOLD
    )
    return (VISUAL_STABILITY_WEIGHT if stable else 0.0), True


def score_candidate(components: RubricComponents) -> RubricScore:
    """Score one candidate fix against the 0-20 composite rubric."""
    if not 0.0 <= components.wcag_judge_score <= 1.0:
        msg = f"wcag_judge_score must be in [0, 1], got {components.wcag_judge_score}"
        raise ValueError(msg)

    build_points = BUILD_PASS_WEIGHT if components.build_pass else 0.0
    ast_points = AST_VALID_WEIGHT if components.ast_valid else 0.0
    wcag_points = WCAG_COMPLIANCE_WEIGHT * components.wcag_judge_score
    visual_points, measured = _visual_stability_points(components)

    breakdown = {
        "build_pass": build_points,
        "ast_valid": ast_points,
        "wcag_compliance": wcag_points,
        "visual_stability": visual_points,
    }
    return RubricScore(
        total=sum(breakdown.values()),
        max_total=MAX_TOTAL,
        components=breakdown,
        visual_stability_measured=measured,
    )


def best_of(candidates: list[RubricComponents]) -> tuple[int, RubricScore]:
    """Return `(index, score)` of the highest-scoring candidate.

    Expects raw `RubricComponents`, not nested candidate/rubric dicts - callers
    must extract components from candidate payloads before calling this.
    """
    if not candidates:
        msg = "candidates must be non-empty"
        raise ValueError(msg)
    scores = [score_candidate(c) for c in candidates]
    best_index = max(range(len(scores)), key=lambda i: scores[i].total)
    return best_index, scores[best_index]
