"""Tests for Phase 0.2: Violation tracking and intelligent deduplication."""

from __future__ import annotations

import json
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from a11y_fixer.adapters.violation_store import PrePipelineGate, ViolationStore
from a11y_fixer.domain.violations import (
    ViolationState,
    ViolationStatus,
    compute_violation_id,
)


class TestComputeViolationId:
    """Tests for deterministic violation ID generation."""

    def test_compute_violation_id_deterministic(self):
        """Same inputs always produce same ID."""
        id1 = compute_violation_id("image-alt", "img:nth-child(2)")
        id2 = compute_violation_id("image-alt", "img:nth-child(2)")

        assert id1 == id2
        assert len(id1) == 12
        assert all(c in "0123456789abcdef" for c in id1)

    def test_compute_violation_id_different_selectors(self):
        """Different selectors produce different IDs."""
        id1 = compute_violation_id("image-alt", "img:nth-child(2)")
        id2 = compute_violation_id("image-alt", "img:nth-child(3)")

        assert id1 != id2

    def test_compute_violation_id_different_rules(self):
        """Different rules produce different IDs."""
        id1 = compute_violation_id("image-alt", "img")
        id2 = compute_violation_id("color-contrast", "img")

        assert id1 != id2

    def test_compute_violation_id_whitespace_normalization(self):
        """Extra whitespace doesn't affect ID."""
        id1 = compute_violation_id("image-alt", "img:nth-child(2)")
        id2 = compute_violation_id("image-alt", "  img:nth-child(2)  ")

        assert id1 == id2


