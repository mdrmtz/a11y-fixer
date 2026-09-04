from __future__ import annotations

import json
import math
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from a11y_fixer import config
from a11y_fixer.adapters.pr import delivery as pr_delivery
from a11y_fixer.adapters.pr.github_pr_manager import PRMergeResult
from a11y_fixer.adapters.violation_store import PrePipelineGate, ViolationStore
from a11y_fixer.domain.violations import ViolationState, compute_violation_id
from a11y_fixer.hitl import review_queue as review_queue_module
from a11y_fixer.hitl.review_queue import (
    Calibration,
    ReviewQueue,
    calibrate_from_results,
    calibrate_p_ik_floor,
    roc_auc,
    roc_points,
)


def test_roc_auc_perfect_separation_is_one() -> None:
    labels = [1, 1, 1, 0, 0, 0]
    scores = [0.9, 0.8, 0.7, 0.3, 0.2, 0.1]
    assert roc_auc(labels, scores) == 1.0


def test_roc_auc_reversed_separation_is_zero() -> None:
    labels = [1, 1, 0, 0]
    scores = [0.1, 0.2, 0.8, 0.9]
    assert roc_auc(labels, scores) == 0.0


def test_roc_auc_ties_count_half() -> None:
    assert roc_auc([1, 0], [0.5, 0.5]) == 0.5


def test_roc_auc_nan_when_one_class_missing() -> None:
    assert math.isnan(roc_auc([1, 1, 1], [0.9, 0.8, 0.7]))


def test_roc_points_spans_zero_to_one() -> None:
    points = roc_points([1, 1, 0, 0], [0.9, 0.8, 0.3, 0.2])
    assert points[0] == (0.0, 0.0)
    assert points[-1] == (1.0, 1.0)


def _twenty_cleared_p_iks() -> list[float]:
    """20 synthetic 'cleared' confidences - enough samples for a 10% FPR budget to bite."""
    return [0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.80, 0.85, 0.85, 0.90, 0.90, 0.90, 0.95, 0.95, 0.95, 0.95, 1.0, 1.0, 1.0]


def test_calibrate_p_ik_floor_finds_highest_floor_within_budget() -> None:
    floor = calibrate_p_ik_floor(_twenty_cleared_p_iks(), target_fpr=0.1)
    cleared = sorted(_twenty_cleared_p_iks())
    false_escalations = sum(1 for p in cleared if p < floor)
    assert false_escalations / len(cleared) <= 0.1
    # a strictly higher floor would blow the 10% budget
    assert sum(1 for p in cleared if p < floor + 0.05) / len(cleared) > 0.1


def test_calibrate_p_ik_floor_empty_falls_back_to_default() -> None:
    from a11y_fixer.domain.hitl_policy import DEFAULT_P_IK_FLOOR

    assert calibrate_p_ik_floor([]) == DEFAULT_P_IK_FLOOR


def test_calibrate_p_ik_floor_zero_budget_never_raises_the_floor() -> None:
    assert calibrate_p_ik_floor([0.6, 0.7, 0.8], target_fpr=0.0) == 0.0


def _write_results_summary(path: Path, *, cleared_p_iks: list[float], not_cleared_p_iks: list[float]) -> None:
    cases = [{"case_id": f"c{i}", "rule": "image-alt", "page": "/x", "route": "auto", "rubric_score": p * 20.0, "cleared": True} for i, p in enumerate(cleared_p_iks)]
    cases += [{"case_id": f"nc{i}", "rule": "image-alt", "page": "/x", "route": "auto", "rubric_score": p * 20.0, "cleared": False} for i, p in enumerate(not_cleared_p_iks)]
    path.write_text(json.dumps({"summary": {}, "cases": cases}, indent=2), encoding="utf-8")


def test_calibrate_from_results_missing_file_is_uncalibrated(tmp_path: Path) -> None:
    calibration = calibrate_from_results(tmp_path / "does_not_exist.json")
    assert calibration.calibrated is False
    assert calibration.sample_size == 0


