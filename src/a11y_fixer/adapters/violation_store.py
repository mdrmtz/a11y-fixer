"""Violation status storage and pre-pipeline gate logic.

Manages .violation_status.json persistence and implements the decision matrix
for skip/create/replace actions.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Optional

from a11y_fixer.domain.violations import (
    ViolationState,
    ViolationStatus,
    compute_violation_id,
    DuplicatePRInfo,
)


class ViolationStore:
    """Persistent storage for violation tracking across runs."""

    def __init__(self, status_file: Path | None = None):
        """Initialize store.

        Args:
            status_file: Path to .violation_status.json. Defaults to repo root.
        """
        if status_file is None:
            # Find repo root by looking for .git
            current = Path.cwd()
            while current != current.parent:
                if (current / ".git").exists():
                    status_file = current / ".violation_status.json"
                    break
                current = current.parent
            else:
                # Fallback to current directory
                status_file = Path.cwd() / ".violation_status.json"

        self.status_file = status_file
        self._cache: dict[str, ViolationStatus] = {}
        self._load()

    def _load(self) -> None:
        """Load .violation_status.json from disk."""
        if not self.status_file.exists():
            self._cache = {}
            return

        try:
            with open(self.status_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                self._cache = {
                    vid: ViolationStatus.from_dict(vdata) for vid, vdata in data.items()
                }
        except (json.JSONDecodeError, ValueError) as e:
            print(f"Warning: Failed to load violation status: {e}")
            self._cache = {}

    def save(self) -> None:
        """Write .violation_status.json to disk."""
        self.status_file.parent.mkdir(parents=True, exist_ok=True)
        data = {vid: vs.to_dict() for vid, vs in self._cache.items()}
        with open(self.status_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def get(self, violation_id: str) -> ViolationStatus | None:
        """Retrieve violation status."""
        return self._cache.get(violation_id)

    def upsert(self, violation_status: ViolationStatus) -> None:
        """Add or update violation status."""
        self._cache[violation_status.violation_id] = violation_status

    def find_duplicate_prs(
        self, violation_id: str, kept_pr_number: int
    ) -> list[DuplicatePRInfo]:
        """Find all duplicate PRs for same violation to close.

        Args:
            violation_id: The violation we're tracking
            kept_pr_number: PR number we're keeping (highest score or newly merged)

        Returns:
            List of DuplicatePRInfo for PRs to close
        """
        # In a real implementation, this would query GitHub to find all PRs
        # with [violation-{violation_id}] in title. For now, we return
        # the structural type so integration can use it.
        return []

    def mark_merged(
        self,
        violation_id: str,
        rule_id: str,
        selector: str,
        pr_number: int | None = None,
    ) -> None:
        """Record violation as MERGED (template fix or PR).

        Used when html-lang fast-track applies successfully or when a PR is merged.
        Ensures downstream violations with same violation_id are skipped via
        PrePipelineGate deduplication.

        Args:
            violation_id: The violation being marked
            rule_id: e.g., "html-has-lang"
            selector: CSS selector, e.g., "html"
            pr_number: Optional PR number (if merged via PR)
        """
        status = ViolationStatus(
            violation_id=violation_id,
            rule_id=rule_id,
            selector=selector,
            current_pr_number=pr_number,
            current_score=20.0,  # Build-verified score
            current_solution_hash="",  # Empty string, not None
            state=ViolationState.MERGED,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        self.upsert(status)


class PrePipelineGate:
    """Decision logic: should we skip, create, or replace?"""

    # Score margin threshold: new solution must be at least this much better
    BETTER_SOLUTION_MARGIN = 1.5

    # Quality threshold: if score >= this, auto-merge when replacing
    AUTO_MERGE_THRESHOLD = 18.0

    def __init__(self, store: ViolationStore):
        self.store = store

    def should_process(
        self,
        rule_id: str,
        selector: str,
        new_score: float,
        new_solution_hash: str,
    ) -> tuple[str, str, Optional[int]]:
        """Determine action: CREATE, SKIP, or REPLACE.

        Args:
            rule_id: e.g., "image-alt"
            selector: CSS selector
            new_score: Rubric score of new solution (0-20)
            new_solution_hash: Hash of proposed solution code

        Returns:
            Tuple of (action, reason, old_pr_number_if_any)
            - action: "CREATE", "SKIP", or "REPLACE"
            - reason: Human-readable explanation
            - old_pr_number: PR to close if REPLACE, else None
        """
        violation_id = compute_violation_id(rule_id, selector)
        prior = self.store.get(violation_id)

        # Case 1: First time seeing this violation
        if prior is None:
            # If called with None values (pre-scoring), create placeholder
            # Will be updated with actual score/hash after processing
            status = ViolationStatus(
                violation_id=violation_id,
                rule_id=rule_id,
                selector=selector,
                state=ViolationState.NEW,
                current_score=new_score,
                current_solution_hash=new_solution_hash or "",
                best_solution_hash=new_solution_hash or "",
                best_score=new_score or 0.0,
            )
            self.store.upsert(status)
            self.store.save()
            return ("CREATE", "new_violation", None)

        # Case 2: Marked as WONT_FIX by human decision
        if prior.state == ViolationState.WONT_FIX:
            return ("SKIP", "marked_wont_fix_by_human", None)

        # Case 3: Already merged into main
        if prior.state == ViolationState.MERGED:
            return ("SKIP", "already_merged_to_main", prior.current_pr_number)

        # Case 4: Identical solution (same hash)
        if new_solution_hash is not None and new_solution_hash == prior.best_solution_hash:
            return ("SKIP", "identical_solution_exists", prior.current_pr_number)

        # Case 5: Open PR exists
        if prior.state == ViolationState.PR_OPEN:
            # If called with None values (pre-scoring check), skip to avoid re-processing
            if new_score is None or new_solution_hash is None:
                return (
                    "SKIP",
                    "existing_pr_already_open (awaiting review)",
                    prior.current_pr_number,
                )

            # Is new solution significantly better?
            if new_score > prior.current_score + self.BETTER_SOLUTION_MARGIN:
                # YES: Replace with better solution
                status = ViolationStatus(
                    violation_id=violation_id,
                    rule_id=rule_id,
                    selector=selector,
                    state=ViolationState.BETTER_SOLUTION_READY,
                    current_score=new_score,
                    current_solution_hash=new_solution_hash,
                    best_solution_hash=new_solution_hash,
                    best_score=new_score,
                    created_at=prior.created_at,
                    updated_at=prior.updated_at,
                )
                self.store.upsert(status)
                self.store.save()
                return (
                    "REPLACE",
                    f"better_solution_ready (new_score={new_score:.1f} vs old={prior.current_score:.1f})",
                    prior.current_pr_number,
                )
            else:
                # NO: Skip, existing PR is good enough
                return (
                    "SKIP",
                    f"existing_pr_adequate (new_score={new_score:.1f} vs old={prior.current_score:.1f})",
                    prior.current_pr_number,
                )

        # Case 6: Closed (for whatever reason)
        if prior.state in (
            ViolationState.CLOSED_DUMMY,
            ViolationState.CLOSED_CONFLICT,
            ViolationState.CLOSED_SUPERSEDED,
        ):
            # Try again with new solution
            status = ViolationStatus(
                violation_id=violation_id,
                rule_id=rule_id,
                selector=selector,
                state=ViolationState.PR_OPEN,
                current_score=new_score,
                current_solution_hash=new_solution_hash,
                best_solution_hash=new_solution_hash,
                best_score=new_score,
                created_at=prior.created_at,
                updated_at=prior.updated_at,
            )
            self.store.upsert(status)
            self.store.save()
            return (
                "CREATE",
                f"retry_closed_violation (prev_reason={prior.close_reason})",
                None,
            )

        # Case 7: Escalated to a human, awaiting review in the HITL queue.
        # Mirrors Case 5 (PR_OPEN): the pre-scoring check (`new_score`/
        # `new_solution_hash` still None, called before qa_critic even runs)
        # must recognize this as "already handled, don't reprocess" instead
        # of falling through to unknown_state_fallback - which is exactly
        # what happened before this state existed, since
        # `record_queue_entry()` had nowhere else to put the violation but
        # `NEW`. HITL_QUEUED entries don't populate `current_score` (only
        # `hitl_queue_score`), so compare against that instead.
        if prior.state == ViolationState.HITL_QUEUED:
            if new_score is None or new_solution_hash is None:
                return (
                    "SKIP",
                    "escalated_to_human_awaiting_review",
                    None,
                )

            queued_score = prior.hitl_queue_score or 0.0
            if new_score > queued_score + self.BETTER_SOLUTION_MARGIN:
                return (
                    "CREATE",
                    "better_solution_ready_for_escalated_violation "
                    f"(new_score={new_score:.1f} vs queued={queued_score:.1f})",
                    None,
                )
            return (
                "SKIP",
                "existing_hitl_queue_entry_adequate "
                f"(new_score={new_score:.1f} vs queued={queued_score:.1f})",
                None,
            )

        # Default: safety fallback
        return ("SKIP", "unknown_state_fallback", None)


class HITLQueueGate:
    """Deduplication logic for HITL queue entries, mirroring PrePipelineGate.

    Prevents duplicate escalations to the human review queue by tracking
    which violations have already been queued and managing replacements
    when better solutions are found.
    """

    # Score margin: new queued solution must be better by this much to replace
    BETTER_SOLUTION_MARGIN = 1.5

    def __init__(self, store: ViolationStore):
        """Initialize gate with access to violation store."""
        self.store = store

    def should_queue(
        self,
        rule_id: str,
        selector: str,
        score: float,
    ) -> tuple[str, str, Optional[str]]:
        """Determine action for HITL queue: ADD, SKIP, or REPLACE.

        Args:
            rule_id: e.g., "image-alt"
            selector: CSS selector
            score: Rubric score of solution (0-20)

        Returns:
            Tuple of (action, reason, path_to_old_entry_if_any)
            - action: "ADD", "SKIP", or "REPLACE"
            - reason: Human-readable explanation
            - old_path: Path to old queue entry to remove/replace, else None
        """
        violation_id = compute_violation_id(rule_id, selector)
        prior = self.store.get(violation_id)

        # Case 1: First time seeing this violation
        if prior is None:
            status = ViolationStatus(
                violation_id=violation_id,
                rule_id=rule_id,
                selector=selector,
                state=ViolationState.HITL_QUEUED,
                hitl_queue_score=score,
            )
            self.store.upsert(status)
            self.store.save()
            return ("ADD", "new_violation_escalating_to_human", None)

        # Case 2: Marked as WONT_FIX by human decision
        if prior.state == ViolationState.WONT_FIX:
            return ("SKIP", "marked_wont_fix_by_human", None)

        # Case 3: Already merged into main
        if prior.state == ViolationState.MERGED:
            return ("SKIP", "already_merged_to_main", None)

        # Case 4: No current queue entry - treat as new escalation
        if prior.hitl_queue_path is None or prior.hitl_queue_score is None:
            # Update score for comparison on next call
            status = prior.__class__(**{**prior.__dict__, "hitl_queue_score": score})
            self.store.upsert(status)
            self.store.save()
            return ("ADD", "new_escalation_for_known_violation", None)

        # Case 5: Identical score (same solution)
        if score == prior.hitl_queue_score:
            return (
                "SKIP",
                f"identical_solution_queued (score={score:.1f})",
                prior.hitl_queue_path,
            )

        # Case 6: Better solution available
        if score > prior.hitl_queue_score + self.BETTER_SOLUTION_MARGIN:
            old_path = prior.hitl_queue_path
            status = prior.__class__(**{**prior.__dict__, "hitl_queue_score": score})
            self.store.upsert(status)
            self.store.save()
            return (
                "REPLACE",
                f"better_solution_ready (new_score={score:.1f} vs old={prior.hitl_queue_score:.1f})",
                old_path,
            )

        # Case 7: Worse or marginally better solution
        return (
            "SKIP",
            f"existing_queue_entry_adequate (new_score={score:.1f} vs old={prior.hitl_queue_score:.1f})",
            prior.hitl_queue_path,
        )

    def record_queue_entry(
        self, rule_id: str, selector: str, queue_path: str, score: float
    ) -> None:
        """Record that a violation has been queued.

        Always (re)stamps `state=HITL_QUEUED` - this is the write path that
        makes an escalation visible to `PrePipelineGate.should_process()` on
        a later run, so it must not leave the prior state (often `NEW`)
        sitting there for `should_process()` to fall through on.
        """
        violation_id = compute_violation_id(rule_id, selector)
        prior = self.store.get(violation_id)

        if prior is None:
            status = ViolationStatus(
                violation_id=violation_id,
                rule_id=rule_id,
                selector=selector,
                state=ViolationState.HITL_QUEUED,
                hitl_queue_path=queue_path,
                hitl_queue_score=score,
            )
        else:
            status = prior.__class__(
                **{
                    **prior.__dict__,
                    "state": ViolationState.HITL_QUEUED,
                    "hitl_queue_path": queue_path,
                    "hitl_queue_score": score,
                }
            )

        self.store.upsert(status)
        self.store.save()

    def mark_reviewed(
        self, rule_id: str, selector: str, decision: str, reason: str = ""
    ) -> None:
        """Record human review decision: approve or reject."""
        violation_id = compute_violation_id(rule_id, selector)
        prior = self.store.get(violation_id)

        if prior is None:
            return  # No tracking data, nothing to mark

        if decision == "approve":
            new_state = ViolationState.MERGED
            close_reason = "approved_by_human_review"
        elif decision == "reject":
            new_state = ViolationState.WONT_FIX
            close_reason = f"rejected_by_human_review: {reason}"
        else:
            return  # Unknown decision

        status = prior.__class__(
            **{
                **prior.__dict__,
                "state": new_state,
                "close_reason": close_reason,
                "closed_at": datetime.now(UTC),
                "hitl_queue_path": None,  # Clear queue path after decision
            }
        )
        self.store.upsert(status)
        self.store.save()
