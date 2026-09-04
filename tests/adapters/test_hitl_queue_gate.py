"""Tests for HITL queue deduplication gate."""

import pytest
from pathlib import Path
from tempfile import TemporaryDirectory

from a11y_fixer.adapters.violation_store import HITLQueueGate, PrePipelineGate, ViolationStore
from a11y_fixer.domain.violations import ViolationState, compute_violation_id


class TestHITLQueueGate:
    """Test HITLQueueGate deduplication logic."""

    def test_should_queue_new_violation(self):
        """First time seeing a violation should ADD to queue."""
        with TemporaryDirectory() as tmpdir:
            store_path = Path(tmpdir) / "violations.json"
            store = ViolationStore(status_file=store_path)
            gate = HITLQueueGate(store)

            action, reason, old_path = gate.should_queue(
                rule_id="image-alt",
                selector="img:nth-child(2)",
                score=15.0,
            )

            assert action == "ADD"
            assert old_path is None
            assert "new_violation" in reason

    def test_should_queue_new_violation_stamps_hitl_queued_state(self):
        """First-time escalation must land in HITL_QUEUED, not NEW - that's
        what lets PrePipelineGate.should_process() recognize it on a later
        run instead of falling through to unknown_state_fallback."""
        with TemporaryDirectory() as tmpdir:
            store_path = Path(tmpdir) / "violations.json"
            store = ViolationStore(status_file=store_path)
            gate = HITLQueueGate(store)

            gate.should_queue(rule_id="image-alt", selector="img:nth-child(2)", score=15.0)

            violation_id = compute_violation_id("image-alt", "img:nth-child(2)")
            assert store.get(violation_id).state == ViolationState.HITL_QUEUED

    def test_should_queue_identical_solution(self):
        """Same score should SKIP."""
        with TemporaryDirectory() as tmpdir:
            store_path = Path(tmpdir) / "violations.json"
            store = ViolationStore(status_file=store_path)
            gate = HITLQueueGate(store)

            # First call: ADD
            action1, _, _ = gate.should_queue(
                "image-alt", "img:nth-child(2)", score=15.0
            )
            assert action1 == "ADD"

            # Record the queue entry so it's tracked
            gate.record_queue_entry(
                "image-alt",
                "img:nth-child(2)",
                queue_path="/hitl_queue/1234567890-image-alt-img.json",
                score=15.0,
            )

            # Second call with same score: SKIP
            action, reason, old_path = gate.should_queue(
                rule_id="image-alt",
                selector="img:nth-child(2)",
                score=15.0,
            )

            assert action == "SKIP"
            assert "identical_solution" in reason

    def test_should_queue_better_solution(self):
        """Better score (by margin) should REPLACE."""
        with TemporaryDirectory() as tmpdir:
            store_path = Path(tmpdir) / "violations.json"
            store = ViolationStore(status_file=store_path)
            gate = HITLQueueGate(store)

            # First call: ADD at score 10
            action1, _, _ = gate.should_queue(
                "image-alt", "img:nth-child(2)", score=10.0
            )
            assert action1 == "ADD"

            # Record the queue entry
            gate.record_queue_entry(
                "image-alt",
                "img:nth-child(2)",
                queue_path="/hitl_queue/1234567890-image-alt-img.json",
                score=10.0,
            )

            # Second call with much better score: REPLACE (margin > 1.5)
            action, reason, old_path = gate.should_queue(
                rule_id="image-alt",
                selector="img:nth-child(2)",
                score=12.0,  # 2.0 points better
            )

            assert action == "REPLACE"
            assert "better_solution" in reason

    def test_should_queue_marginally_better_solution(self):
        """Slightly better score (below margin) should SKIP."""
        with TemporaryDirectory() as tmpdir:
            store_path = Path(tmpdir) / "violations.json"
            store = ViolationStore(status_file=store_path)
            gate = HITLQueueGate(store)

            # First call: ADD at score 10
            action1, _, _ = gate.should_queue(
                "image-alt", "img:nth-child(2)", score=10.0
            )
            assert action1 == "ADD"

            # Record the queue entry
            gate.record_queue_entry(
                "image-alt",
                "img:nth-child(2)",
                queue_path="/hitl_queue/1234567890-image-alt-img.json",
                score=10.0,
            )

            # Second call with marginally better score: SKIP (margin < 1.5)
            action, reason, old_path = gate.should_queue(
                rule_id="image-alt",
                selector="img:nth-child(2)",
                score=11.0,  # Only 1.0 point better
            )

            assert action == "SKIP"
            assert "existing_queue_entry_adequate" in reason

    def test_record_queue_entry(self):
        """Recording a queue entry should update violation status."""
        with TemporaryDirectory() as tmpdir:
            store_path = Path(tmpdir) / "violations.json"
            store = ViolationStore(status_file=store_path)
            gate = HITLQueueGate(store)

            queue_path = "/hitl_queue/1234567890-image-alt-img.json"
            gate.record_queue_entry(
                rule_id="image-alt",
                selector="img",
                queue_path=queue_path,
                score=16.0,
            )

            # Verify it's stored
            violation_id = compute_violation_id("image-alt", "img")
            status = store.get(violation_id)

            assert status is not None
            assert status.hitl_queue_path == queue_path
            assert status.hitl_queue_score == 16.0
            assert status.state == ViolationState.HITL_QUEUED

    def test_record_queue_entry_restamps_hitl_queued_over_prior_state(self):
        """record_queue_entry() must always set HITL_QUEUED, even when a
        prior (non-terminal) status already exists - it's the only write
        path that marks a violation as escalated, so it can't silently keep
        whatever state was there before (e.g. NEW)."""
        with TemporaryDirectory() as tmpdir:
            store_path = Path(tmpdir) / "violations.json"
            store = ViolationStore(status_file=store_path)
            gate = HITLQueueGate(store)

            gate.should_queue("image-alt", "img", score=10.0)
            gate.record_queue_entry("image-alt", "img", "/hitl_queue/1.json", 10.0)

            violation_id = compute_violation_id("image-alt", "img")
            assert store.get(violation_id).state == ViolationState.HITL_QUEUED

    def test_mark_reviewed_approve(self):
        """Approving a queued item should mark it as MERGED."""
        with TemporaryDirectory() as tmpdir:
            store_path = Path(tmpdir) / "violations.json"
            store = ViolationStore(status_file=store_path)
            gate = HITLQueueGate(store)

            # Queue an item first
            gate.record_queue_entry(
                rule_id="image-alt",
                selector="img",
                queue_path="/hitl_queue/123.json",
                score=16.0,
            )

            # Mark as approved
            gate.mark_reviewed("image-alt", "img", "approve")

            # Verify state changed
            violation_id = compute_violation_id("image-alt", "img")
            status = store.get(violation_id)

            assert status.state == ViolationState.MERGED
            assert status.hitl_queue_path is None  # Cleared
            assert "approved_by_human_review" in status.close_reason

    def test_mark_reviewed_reject(self):
        """Rejecting a queued item should mark it as WONT_FIX."""
        with TemporaryDirectory() as tmpdir:
            store_path = Path(tmpdir) / "violations.json"
            store = ViolationStore(status_file=store_path)
            gate = HITLQueueGate(store)

            # Queue an item first
            gate.record_queue_entry(
                rule_id="image-alt",
                selector="img",
                queue_path="/hitl_queue/123.json",
                score=16.0,
            )

            # Mark as rejected
            gate.mark_reviewed(
                "image-alt",
                "img",
                "reject",
                reason="potential false positive",
            )

            # Verify state changed
            violation_id = compute_violation_id("image-alt", "img")
            status = store.get(violation_id)

            assert status.state == ViolationState.WONT_FIX
            assert status.hitl_queue_path is None  # Cleared
            assert "rejected_by_human_review" in status.close_reason
            assert "potential false positive" in status.close_reason

    def test_skip_wont_fix_violation(self):
        """Should SKIP violations marked WONT_FIX by human."""
        with TemporaryDirectory() as tmpdir:
            store_path = Path(tmpdir) / "violations.json"
            store = ViolationStore(status_file=store_path)
            gate = HITLQueueGate(store)

            # First queue it
            gate.record_queue_entry(
                "image-alt",
                "img",
                queue_path="/hitl_queue/123.json",
                score=16.0,
            )

            # Mark as WONT_FIX (human rejection)
            gate.mark_reviewed("image-alt", "img", "reject")

            # Try to queue again
            action, reason, _ = gate.should_queue("image-alt", "img", score=18.0)

            assert action == "SKIP"
            assert "marked_wont_fix_by_human" in reason

    def test_multiple_different_violations(self):
        """Different violations should have independent queues."""
        with TemporaryDirectory() as tmpdir:
            store_path = Path(tmpdir) / "violations.json"
            store = ViolationStore(status_file=store_path)
            gate = HITLQueueGate(store)

            # Queue violation 1
            action1, _, _ = gate.should_queue("image-alt", "img:first", score=10.0)
            assert action1 == "ADD"
            gate.record_queue_entry(
                "image-alt", "img:first", "/hitl_queue/1.json", 10.0
            )

            # Queue violation 2 (different selector)
            action2, _, _ = gate.should_queue("image-alt", "img:last", score=10.0)
            assert action2 == "ADD"
            gate.record_queue_entry("image-alt", "img:last", "/hitl_queue/2.json", 10.0)

            # Queue violation 3 (different rule)
            action3, _, _ = gate.should_queue("color-contrast", "p", score=10.0)
            assert action3 == "ADD"
            gate.record_queue_entry("color-contrast", "p", "/hitl_queue/3.json", 10.0)

            # Try to queue violation 1 again with same score: SKIP
            action_dup, _, _ = gate.should_queue("image-alt", "img:first", score=10.0)
            assert action_dup == "SKIP"

            # Verify all are independent
            v1_id = compute_violation_id("image-alt", "img:first")
            v2_id = compute_violation_id("image-alt", "img:last")
            v3_id = compute_violation_id("color-contrast", "p")
            assert store.get(v1_id) is not None
            assert store.get(v2_id) is not None
            assert store.get(v3_id) is not None