def test_calibrate_from_results_too_few_cases_is_uncalibrated(tmp_path: Path) -> None:
    results_path = tmp_path / "results_summary.json"
    _write_results_summary(results_path, cleared_p_iks=[0.9], not_cleared_p_iks=[])
    calibration = calibrate_from_results(results_path)
    assert calibration.calibrated is False


def test_calibrate_from_results_real_file_calibrates(tmp_path: Path) -> None:
    results_path = tmp_path / "results_summary.json"
    _write_results_summary(results_path, cleared_p_iks=_twenty_cleared_p_iks(), not_cleared_p_iks=[0.1, 0.2, 0.3])
    calibration = calibrate_from_results(results_path, target_fpr=0.1)
    assert isinstance(calibration, Calibration)
    assert calibration.calibrated is True
    assert calibration.sample_size == 23
    assert calibration.auc == 1.0  # every not-cleared p_ik is well below every cleared one
    assert calibration.p_ik_floor > 0.0


def _queue_item(rule: str = "image-alt", changes: list[dict] | None = None) -> dict:
    return {
        "violation": {"rule": rule, "url": "/about", "selector": "img", "html": "<img>"},
        "response": {"rule": rule, "rationale": "descriptive alt text", "score": 10.0, "route": "human"},
        "changes": changes if changes is not None else [{"path": "about.component.html", "old_content": "<img>\n", "new_content": '<img alt="logo">\n'}],
    }


def _store(tmp_path: Path) -> ViolationStore:
    """The same on-disk location `_queue()` points its `ReviewQueue` at, so
    a test can open a fresh `ViolationStore` after `review()` runs and see
    exactly what got persisted - without reaching into `ReviewQueue`'s
    private `_store` attribute.
    """
    return ViolationStore(status_file=tmp_path / ".violation_status.json")


def _queue(tmp_path: Path) -> ReviewQueue:
    return ReviewQueue(
        tmp_path / "hitl_queue",
        wiki_dir=tmp_path / "wiki",
        pr_config=config.PRDeliveryConfig(live=False, github_token=None, github_repo=None),
        output_dir=tmp_path / "prs",
        # Isolated store: without this, ReviewQueue.__init__'s default
        # points at the REAL repo-root `.violation_status.json,` and every
        # test in this file would pollute it (this is exactly how the
        # stray "rule-a"/"rule-b" entries got into the real file).
        store=_store(tmp_path),
    )


def test_list_pending_empty_queue_dir(tmp_path: Path) -> None:
    assert _queue(tmp_path).list_pending() == []


def test_list_pending_excludes_already_reviewed(tmp_path: Path) -> None:
    queue_dir = tmp_path / "hitl_queue"
    queue_dir.mkdir(parents=True)
    (queue_dir / "1-a.json").write_text(json.dumps(_queue_item()), encoding="utf-8")
    (queue_dir / "2-b.json").write_text(json.dumps(_queue_item()), encoding="utf-8")
    (queue_dir / "2-b.decision.json").write_text("{}", encoding="utf-8")

    pending = _queue(tmp_path).list_pending()

    assert [p.name for p in pending] == ["1-a.json"]


def test_review_reject_ingests_a_real_wiki_lesson(tmp_path: Path) -> None:
    queue_dir = tmp_path / "hitl_queue"
    queue_dir.mkdir(parents=True)
    queue_path = queue_dir / "1-image-alt.json"
    queue_path.write_text(json.dumps(_queue_item()), encoding="utf-8")

    result = _queue(tmp_path).review(queue_path, "reject", reviewer="alice", notes="use a longer alt description")

    assert result["decision"] == "reject"
    assert result["reviewer"] == "alice"
    assert "lesson_id" in result
    lessons_dir = tmp_path / "wiki" / "lessons"
    assert len(list(lessons_dir.glob("*.json"))) == 1
    memory_text = (tmp_path / "wiki" / "AGENTS.md").read_text(encoding="utf-8")
    assert "image-alt" in memory_text
    assert "use a longer alt description" in memory_text


