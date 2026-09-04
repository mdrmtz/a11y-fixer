"""Violation tracking and deduplication domain model.

Provides deterministic violation IDs, state machine for PR lifecycle,
and solution hash tracking for intelligent duplicate detection and replacement.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, asdict, field
from datetime import UTC, datetime
from enum import Enum
from typing import Optional


class ViolationState(str, Enum):
    """Lifecycle state of a violation in the system."""

    NEW = "NEW"  # No action taken yet
    HITL_QUEUED = "HITL_QUEUED"  # Escalated to the human review queue, awaiting a decision
    PR_OPEN = "PR_OPEN"  # Open PR exists for this violation
    BETTER_SOLUTION_READY = (
        "BETTER_SOLUTION_READY"  # New PR with higher score ready to replace
    )
    MERGED = "MERGED"  # PR merged into main
    WONT_FIX = "WONT_FIX"  # Human decision, don't attempt fix
    CLOSED_DUMMY = "CLOSED_DUMMY"  # Closed as test/dummy attempt
    CLOSED_CONFLICT = "CLOSED_CONFLICT"  # Closed due to merge conflict
    CLOSED_SUPERSEDED = "CLOSED_SUPERSEDED"  # Closed by better solution


def compute_violation_id(rule_id: str, selector: str) -> str:
    """Compute deterministic violation ID (independent of runs).

    Enables tracking same violation across multiple runs without
    relying on line numbers or timestamps.

    Args:
        rule_id: e.g., "image-alt", "color-contrast"
        selector: CSS selector, e.g., "img:nth-child(2)"

    Returns:
        First 12 chars of sha256 hash: "7fa3c2b8d1e9"
    """
    # Normalize selector: remove extra whitespace, sort compound selectors
    normalized = " ".join(selector.split())

    # Combine rule_id and selector with separator to avoid collisions
    combined = f"{rule_id}||{normalized}"

    # Hash and return first 12 chars (collision-resistant for practical use)
    hash_digest = hashlib.sha256(combined.encode("utf-8")).hexdigest()
    return hash_digest[:12]


@dataclass
class ViolationStatus:
    """Complete tracking record for a violation."""

    violation_id: str
    rule_id: str
    selector: str
    state: ViolationState

    # Current PR (if any)
    current_pr_number: Optional[int] = None
    current_score: Optional[float] = None
    current_solution_hash: str = ""

    # Best solution ever attempted
    best_solution_hash: str = ""
    best_score: float = 0.0

    # HITL queue tracking (deduplication)
    hitl_queue_path: Optional[str] = None  # Path to current queue entry
    hitl_queue_score: Optional[float] = None  # Score of queued solution

    # Timeline
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    closed_at: Optional[datetime] = None

    # Closure context
    close_reason: Optional[str] = None
    superseded_by_pr: Optional[int] = None

    def to_dict(self) -> dict:
        """Convert to serializable dict for JSON storage."""
        return {
            "violation_id": self.violation_id,
            "rule_id": self.rule_id,
            "selector": self.selector,
            "state": self.state.value,
            "current_pr_number": self.current_pr_number,
            "current_score": self.current_score,
            "current_solution_hash": self.current_solution_hash,
            "best_solution_hash": self.best_solution_hash,
            "best_score": self.best_score,
            "hitl_queue_path": self.hitl_queue_path,
            "hitl_queue_score": self.hitl_queue_score,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "closed_at": self.closed_at.isoformat() if self.closed_at else None,
            "close_reason": self.close_reason,
            "superseded_by_pr": self.superseded_by_pr,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ViolationStatus:
        """Deserialize from JSON storage."""
        # Handle missing/null datetime fields gracefully with defaults
        def parse_datetime(dt_str: str | None) -> datetime:
            if not dt_str:
                return datetime.now(UTC)
            return datetime.fromisoformat(dt_str)

        return cls(
            violation_id=data["violation_id"],
            rule_id=data["rule_id"],
            selector=data["selector"],
            state=ViolationState(data["state"]),
            current_pr_number=data.get("current_pr_number"),
            current_score=data.get("current_score"),
            current_solution_hash=data.get("current_solution_hash", ""),
            best_solution_hash=data.get("best_solution_hash", ""),
            best_score=data.get("best_score", 0.0),
            hitl_queue_path=data.get("hitl_queue_path"),
            hitl_queue_score=data.get("hitl_queue_score"),
            created_at=parse_datetime(data.get("created_at")),
            updated_at=parse_datetime(data.get("updated_at")),
            closed_at=(
                parse_datetime(data.get("closed_at"))
                if data.get("closed_at")
                else None
            ),
            close_reason=data.get("close_reason"),
            superseded_by_pr=data.get("superseded_by_pr"),
        )


@dataclass
class DuplicatePRInfo:
    """Info about a duplicate PR to close."""

    pr_number: int
    current_score: float
    reason: str  # "duplicate" or "superseded"
    new_pr_number: int  # The PR we're keeping/merging
