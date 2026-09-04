from __future__ import annotations

import pytest

from a11y_fixer.domain.rubric import RubricComponents, best_of, score_candidate


def test_perfect_candidate_scores_max_total() -> None:
    components = RubricComponents(build_pass=True, ast_valid=True, wcag_judge_score=1.0, cls=0.0, bbox_drift_pct=0.0)
    result = score_candidate(components)
    assert result.total == 20.0
    assert result.max_total == 20.0
    assert result.visual_stability_measured is True


def test_failed_build_loses_only_build_points() -> None:
    passing = RubricComponents(build_pass=True, ast_valid=True, wcag_judge_score=1.0, cls=0.0, bbox_drift_pct=0.0)
    failing = RubricComponents(build_pass=False, ast_valid=True, wcag_judge_score=1.0, cls=0.0, bbox_drift_pct=0.0)
    assert score_candidate(passing).total - score_candidate(failing).total == 8.0


def test_wcag_judge_score_is_scaled_linearly() -> None:
    components = RubricComponents(build_pass=False, ast_valid=False, wcag_judge_score=0.4, cls=None, bbox_drift_pct=None)
    result = score_candidate(components)
    assert result.components["wcag_compliance"] == pytest.approx(2.0)


def test_visual_stability_unmeasured_scores_zero_and_is_flagged() -> None:
    components = RubricComponents(build_pass=True, ast_valid=True, wcag_judge_score=1.0, cls=None, bbox_drift_pct=None)
    result = score_candidate(components)
    assert result.components["visual_stability"] == 0.0
    assert result.visual_stability_measured is False


def test_visual_stability_fails_outside_thresholds() -> None:
    components = RubricComponents(build_pass=True, ast_valid=True, wcag_judge_score=1.0, cls=0.2, bbox_drift_pct=5.0)
    result = score_candidate(components)
    assert result.components["visual_stability"] == 0.0
    assert result.visual_stability_measured is True


def test_out_of_range_wcag_judge_score_raises() -> None:
    with pytest.raises(ValueError, match="wcag_judge_score"):
        score_candidate(RubricComponents(build_pass=True, ast_valid=True, wcag_judge_score=1.5))


def test_best_of_picks_highest_scoring_candidate() -> None:
    candidates = [
        RubricComponents(build_pass=False, ast_valid=True, wcag_judge_score=0.5),
        RubricComponents(build_pass=True, ast_valid=True, wcag_judge_score=0.9),
        RubricComponents(build_pass=True, ast_valid=False, wcag_judge_score=0.2),
    ]
    index, result = best_of(candidates)
    assert index == 1
    assert result.total == score_candidate(candidates[1]).total


def test_best_of_rejects_empty_list() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        best_of([])