class TestViolationStore:
    """Tests for persistence and storage."""

    def test_violation_store_persistence(self):
        """Store saves and loads from disk."""
        with tempfile.TemporaryDirectory() as tmpdir:
            status_file = Path(tmpdir) / ".violation_status.json"

            # Create and save
            store1 = ViolationStore(status_file)
            status = ViolationStatus(
                violation_id="abc123",
                rule_id="image-alt",
                selector="img",
                state=ViolationState.PR_OPEN,
                current_pr_number=42,
                current_score=15.0,
            )
            store1.upsert(status)
            store1.save()

            # Load in new store
            store2 = ViolationStore(status_file)
            loaded = store2.get("abc123")

            assert loaded is not None
            assert loaded.violation_id == "abc123"
            assert loaded.current_pr_number == 42
            assert loaded.current_score == 15.0

    def test_violation_store_empty_when_no_file(self):
        """Empty store when file doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            status_file = Path(tmpdir) / ".violation_status.json"
            store = ViolationStore(status_file)

            assert store.get("nonexistent") is None

    def test_violation_status_serialization(self):
        """ViolationStatus can be serialized/deserialized."""
        status = ViolationStatus(
            violation_id="abc123",
            rule_id="image-alt",
            selector="img",
            state=ViolationState.MERGED,
            current_pr_number=10,
            current_score=18.5,
            best_score=18.5,
        )

        data = status.to_dict()
        restored = ViolationStatus.from_dict(data)

        assert restored.violation_id == status.violation_id
        assert restored.state == status.state
        assert restored.current_score == status.current_score


class TestPrePipelineGate:
    """Tests for skip/create/replace decision logic."""

    @pytest.fixture
    def store_and_gate(self):
        """Fixture providing a temporary store and gate."""
        with tempfile.TemporaryDirectory() as tmpdir:
            status_file = Path(tmpdir) / ".violation_status.json"
            store = ViolationStore(status_file)
            gate = PrePipelineGate(store)
            yield store, gate

    def test_create_new_violation(self, store_and_gate):
        """First-time violation returns CREATE."""
        store, gate = store_and_gate

        action, reason, old_pr = gate.should_process(
            "image-alt", "img", new_score=17.0, new_solution_hash="sol_abc123"
        )

        assert action == "CREATE"
        assert "new_violation" in reason
        assert old_pr is None

    def test_new_state_pre_scoring_recheck_allows_retry(self, store_and_gate):
        """A NEW placeholder (Case 1's own write, or left behind on
        purpose by a delivery that never went live - see cli.py's
        html-has-lang fast-track and case-11, 2026-09-04) must not fall
        through to unknown_state_fallback on the next pre-scoring check
        (new_score=None, new_solution_hash=None) - that call pattern
        skips Case 4's identical-hash check entirely, since it requires a
        real hash.
        """
        store, gate = store_and_gate

        action1, reason1, _ = gate.should_process(
            "html-has-lang", "html", new_score=None, new_solution_hash=None
        )
        assert action1 == "CREATE"
        assert reason1 == "new_violation"

        # Simulates a dry-run: the violation was left at NEW on purpose
        # (no mark_merged() call), then the same pre-scoring check runs
        # again on a later invocation.
        action2, reason2, old_pr = gate.should_process(
            "html-has-lang", "html", new_score=None, new_solution_hash=None
        )
        assert action2 == "CREATE"
        assert reason2 == "still_new_retry"
        assert old_pr is None

    def test_skip_identical_solution(self, store_and_gate):
        """Identical solution hash returns SKIP."""
        store, gate = store_and_gate

        # Create initial
        action1, _, _ = gate.should_process(
            "image-alt", "img", new_score=15.0, new_solution_hash="sol_abc"
        )
        assert action1 == "CREATE"

        # Attempt same solution again
        action2, reason, old_pr = gate.should_process(
            "image-alt", "img", new_score=15.0, new_solution_hash="sol_abc"
        )

        assert action2 == "SKIP"
        assert "identical_solution" in reason

    def test_skip_wont_fix(self, store_and_gate):
        """Marked WONT_FIX always returns SKIP."""
        store, gate = store_and_gate

        # Create initial
        gate.should_process("image-alt", "img", new_score=15.0, new_solution_hash="sol_abc")

        # Mark as WONT_FIX
        violation_id = compute_violation_id("image-alt", "img")
        status = store.get(violation_id)
        status.state = ViolationState.WONT_FIX
        store.upsert(status)
        store.save()

        # Try again with better solution
        action, reason, _ = gate.should_process(
            "image-alt", "img", new_score=19.0, new_solution_hash="sol_better"
        )

        assert action == "SKIP"
        assert "wont_fix" in reason

    def test_replace_when_score_significantly_better(self, store_and_gate):
        """Higher score by > margin returns REPLACE."""
        store, gate = store_and_gate

        # Create initial with low score
        action1, _, _ = gate.should_process(
            "image-alt", "img", new_score=15.0, new_solution_hash="sol_old"
        )
        assert action1 == "CREATE"

        # Update state to PR_OPEN (simulate PR creation)
        violation_id = compute_violation_id("image-alt", "img")
        status = store.get(violation_id)
        status.state = ViolationState.PR_OPEN
        status.current_pr_number = 42
        store.upsert(status)
        store.save()

        # Try with significantly better solution
        action2, reason, old_pr = gate.should_process(
            "image-alt", "img", new_score=19.0, new_solution_hash="sol_new"
        )

        assert action2 == "REPLACE"
        assert old_pr == 42
        assert "better_solution" in reason

    def test_skip_when_score_marginally_better(self, store_and_gate):
        """Marginal score improvement returns SKIP."""
        store, gate = store_and_gate

        # Create initial
        action1, _, _ = gate.should_process(
            "image-alt", "img", new_score=15.0, new_solution_hash="sol_old"
        )
        assert action1 == "CREATE"

        # Update to PR_OPEN
        violation_id = compute_violation_id("image-alt", "img")
        status = store.get(violation_id)
        status.state = ViolationState.PR_OPEN
        status.current_pr_number = 42
        store.upsert(status)
        store.save()

        # Try with marginal improvement (less than margin threshold)
        action2, reason, old_pr = gate.should_process(
            "image-alt", "img", new_score=16.0, new_solution_hash="sol_slightly_better"
        )

        assert action2 == "SKIP"
        assert old_pr == 42
        assert "adequate" in reason.lower()

    def test_skip_already_merged(self, store_and_gate):
        """Already merged violation returns SKIP."""
        store, gate = store_and_gate

        # Create and mark as merged
        gate.should_process("image-alt", "img", new_score=17.0, new_solution_hash="sol_abc")
        violation_id = compute_violation_id("image-alt", "img")
        status = store.get(violation_id)
        status.state = ViolationState.MERGED
        status.current_pr_number = 100
        store.upsert(status)
        store.save()

        # Try again
        action, reason, old_pr = gate.should_process(
            "image-alt", "img", new_score=18.0, new_solution_hash="sol_new"
        )

        assert action == "SKIP"
        assert "merged" in reason.lower()
        assert old_pr == 100

    def test_retry_closed_violation(self, store_and_gate):
        """Closed violation with new solution returns CREATE."""
        store, gate = store_and_gate

        # Create, then close as dummy
        gate.should_process("image-alt", "img", new_score=10.0, new_solution_hash="sol_poor")
        violation_id = compute_violation_id("image-alt", "img")
        status = store.get(violation_id)
        status.state = ViolationState.CLOSED_DUMMY
        status.close_reason = "test_attempt"
        store.upsert(status)
        store.save()

        # Try with new solution
        action, reason, old_pr = gate.should_process(
            "image-alt", "img", new_score=18.0, new_solution_hash="sol_good"
        )

        assert action == "CREATE"
        assert "retry_closed" in reason


class TestViolationStatusDataclass:
    """Tests for ViolationStatus behavior."""

    def test_default_timestamps(self):
        """ViolationStatus gets current timestamp by default."""
        status = ViolationStatus(
            violation_id="abc",
            rule_id="test",
            selector="sel",
            state=ViolationState.NEW,
        )

        assert status.created_at is not None
        assert status.updated_at is not None
        assert isinstance(status.created_at, datetime)

    def test_timestamps_close_to_now(self):
        """Created timestamp should be close to now."""
        before = datetime.now(UTC)
        status = ViolationStatus(
            violation_id="abc",
            rule_id="test",
            selector="sel",
            state=ViolationState.NEW,
        )
        after = datetime.now(UTC)

        assert before <= status.created_at <= after


class TestEndToEndScenario:
    """Integration tests simulating realistic scenarios."""

    def test_duplicate_detection_scenario(self):
        """Scenario: 8 identical duplicate PRs, 1 better solution."""
        with tempfile.TemporaryDirectory() as tmpdir:
            status_file = Path(tmpdir) / ".violation_status.json"
            store = ViolationStore(status_file)
            gate = PrePipelineGate(store)

            # Simulate PR #2-9: all fixing image-alt with same solution (score ~15.0)
            violation_id = compute_violation_id("image-alt", "img")

            # First run creates PR (represents PR #2)
            action1, reason1, _ = gate.should_process(
                "image-alt", "img", new_score=15.0, new_solution_hash="sol_v1"
            )
            assert action1 == "CREATE", f"PR #2 should CREATE, got {action1}"

            # Mark as PR_OPEN
            status = store.get(violation_id)
            status.state = ViolationState.PR_OPEN
            status.current_pr_number = 2
            store.upsert(status)
            store.save()

            # Second run, same solution (represents PR #3)
            action2, reason2, old_pr2 = gate.should_process(
                "image-alt", "img", new_score=15.0, new_solution_hash="sol_v1"
            )
            assert action2 == "SKIP", f"PR #3 should SKIP duplicate, got {action2}"
            assert old_pr2 == 2

            # Third run, slightly better (represents improved attempt)
            action3, reason3, old_pr3 = gate.should_process(
                "image-alt", "img", new_score=16.0, new_solution_hash="sol_v1_refined"
            )
            assert action3 == "SKIP", f"PR #3.5 should SKIP marginal improvement, got {action3}"
            assert old_pr3 == 2

            # New run with SIGNIFICANTLY better solution
            action4, reason4, old_pr4 = gate.should_process(
                "image-alt", "img", new_score=19.0, new_solution_hash="sol_v2_better"
            )
            assert action4 == "REPLACE", f"Better solution should REPLACE, got {action4}"
            assert old_pr4 == 2, f"Should reference PR #2 for replacement"

            # Verify state tracks best solution
            final_status = store.get(violation_id)
            assert final_status.best_score == 19.0
            assert final_status.best_solution_hash == "sol_v2_better"
