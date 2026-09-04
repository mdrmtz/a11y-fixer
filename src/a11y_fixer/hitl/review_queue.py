"""HITL Bounded Decider: calibrated-threshold, ROC/AUC review queue.

Ports the `TrajectoryLogger -> Monitor -> ReviewQueue` pattern from Module-06
Lab 6.2 (`12-logging_observability_and_human_intervention_in_agent_systems`)
onto this project's own escalation queue - the same "a detector is a
calibrated classifier, not a yes/no oracle" idea, with the polarity flipped:
that lab flags HIGH suspicion as risky and calibrates its threshold against
BENIGN traffic; this domain flags LOW confidence (P(IK)) as risky and
calibrates its floor against historically-CLEARED cases (the closest
equivalent of "benign" - the ones a false escalation would waste review time
on), so a false-escalation budget can be set deliberately instead of using
the hardcoded defaults in `domain/hitl_policy.py` forever.

`domain/hitl_policy.assess_risk()` is the "Monitor" here - already wired into
`cli.py::deliver_violation()`. This module adds the two things the plan's
Phase E still lacked:
  - `roc_auc`/`roc_points`/`calibrate_p_ik_floor`/`calibrate_from_results`:
    pure classifier-quality metrics and threshold calibration over historical
    (confidence, cleared) pairs - unit-tested with synthetic data here, ready
    to consume a real `evaluation/results/results_summary.json` the moment
    one exists (falls back to the existing hardcoded default otherwise).
  - `ReviewQueue`: wraps the existing filesystem-backed `hitl_queue/`
    directory with `list_pending()`/`review()`/`get_stats()`. `review()` is
    the first real capture point for a human's actual approve/reject
    decision - reject calls `wiki_pipeline.ingest_lesson()` for real, and
    approve re-applies the persisted diff via `pr_delivery.deliver()`. Both
    were previously 100% unimplemented (the queue only ever recorded that a
    human SHOULD look, never what they decided).

Bug fix (2026-09-04): `review()` used to stop at `wiki_pipeline`/
`pr_delivery` and never touch `ViolationStore` at all - a human clicking
Approve (from the CLI or the HITL dashboard, which shells out to the same
`review --approve` command) delivered a real PR, sometimes even auto-merged
it, and `.violation_status.json` never heard about it. The entry stayed at
whatever state `PrePipelineGate` created it in when the violation was first
queued (always `NEW`), which made `PrePipelineGate.should_process()` fall
through to its `unknown_state_fallback` default on every later run instead
of correctly recognizing "already approved" or "already merged, don't
re-propose a fix". `review()` now updates the store on both approve
(`PR_OPEN`, upgraded to `MERGED` if auto-merge succeeds) and reject
(`WONT_FIX` - a state that existed in the schema from the start but that
nothing ever actually set).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from a11y_fixer import config
from a11y_fixer.adapters.pr import delivery as pr_delivery
from a11y_fixer.adapters.pr.github_pr_manager import GitHubPRManager
from a11y_fixer.adapters.retrieval import wiki_pipeline
from a11y_fixer.adapters.violation_store import ViolationStore
from a11y_fixer.domain.hitl_policy import DEFAULT_P_IK_FLOOR
from a11y_fixer.domain.violations import (
    ViolationState,
    ViolationStatus,
    compute_violation_id,
)

Decision = Literal["approve", "reject"]

DECISION_SUFFIX = ".decision.json"

# Same threshold `cli.py::deliver_violation()` uses for the fully-automated
# pipeline's auto-merge (Task 3.2) - kept in sync so a human clicking
# "Approve" from the HITL dashboard gets the same auto-merge behavior the
# dashboard's own confirm() dialog already promises ("auto-merges if score
# >= 18"), instead of that promise being dead UI copy.
AUTO_MERGE_THRESHOLD = 18.0


def roc_auc(labels: list[int], scores: list[float]) -> float:
    """AUC = P(score(positive) > score(negative)), ties counting half.

    `labels`: 1 for the class of interest (here: a case that did NOT clear
    on re-audit - i.e. one that should have been escalated), 0 otherwise.
    """
    pos = [s for label, s in zip(labels, scores, strict=True) if label == 1]
    neg = [s for label, s in zip(labels, scores, strict=True) if label == 0]
    if not pos or not neg:
        return float("nan")
    wins = sum((1.0 if p > n else 0.5 if p == n else 0.0) for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


def roc_points(labels: list[int], scores: list[float]) -> list[tuple[float, float]]:
    """(false-positive-rate, true-positive-rate) points across every threshold - for plotting."""
    positives = sum(1 for label in labels if label == 1)
    negatives = sum(1 for label in labels if label == 0)
    points = []
    for threshold in [min(scores) - 1, *sorted(set(scores)), max(scores) + 1]:
        true_pos = sum(
            1
            for label, score in zip(labels, scores, strict=True)
            if label == 1 and score >= threshold
        )
        false_pos = sum(
            1
            for label, score in zip(labels, scores, strict=True)
            if label == 0 and score >= threshold
        )
        points.append(
            (
                false_pos / negatives if negatives else 0.0,
                true_pos / positives if positives else 0.0,
            )
        )
    return sorted(points)


def calibrate_p_ik_floor(
    cleared_p_iks: list[float], *, target_fpr: float = 0.05
) -> float:
    """Highest P(IK) floor whose false-escalation rate on historically-CLEARED
    cases stays at or below `target_fpr` - the most protective floor the
    false-escalation budget affords.

    Mirrors Lab 6.2's `calibrate_threshold` with the polarity flipped: that
    lab picks the LOWEST (most sensitive) suspicion threshold within a
    false-positive budget on benign traffic (escalate ABOVE the threshold);
    escalating BELOW the floor instead means the equally-most-sensitive
    choice is the HIGHEST floor the budget allows, not the lowest.
    """
    cleared = sorted(cleared_p_iks)
    n = len(cleared)
    if n == 0:
        return DEFAULT_P_IK_FLOOR
    best_floor = 0.0
    for floor in [0.0, *(p + 0.001 for p in cleared)]:
        false_escalation_rate = sum(1 for p in cleared if p < floor) / n
        if false_escalation_rate > target_fpr:
            break  # `cleared` is sorted, so the rate is non-decreasing in floor - it never recovers
        best_floor = floor
    return round(min(best_floor, 1.0), 3)


@dataclass(frozen=True)
class Calibration:
    p_ik_floor: float
    target_fpr: float
    auc: float
    sample_size: int
    calibrated: bool


def calibrate_from_results(
    results_path: Path, *, target_fpr: float = 0.05
) -> Calibration:
    """Calibrate `p_ik_floor` from a real `run_eval.py` results_summary.json.

    Falls back to `hitl_policy.DEFAULT_P_IK_FLOOR` (uncalibrated) if the file
    doesn't exist yet, or holds too few cases / only one outcome class to
    calibrate meaningfully - running the real benchmark
    (`python -m evaluation.run_eval --phase all`) is a prerequisite this
    function does not perform itself.
    """
    if not results_path.exists():
        return Calibration(
            p_ik_floor=DEFAULT_P_IK_FLOOR,
            target_fpr=target_fpr,
            auc=float("nan"),
            sample_size=0,
            calibrated=False,
        )

    cases = json.loads(results_path.read_text(encoding="utf-8")).get("cases", [])
    p_iks = [max(0.0, min(1.0, case["rubric_score"] / 20.0)) for case in cases]
    not_cleared = [
        0 if case["cleared"] else 1 for case in cases
    ]  # escalate-worthy cases score 1, not 0

    if len(cases) < 2 or len(set(not_cleared)) < 2:  # noqa: PLR2004
        return Calibration(
            p_ik_floor=DEFAULT_P_IK_FLOOR,
            target_fpr=target_fpr,
            auc=float("nan"),
            sample_size=len(cases),
            calibrated=False,
        )

    cleared_p_iks = [
        p_ik for p_ik, case in zip(p_iks, cases, strict=True) if case["cleared"]
    ]
    floor = calibrate_p_ik_floor(cleared_p_iks, target_fpr=target_fpr)
    # Score as "1 - p_ik" so a higher score means "more likely should escalate", matching roc_auc's polarity.
    auc = roc_auc(not_cleared, [1.0 - p_ik for p_ik in p_iks])
    return Calibration(
        p_ik_floor=floor,
        target_fpr=target_fpr,
        auc=auc,
        sample_size=len(cases),
        calibrated=True,
    )


class ReviewQueue:
    """Wraps the filesystem-backed `hitl_queue/` directory: lists pending
    items and records a human's real approve/reject decision.
    """

    def __init__(
        self,
        queue_dir: Path,
        *,
        wiki_dir: Path,
        pr_config: config.PRDeliveryConfig,
        output_dir: Path,
        store: ViolationStore | None = None,
    ) -> None:
        self._queue_dir = queue_dir
        self._wiki_dir = wiki_dir
        self._pr_config = pr_config
        self._output_dir = output_dir
        # Defaults to the same real `.violation_status.json` every other
        # entry point uses (`cli.py`'s `_acmd_run`/`_check_merged_prs`) so a
        # decision made here is visible to them and vice versa. Tests (and
        # any other caller that wants isolation) pass their own `store`.
        self._store = store or ViolationStore(
            status_file=config.agent_root() / ".violation_status.json"
        )

    def _decision_path(self, queue_path: Path) -> Path:
        return queue_path.with_suffix(DECISION_SUFFIX)

    def list_pending(self) -> list[Path]:
        """Every un-reviewed queue item, oldest first (filenames are `time.time_ns()`-prefixed)."""
        if not self._queue_dir.exists():
            return []
        return sorted(
            path
            for path in self._queue_dir.glob("*.json")
            if not path.name.endswith(DECISION_SUFFIX)
            and not self._decision_path(path).exists()
        )

    def _record_reject(self, violation_id: str, violation: dict, notes: str) -> None:
        """Mark a rejected violation `WONT_FIX` so `PrePipelineGate` stops
        re-proposing a fix for it. `WONT_FIX` already existed in
        `ViolationState`/`should_process()`'s case 2 - nothing ever set it
        until now, so a rejected violation kept getting re-surfaced (and
        re-queued) on every subsequent run.
        """
        prior = self._store.get(violation_id)
        status = ViolationStatus(
            violation_id=violation_id,
            rule_id=violation["rule"],
            selector=violation["selector"],
            state=ViolationState.WONT_FIX,
            current_pr_number=prior.current_pr_number if prior else None,
            current_score=prior.current_score if prior else None,
            current_solution_hash=prior.current_solution_hash if prior else "",
            best_solution_hash=prior.best_solution_hash if prior else "",
            best_score=prior.best_score if prior else 0.0,
            hitl_queue_path=prior.hitl_queue_path if prior else None,
            hitl_queue_score=prior.hitl_queue_score if prior else None,
            created_at=prior.created_at if prior else datetime.now(UTC),
            updated_at=datetime.now(UTC),
            closed_at=datetime.now(UTC),
            close_reason=notes or "rejected by reviewer",
        )
        self._store.upsert(status)
        self._store.save()

    def _record_approve(
        self,
        violation_id: str,
        violation: dict,
        item: dict,
        delivery_result: object,
    ) -> None:
        """Mark an approved violation `PR_OPEN` - the queue-side mirror of
        `cli.py::deliver_violation()`'s own store update on the fully-
        automated path (see module docstring for why this was missing).
        """
        prior = self._store.get(violation_id)
        pr_number = (
            delivery_result.pull_request_number
            if isinstance(delivery_result, pr_delivery.LiveResult)
            else None
        )
        status = ViolationStatus(
            violation_id=violation_id,
            rule_id=violation["rule"],
            selector=violation["selector"],
            state=ViolationState.PR_OPEN,
            current_pr_number=pr_number,
            current_score=item.get("response", {}).get("score"),
            current_solution_hash=prior.current_solution_hash if prior else "",
            best_solution_hash=prior.best_solution_hash if prior else "",
            best_score=prior.best_score if prior else 0.0,
            hitl_queue_path=prior.hitl_queue_path if prior else None,
            hitl_queue_score=prior.hitl_queue_score if prior else None,
            created_at=prior.created_at if prior else datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        self._store.upsert(status)
        self._store.save()

    def review(
        self,
        queue_path: Path,
        decision: Decision,
        *,
        reviewer: str = "cli",
        notes: str = "",
    ) -> dict:
        """Record a human's real decision on one queued item.

        Reject: files the rejection as an institutional-memory lesson via
        `wiki_pipeline.ingest_lesson()`, and marks the violation `WONT_FIX`
        in the violation store. Approve: re-applies the persisted diff,
        delivers it via `pr_delivery.deliver()`, marks the violation
        `PR_OPEN` (or `MERGED` if auto-merge succeeds), and auto-merges when
        the score clears `AUTO_MERGE_THRESHOLD`.
        """
        if self._decision_path(queue_path).exists():
            msg = f"{queue_path} was already reviewed"
            raise ValueError(msg)

        item = json.loads(queue_path.read_text(encoding="utf-8"))
        violation = item["violation"]
        violation_id = compute_violation_id(violation["rule"], violation["selector"])
        result: dict[str, Any] = {"decision": decision, "reviewer": reviewer}

        if decision == "reject":
            changes = item.get("changes", [])
            file_path = (
                changes[0]["path"] if changes else violation.get("selector", "unknown")
            )
            lesson = wiki_pipeline.ingest_lesson(
                self._wiki_dir,
                rule=violation["rule"],
                file_path=file_path,
                rejection_reason=notes or "rejected without a stated reason",
                constraint=notes,
            )
            result["lesson_id"] = lesson.id
            self._record_reject(violation_id, violation, notes)
        elif decision == "approve":
            changes = [
                pr_delivery.FileChange(**change) for change in item.get("changes", [])
            ]
            if not changes:
                result["delivered"] = False
                result["reason"] = "no persisted file changes to deliver"
            else:
                plan = pr_delivery.PullRequestPlan(
                    title=f"a11y-fixer: fix {violation['rule']} ({violation['selector']})",
                    body=item["response"]["rationale"],
                    branch_name=f"a11y-fixer/{violation['rule']}-{int(time.time())}",
                    changes=changes,
                )
                result["delivered"] = True
                delivery_result = pr_delivery.deliver(
                    plan, config=self._pr_config, output_dir=self._output_dir
                )
                result["result"] = vars(delivery_result)
                self._record_approve(violation_id, violation, item, delivery_result)

                # Auto-merge, mirroring cli.py::deliver_violation()'s Task 3.2-3.3
                # (only possible once a real PR exists, i.e. live delivery succeeded).
                if (
                    self._pr_config.live
                    and self._pr_config.github_token
                    and self._pr_config.github_repo
                    and isinstance(delivery_result, pr_delivery.LiveResult)
                ):
                    score = item.get("response", {}).get("score", 0)
                    pr_number = delivery_result.pull_request_number
                    try:
                        pr_mgr = GitHubPRManager(
                            github_token=self._pr_config.github_token,
                            github_repo=self._pr_config.github_repo,
                        )
                        merge_result = pr_mgr.auto_merge_pr(
                            pr_number, score, merge_threshold=AUTO_MERGE_THRESHOLD
                        )
                        result["auto_merge"] = vars(merge_result)

                        if merge_result.success:
                            self._store.mark_merged(
                                violation_id,
                                violation["rule"],
                                violation["selector"],
                                pr_number,
                            )
                            self._store.save()

                            dup_results = pr_mgr.cleanup_duplicate_prs(
                                violation_id, kept_pr_number=pr_number
                            )
                            if dup_results:
                                result["duplicate_cleanup"] = [
                                    vars(dup) for dup in dup_results
                                ]
                    except Exception as exc:  # noqa: BLE001
                        result["auto_merge"] = {
                            "success": False,
                            "pr_number": pr_number,
                            "reason": f"error: {exc}",
                        }
        else:
            msg = f"decision must be 'approve' or 'reject', got {decision!r}"
            raise ValueError(msg)

        self._decision_path(queue_path).write_text(
            json.dumps(
                {
                    **result,
                    "notes": notes,
                    "reviewed_at": datetime.now(UTC).isoformat(),
                },
                default=str,
                indent=2,
            ),
            encoding="utf-8",
        )
        return result

    def get_stats(self) -> dict:
        if not self._queue_dir.exists():
            return {"pending": 0, "reviewed": 0, "total": 0}
        items = [
            path
            for path in self._queue_dir.glob("*.json")
            if not path.name.endswith(DECISION_SUFFIX)
        ]
        pending = sum(1 for path in items if not self._decision_path(path).exists())
        return {
            "pending": pending,
            "reviewed": len(items) - pending,
            "total": len(items),
        }
