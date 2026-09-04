"""Risk-routing decision predicates for the HITL Bounded Decider.

Pure functions: given a violation's rule/file/measured confidence, decide
whether a candidate fix may be auto-delivered or must be escalated to a
human. `hitl/review_queue.py` wraps these predicates with the actual queue
mechanics and calibration (ROC/AUC, threshold tuning).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

# axe-core rules where even a passing candidate should still see a human,
# regardless of score. Empty for now: color-contrast and html-has-lang were
# removed (2026-09) once ViolationState.HITL_QUEUED started tracking escalated
# violations properly across runs - keeping them here was papering over
# should_process()'s unknown_state_fallback gap rather than routing on actual
# risk. color-contrast's judgment-heavy cases still get caught by
# low_confidence (a bad contrast fix scores low); html-has-lang's site-wide
# blast radius can be reinstated here (or added to
# HIGH_BLAST_RADIUS_PATH_FRAGMENTS) if real-world routing shows it's needed.
HIGH_RISK_RULES: frozenset[str] = frozenset()

# Path fragments whose edits are shared across more than one page/component.
HIGH_BLAST_RADIUS_PATH_FRAGMENTS: tuple[str, ...] = ("index.html", "app.html", "app.ts")

DEFAULT_RUBRIC_PASS_FLOOR: float = 15.0
DEFAULT_P_IK_FLOOR: float = 0.75

Route = Literal["auto", "human"]


@dataclass(frozen=True)
class RiskAssessment:
    """The outcome of routing one candidate fix through the Bounded Decider."""

    rule: str
    file_path: str
    rubric_score: float
    p_ik: float
    high_risk_rule: bool
    high_blast_radius: bool
    low_confidence: bool
    route: Route
    reasons: list[str] = field(default_factory=list)


def assess_risk(
    *,
    rule: str,
    file_path: str,
    rubric_score: float,
    p_ik: float,
    rubric_pass_floor: float = DEFAULT_RUBRIC_PASS_FLOOR,
    p_ik_floor: float = DEFAULT_P_IK_FLOOR,
    verified_deterministic: bool = False,
) -> RiskAssessment:
    """Route a candidate fix to `"auto"` delivery or `"human"` review.

    Any one of: a high-risk rule, a high-blast-radius file, a sub-floor
    rubric score, or a sub-floor P(IK) routes to `"human"` - unless
    `verified_deterministic=True` and blast radius is the *only* factor
    involved, in which case it does not by itself force human review
    (see the parameter's own docstring below).

    Args:
        verified_deterministic: Narrow carve-out for fixes with no LLM
            judgment call involved at all - today, only the
            html-has-lang fast-track (`domain/html_lang_fix.py` +
            `adapters/html_lang_applier.py`): the same fixed-string
            `<html lang="en">` edit every time, applied directly and
            verified by a real `ng build` before this function is ever
            reached. For that fix, touching the site-wide
            `src/index.html` isn't a risk a human needs to catch - a
            passing build already proves it, and there's no judgment
            call to second-guess since the template is hardcoded. This
            flag never waives `high_risk_rule` or `low_confidence`:
            those reflect the fix's own trustworthiness (an LLM fix
            could still be low-confidence garbage even in a narrow
            file), which a build passing says nothing about.
    """
    reasons: list[str] = []

    high_risk_rule = rule in HIGH_RISK_RULES
    if high_risk_rule:
        reasons.append(f"rule '{rule}' is on the HIGH_RISK_RULES list")

    high_blast_radius = any(
        fragment in file_path for fragment in HIGH_BLAST_RADIUS_PATH_FRAGMENTS
    )
    # `high_blast_radius` itself stays an accurate signal (the file really
    # is site-wide) even when waived below - only whether it *forces human*
    # is conditional, so audit trails (the HITL queue JSON's risk_assessments)
    # never lose the underlying fact.
    blast_radius_forces_human = high_blast_radius
    if high_blast_radius:
        reasons.append(f"file '{file_path}' matches a high-blast-radius path fragment")
        if verified_deterministic:
            blast_radius_forces_human = False
            reasons.append(
                "blast-radius risk waived: fix is deterministic and build-verified"
            )

    low_confidence = rubric_score < rubric_pass_floor or p_ik < p_ik_floor
    if rubric_score < rubric_pass_floor:
        reasons.append(
            f"rubric score {rubric_score} below pass floor {rubric_pass_floor}"
        )
    if p_ik < p_ik_floor:
        reasons.append(f"P(IK) {p_ik} below floor {p_ik_floor}")

    route: Route = (
        "human"
        if (high_risk_rule or blast_radius_forces_human or low_confidence)
        else "auto"
    )
    if route == "auto":
        reasons.append("no risk factors triggered")

    return RiskAssessment(
        rule=rule,
        file_path=file_path,
        rubric_score=rubric_score,
        p_ik=p_ik,
        high_risk_rule=high_risk_rule,
        high_blast_radius=high_blast_radius,
        low_confidence=low_confidence,
        route=route,
        reasons=reasons,
    )