def test_review_reject_marks_violation_wont_fix_in_store(tmp_path: Path) -> None:
    """Regression test (2026-09-04): rejecting a HITL item used to never
    touch `.violation_status.json` at all, so `PrePipelineGate` kept
    re-surfacing the same rejected violation on every later run.
    """
    queue_dir = tmp_path / "hitl_queue"
    queue_dir.mkdir(parents=True)
    queue_path = queue_dir / "1-image-alt.json"
    queue_path.write_text(json.dumps(_queue_item()), encoding="utf-8")

    _queue(tmp_path).review(
        queue_path, "reject", reviewer="alice", notes="needs a longer description"
    )

    violation_id = compute_violation_id("image-alt", "img")
    status = _store(tmp_path).get(violation_id)

    assert status is not None
    assert status.state == ViolationState.WONT_FIX
    assert status.close_reason == "needs a longer description"
    assert status.closed_at is not None


def test_pre_pipeline_gate_skips_a_rejected_violation_by_name_not_fallback(
    tmp_path: Path,
) -> None:
    """The exact bug the user reported (via PR #21's approve path, mirrored
    here on reject): a HITL decision must show up to `PrePipelineGate` as
    its own named reason, never `unknown_state_fallback`.
    """
    queue_dir = tmp_path / "hitl_queue"
    queue_dir.mkdir(parents=True)
    queue_path = queue_dir / "1-image-alt.json"
    queue_path.write_text(json.dumps(_queue_item()), encoding="utf-8")

    _queue(tmp_path).review(queue_path, "reject", reviewer="alice", notes="not accessible enough")

    gate = PrePipelineGate(_store(tmp_path))
    action, reason, _old_pr = gate.should_process("image-alt", "img", 10.0, "some-hash")

    assert action == "SKIP"
    assert reason == "marked_wont_fix_by_human"


def test_review_approve_delivers_a_real_dry_run_pr(tmp_path: Path) -> None:
    queue_dir = tmp_path / "hitl_queue"
    queue_dir.mkdir(parents=True)
    queue_path = queue_dir / "1-image-alt.json"
    queue_path.write_text(json.dumps(_queue_item()), encoding="utf-8")

    result = _queue(tmp_path).review(queue_path, "approve", reviewer="bob")

    assert result["decision"] == "approve"
    assert result["delivered"] is True
    assert Path(result["result"]["diff_path"]).exists()


def test_review_approve_dry_run_marks_violation_pr_open_with_no_pr_number(
    tmp_path: Path,
) -> None:
    queue_dir = tmp_path / "hitl_queue"
    queue_dir.mkdir(parents=True)
    queue_path = queue_dir / "1-image-alt.json"
    queue_path.write_text(json.dumps(_queue_item()), encoding="utf-8")

    _queue(tmp_path).review(queue_path, "approve", reviewer="bob")

    violation_id = compute_violation_id("image-alt", "img")
    status = _store(tmp_path).get(violation_id)

    assert status is not None
    assert status.state == ViolationState.PR_OPEN
    assert status.current_pr_number is None
    assert status.current_score == 10.0