class TestHitlQueuedRecognizedByPrePipelineGate:
    """Reproduces the real bug: a violation escalated to HITL via
    HITLQueueGate used to come back as `state=NEW`, so the very next run's
    `PrePipelineGate.should_process()` pre-scoring check (called with
    `new_score=None, new_solution_hash=None`, exactly as `cli.py`'s fleet/
    run loop does before qa_critic even runs) fell through every explicit
    case straight to `unknown_state_fallback` - a generic "skip, no idea
    why" instead of "already escalated, awaiting a human decision"."""

    def test_rerun_after_escalation_is_not_unknown_state_fallback(self):
        with TemporaryDirectory() as tmpdir:
            store = ViolationStore(status_file=Path(tmpdir) / "violations.json")
            queue_gate = HITLQueueGate(store)
            pipeline_gate = PrePipelineGate(store)

            # Run 1: violation is escalated to the HITL queue.
            queue_gate.should_queue("html-has-lang", "html", score=20.0)
            queue_gate.record_queue_entry(
                "html-has-lang", "html", "/hitl_queue/1.json", 20.0
            )

            # Run 2: cli.py's pre-scoring gate check on the same violation.
            action, reason, _ = pipeline_gate.should_process(
                "html-has-lang", "html", new_score=None, new_solution_hash=None
            )

            assert action == "SKIP"
            assert reason == "escalated_to_human_awaiting_review"
            assert reason != "unknown_state_fallback"

    def test_rerun_with_significantly_better_score_creates_a_new_attempt(self):
        with TemporaryDirectory() as tmpdir:
            store = ViolationStore(status_file=Path(tmpdir) / "violations.json")
            queue_gate = HITLQueueGate(store)
            pipeline_gate = PrePipelineGate(store)

            queue_gate.should_queue("image-alt", "img", score=10.0)
            queue_gate.record_queue_entry("image-alt", "img", "/hitl_queue/1.json", 10.0)

            action, reason, _ = pipeline_gate.should_process(
                "image-alt", "img", new_score=15.0, new_solution_hash="sol_better"
            )

            assert action == "CREATE"
            assert "better_solution_ready_for_escalated_violation" in reason

    def test_rerun_with_marginal_score_still_skips_as_adequate(self):
        with TemporaryDirectory() as tmpdir:
            store = ViolationStore(status_file=Path(tmpdir) / "violations.json")
            queue_gate = HITLQueueGate(store)
            pipeline_gate = PrePipelineGate(store)

            queue_gate.should_queue("image-alt", "img", score=10.0)
            queue_gate.record_queue_entry("image-alt", "img", "/hitl_queue/1.json", 10.0)

            action, reason, _ = pipeline_gate.should_process(
                "image-alt", "img", new_score=10.5, new_solution_hash="sol_marginal"
            )

            assert action == "SKIP"
            assert "existing_hitl_queue_entry_adequate" in reason
