from __future__ import annotations

import pytest

from a11y_fixer.domain import hitl_policy
from a11y_fixer.domain.hitl_policy import HIGH_RISK_RULES, assess_risk


def test_high_risk_rules_list_is_empty() -> None:
    """color-contrast and html-has-lang were removed (2026-09):
    ViolationState.HITL_QUEUED now tracks an escalated violation across runs
    (see PrePipelineGate.should_process()'s HITL_QUEUED case), so forcing
    every instance of these two rules to a human regardless of score was no
    longer buying anything real - it was compensating for the
    unknown_state_fallback gap, not routing on actual risk."""
    assert HIGH_RISK_RULES == frozenset()


def test_color_contrast_with_perfect_score_and_narrow_file_now_routes_auto() -> None:
    """Was the HIGH_RISK_RULES example for forced human review; now that the
    rule has been removed from the list, a perfect-confidence, narrow-blast
    fix routes auto like any other rule. color-contrast's genuinely
    judgment-heavy cases are still caught by low_confidence when the model
    itself isn't sure."""
    assessment = assess_risk(rule="color-contrast", file_path="src/app/pages/product/product.component.html", rubric_score=20.0, p_ik=1.0)
    assert assessment.route == "auto"
    assert assessment.high_risk_rule is False


def test_html_has_lang_with_perfect_score_and_narrow_file_now_routes_auto() -> None:
    """Same change for html-has-lang, using a file path that doesn't also
    trip the (separate) high_blast_radius guardrail."""
    assessment = assess_risk(rule="html-has-lang", file_path="src/app/pages/about/about.component.html", rubric_score=20.0, p_ik=1.0)
    assert assessment.route == "auto"
    assert assessment.high_risk_rule is False


def test_low_confidence_routes_to_human() -> None:
    assessment = assess_risk(rule="button-name", file_path="src/app/pages/blog/blog.component.html", rubric_score=10.0, p_ik=0.9)
    assert assessment.route == "human"
    assert assessment.low_confidence is True


def test_low_p_ik_routes_to_human() -> None:
    assessment = assess_risk(rule="button-name", file_path="src/app/pages/blog/blog.component.html", rubric_score=20.0, p_ik=0.5)
    assert assessment.route == "human"
    assert assessment.low_confidence is True


def test_high_blast_radius_file_routes_to_human() -> None:
    assessment = assess_risk(rule="image-alt", file_path="src/index.html", rubric_score=20.0, p_ik=1.0)
    assert assessment.route == "human"
    assert assessment.high_blast_radius is True


def test_low_risk_rule_high_confidence_narrow_file_routes_auto() -> None:
    assessment = assess_risk(rule="image-alt", file_path="src/app/pages/about/about.component.html", rubric_score=20.0, p_ik=1.0)
    assert assessment.route == "auto"
    assert assessment.high_risk_rule is False
    assert assessment.high_blast_radius is False
    assert assessment.low_confidence is False


def test_verified_deterministic_waives_blast_radius_alone() -> None:
    """The html-has-lang fast-track carve-out: a deterministic, build-
    verified fix touching a high-blast-radius file (src/index.html) routes
    auto when blast radius is the only factor against it. high_blast_radius
    itself stays True - it's still an accurate signal for anything that
    inspects the RiskAssessment - only the routing decision is waived."""
    assessment = assess_risk(
        rule="html-has-lang",
        file_path="src/index.html",
        rubric_score=20.0,
        p_ik=1.0,
        verified_deterministic=True,
    )
    assert assessment.route == "auto"
    assert assessment.high_blast_radius is True
    assert any("waived" in reason for reason in assessment.reasons)


def test_verified_deterministic_does_not_waive_low_confidence() -> None:
    """The carve-out only covers blast radius - a low score/P(IK) still
    forces human even when verified_deterministic=True is passed (the real
    html-lang fast-track always hardcodes score=20.0, so this never happens
    in practice, but the guardrail must not blindly trust the flag)."""
    assessment = assess_risk(
        rule="html-has-lang",
        file_path="src/index.html",
        rubric_score=8.0,
        p_ik=0.4,
        verified_deterministic=True,
    )
    assert assessment.route == "human"
    assert assessment.low_confidence is True


def test_verified_deterministic_does_not_waive_high_risk_rule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nor does it waive a rule-level guardrail, if HIGH_RISK_RULES is ever
    populated again - verified_deterministic is specifically about the
    *file*, not a blanket bypass. HIGH_RISK_RULES is empty today (see
    test_high_risk_rules_list_is_empty), so this monkeypatches one back in
    to exercise the guardrail directly, on a file with no blast radius at
    all so only high_risk_rule is in play."""
    monkeypatch.setattr(hitl_policy, "HIGH_RISK_RULES", frozenset({"button-name"}))
    assessment = assess_risk(
        rule="button-name",
        file_path="src/app/pages/about/about.component.html",
        rubric_score=20.0,
        p_ik=1.0,
        verified_deterministic=True,
    )
    assert assessment.route == "human"
    assert assessment.high_risk_rule is True


def test_verified_deterministic_without_blast_radius_is_a_no_op() -> None:
    """Nothing to waive when the file isn't high-blast-radius in the first
    place - the flag has no effect on the route either way."""
    assessment = assess_risk(
        rule="image-alt",
        file_path="src/app/pages/about/about.component.html",
        rubric_score=20.0,
        p_ik=1.0,
        verified_deterministic=True,
    )
    assert assessment.route == "auto"
    assert assessment.high_blast_radius is False