def test_review_approve_live_auto_merge_success_marks_violation_merged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test for the reported bug (PR #21 approved via HITL but
    `.violation_status.json` never synced): a live approve that auto-merges
    must leave the store at `MERGED` with the real PR number, so the next
    `fleet`/`run` for this violation is a clean `already_merged_to_main`
    skip instead of `unknown_state_fallback`.
    """
    queue_dir = tmp_path / "hitl_queue"
    queue_dir.mkdir(parents=True)
    queue_path = queue_dir / "1-image-alt.json"
    queue_path.write_text(json.dumps(_queue_item()), encoding="utf-8")

    live_result = pr_delivery.LiveResult(
        pull_request_url="https://github.com/owner/repo/pull/21",
        pull_request_number=21,
        branch_name="a11y-fixer/image-alt-123",
    )
    monkeypatch.setattr(
        review_queue_module.pr_delivery,
        "deliver",
        lambda plan, config, output_dir: live_result,  # noqa: ARG005
    )

    fake_manager = MagicMock()
    fake_manager.auto_merge_pr.return_value = PRMergeResult(
        success=True, pr_number=21, reason="auto_merged_high_score (20.0 >= 18.0)"
    )
    fake_manager.cleanup_duplicate_prs.return_value = []
    monkeypatch.setattr(
        review_queue_module, "GitHubPRManager", lambda **kwargs: fake_manager  # noqa: ARG005
    )

    queue = ReviewQueue(
        queue_dir,
        wiki_dir=tmp_path / "wiki",
        pr_config=config.PRDeliveryConfig(
            live=True, github_token="fake-token", github_repo="owner/repo"
        ),
        output_dir=tmp_path / "prs",
        store=_store(tmp_path),
    )

    result = queue.review(queue_path, "approve", reviewer="bob")

    assert result["auto_merge"]["success"] is True
    violation_id = compute_violation_id("image-alt", "img")
    status = _store(tmp_path).get(violation_id)

    assert status is not None
    assert status.state == ViolationState.MERGED
    assert status.current_pr_number == 21


def test_review_approve_with_no_persisted_changes_reports_reason(tmp_path: Path) -> None:
    queue_dir = tmp_path / "hitl_queue"
    queue_dir.mkdir(parents=True)
    queue_path = queue_dir / "1-image-alt.json"
    queue_path.write_text(json.dumps(_queue_item(changes=[])), encoding="utf-8")

    result = _queue(tmp_path).review(queue_path, "approve", reviewer="bob")

    assert result["delivered"] is False
    assert "reason" in result


def test_review_approve_with_no_changes_does_not_touch_store(tmp_path: Path) -> None:
    queue_dir = tmp_path / "hitl_queue"
    queue_dir.mkdir(parents=True)
    queue_path = queue_dir / "1-image-alt.json"
    queue_path.write_text(json.dumps(_queue_item(changes=[])), encoding="utf-8")

    _queue(tmp_path).review(queue_path, "approve", reviewer="bob")

    violation_id = compute_violation_id("image-alt", "img")
    assert _store(tmp_path).get(violation_id) is None


def test_review_twice_raises(tmp_path: Path) -> None:
    queue_dir = tmp_path / "hitl_queue"
    queue_dir.mkdir(parents=True)
    queue_path = queue_dir / "1-image-alt.json"
    queue_path.write_text(json.dumps(_queue_item()), encoding="utf-8")
    queue = _queue(tmp_path)
    queue.review(queue_path, "reject", reviewer="alice")

    with pytest.raises(ValueError, match="already reviewed"):
        queue.review(queue_path, "reject", reviewer="alice")


def test_review_invalid_decision_raises(tmp_path: Path) -> None:
    queue_dir = tmp_path / "hitl_queue"
    queue_dir.mkdir(parents=True)
    queue_path = queue_dir / "1-image-alt.json"
    queue_path.write_text(json.dumps(_queue_item()), encoding="utf-8")

    with pytest.raises(ValueError, match="decision must be"):
        _queue(tmp_path).review(queue_path, "maybe", reviewer="alice")  # type: ignore[arg-type]


def test_get_stats_counts_pending_and_reviewed(tmp_path: Path) -> None:
    queue_dir = tmp_path / "hitl_queue"
    queue_dir.mkdir(parents=True)
    (queue_dir / "1-a.json").write_text(json.dumps(_queue_item()), encoding="utf-8")
    (queue_dir / "2-b.json").write_text(json.dumps(_queue_item()), encoding="utf-8")
    queue = _queue(tmp_path)
    queue.review(queue_dir / "2-b.json", "reject", reviewer="alice")

    assert queue.get_stats() == {"pending": 1, "reviewed": 1, "total": 2}


def test_get_stats_empty_queue_dir(tmp_path: Path) -> None:
    assert _queue(tmp_path).get_stats() == {"pending": 0, "reviewed": 0, "total": 0}
