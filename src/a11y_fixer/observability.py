"""Observability log parser and self-healing inference engine.

This module provides tools to:
1. Parse observability logs in the standard schema (see observability/log/SCHEMA.md)
2. Extract root causes and remediation strategies
3. Make self-healing decisions (retry vs. escalate vs. fix)
4. Prioritize which optimizations to implement
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from a11y_fixer import config


@dataclass
class RootCause:
    """Parsed root cause classification."""

    category: str
    subcategory: str
    confidence: float
    is_deterministic: bool
    is_recoverable: bool

    def __post_init__(self) -> None:
        if not 0 <= self.confidence <= 1:
            raise ValueError(f"Confidence must be 0-1, got {self.confidence}")

    def is_retryable(self) -> bool:
        """Can this failure be safely retried?"""
        return (
            self.is_recoverable
            and self.confidence > 0.7  # High confidence in diagnosis
            and not self.is_deterministic  # Deterministic errors won't change on retry
        )


@dataclass
class RemediationAction:
    """A suggested remediation action with expected success rate."""

    action: str
    expected_success_rate: float
    parameters: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if not 0 <= self.expected_success_rate <= 1:
            raise ValueError(f"Success rate must be 0-1, got {self.expected_success_rate}")


@dataclass
class CriterionFailure:
    """A criterion failure with root cause and remediation."""

    criterion_id: str
    name: str
    severity: str
    passed: bool
    root_cause: RootCause | None = None
    remediation_strategy: str | None = None
    suggested_actions: list[RemediationAction] | None = None

    def best_action(self) -> RemediationAction | None:
        """Return the action with highest expected success rate."""
        if not self.suggested_actions:
            return None
        return max(self.suggested_actions, key=lambda a: a.expected_success_rate)


@dataclass
class CaseLog:
    """Parsed observability log for a single test case."""

    case_id: str
    rule: str
    page: str
    rubric_score: float
    cleared: bool
    latency_seconds: float
    error: str | None = None
    criterion_failures: list[CriterionFailure] | None = None

    def has_retryable_failures(self) -> bool:
        """Do any failures have retryable root causes?"""
        if not self.criterion_failures:
            return False
        return any(
            cf.root_cause and cf.root_cause.is_retryable()
            for cf in self.criterion_failures
        )

    def best_remediation(self) -> tuple[str, list[RemediationAction]] | None:
        """Get the most impactful remediation strategy and actions."""
        if not self.criterion_failures:
            return None

        # Find the most critical failure
        critical = next(
            (cf for cf in self.criterion_failures if cf.severity == "critical"),
            None,
        )
        if critical and critical.suggested_actions:
            return critical.remediation_strategy, critical.suggested_actions

        # Fall back to highest-severity failure
        high_severity = next(
            (cf for cf in self.criterion_failures if cf.severity == "high"),
            None,
        )
        if high_severity and high_severity.suggested_actions:
            return high_severity.remediation_strategy, high_severity.suggested_actions

        return None


class ObservabilityParser:
    """Parse observability logs in standard schema."""

    @staticmethod
    def load_scores_breakdown(log_file: Path) -> dict[str, Any]:
        """Load scores-breakdown-{phase}.json file."""
        with open(log_file, encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    def load_metrics_summary(log_file: Path) -> dict[str, Any]:
        """Load metrics-summary-{phase}.json file."""
        with open(log_file, encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    def parse_case_failures(case_data: dict[str, Any]) -> list[CriterionFailure]:
        """Extract criterion failures from a case."""
        failures = []
        scoring = case_data.get("scoring_details", {})

        for criterion in scoring.get("criteria_breakdown", []):
            root_cause_data = criterion.get("root_cause")
            root_cause = (
                RootCause(
                    category=root_cause_data["category"],
                    subcategory=root_cause_data.get("subcategory", "unknown"),
                    confidence=root_cause_data.get("confidence", 0.5),
                    is_deterministic=root_cause_data.get("is_deterministic", True),
                    is_recoverable=root_cause_data.get("is_recoverable", False),
                )
                if root_cause_data
                else None
            )

            remediation = criterion.get("remediation", {})
            actions = [
                RemediationAction(
                    action=a["action"],
                    expected_success_rate=a.get("expected_success_rate", 0.5),
                    parameters=a.get("parameters"),
                )
                for a in remediation.get("suggested_actions", [])
            ]

            failure = CriterionFailure(
                criterion_id=criterion.get("criterion_id", "unknown"),
                name=criterion["name"],
                severity=criterion.get("severity", "medium"),
                passed=criterion.get("passed", False),
                root_cause=root_cause,
                remediation_strategy=remediation.get("strategy"),
                suggested_actions=actions if actions else None,
            )
            failures.append(failure)

        return failures

    @classmethod
    def load_case_logs(cls, log_file: Path) -> list[CaseLog]:
        """Load all cases from scores_breakdown log."""
        data = cls.load_scores_breakdown(log_file)
        cases = []

        for case_data in data.get("cases", []):
            failures = cls.parse_case_failures(case_data)
            case = CaseLog(
                case_id=case_data["case_id"],
                rule=case_data["rule"],
                page=case_data["page"],
                rubric_score=case_data["rubric_score"],
                cleared=case_data["cleared"],
                latency_seconds=case_data["latency_seconds"],
                error=case_data.get("error"),
                criterion_failures=failures,
            )
            cases.append(case)

        return cases


class SelfHealingAnalyzer:
    """Analyze logs for self-healing decisions."""

    @staticmethod
    def identify_retryable_cases(cases: list[CaseLog]) -> list[CaseLog]:
        """Cases that can be safely retried."""
        return [c for c in cases if c.has_retryable_failures()]

    @staticmethod
    def estimate_retry_success_rate(case: CaseLog) -> float:
        """Estimate success rate if we retry this case."""
        if not case.criterion_failures:
            return 0.0

        retryable = [
            cf
            for cf in case.criterion_failures
            if cf.root_cause and cf.root_cause.is_retryable()
        ]
        if not retryable:
            return 0.0

        # Average expected success rate of best actions
        success_rates = []
        for cf in retryable:
            best = cf.best_action()
            if best:
                success_rates.append(best.expected_success_rate)

        return sum(success_rates) / len(success_rates) if success_rates else 0.0

    @staticmethod
    def should_escalate_to_human(case: CaseLog, success_threshold: float = 0.3) -> bool:
        """Decide if case should be escalated to human."""
        # Always escalate if we have errors
        if case.error is not None:
            return True

        # Escalate if retry success unlikely
        estimated_success = SelfHealingAnalyzer.estimate_retry_success_rate(case)
        return estimated_success < success_threshold

    @staticmethod
    def get_optimization_sequence(
        metrics_data: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Get ordered list of optimizations to implement."""
        recommendations = metrics_data.get("optimization_recommendations", [])
        return sorted(recommendations, key=lambda r: r.get("rank", 999))

    @staticmethod
    def analyze_cascade_failures(metrics_data: dict[str, Any]) -> dict[str, Any]:
        """Analyze failure dependencies."""
        cascades = metrics_data.get("cascade_failures", [])
        if not cascades:
            return {"total": 0, "blockages": []}

        # Sort by frequency to identify most common blockers
        sorted_cascades = sorted(cascades, key=lambda c: c.get("frequency", 0), reverse=True)

        return {
            "total": len(cascades),
            "blockages": sorted_cascades,
            "top_blocker": sorted_cascades[0] if sorted_cascades else None,
        }

    @staticmethod
    def summarize_failure_patterns(
        metrics_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Summarize which failure categories are most common."""
        by_category = metrics_data.get("failure_analysis", {}).get("by_category", {})
        if not by_category:
            return {}

        sorted_categories = sorted(
            by_category.items(),
            key=lambda item: item[1].get("occurrence_count", 0),
            reverse=True,
        )

        return {
            "total_categories": len(sorted_categories),
            "categories": [
                {
                    "name": name,
                    "count": data["occurrence_count"],
                    "percentage": data["percentage_of_failures"],
                    "is_systemic": data["systemic_indicator"],
                    "remediation_focus": data["remediation_focus"],
                }
                for name, data in sorted_categories
            ],
        }


def main() -> None:
    """Example: Analyze Phase 2 results for self-healing."""
    log_dir = config.agent_root() / "observability" / "log"
    scores_file = log_dir / "scores-breakdown-all.json"
    metrics_file = log_dir / "metrics-summary-all.json"

    if not scores_file.exists():
        print("❌ No Phase 2 observability logs found")
        print(f"   Expected: {scores_file}")
        return

    # Parse logs
    print("📊 Analyzing observability logs for self-healing...")
    parser = ObservabilityParser()
    cases = parser.load_case_logs(scores_file)
    metrics = parser.load_metrics_summary(metrics_file)

    # Self-healing analysis
    analyzer = SelfHealingAnalyzer()

    retryable = analyzer.identify_retryable_cases(cases)
    print(f"\n🔄 Retryable cases: {len(retryable)}/{len(cases)}")

    for case in retryable[:3]:  # Show first 3
        success_rate = analyzer.estimate_retry_success_rate(case)
        print(f"   - {case.case_id}: {success_rate:.1%} estimated success")

    # Cascade failures
    cascades = analyzer.analyze_cascade_failures(metrics)
    print(f"\n⛓️  Cascade failures: {cascades['total']}")
    if cascades["top_blocker"]:
        top = cascades["top_blocker"]
        print(f"   Top blocker: {top['trigger']} → {top['consequence']} ({top['frequency']:.1%} frequency)")

    # Failure patterns
    patterns = analyzer.summarize_failure_patterns(metrics)
    print(f"\n📈 Failure patterns ({patterns.get('total_categories', 0)} categories):")
    for category in patterns.get("categories", [])[:3]:
        print(f"   - {category['name']}: {category['count']} ({category['percentage']:.1%})")
        if category["is_systemic"]:
            print(f"     ⚠️  Systemic issue - {category['remediation_focus']}")

    # Optimization sequence
    optimizations = analyzer.get_optimization_sequence(metrics)
    print(f"\n🎯 Recommended optimizations (top 3 by impact):")
    for opt in optimizations[:3]:
        print(f"   {opt['rank']}. {opt['recommendation']}")
        print(f"      Impact: +{opt['impact_on_clearance_rate']:.1%} clearance rate")
        print(f"      Effort: ~{opt['estimated_effort_hours']}h, Confidence: {opt['confidence']:.0%}")

    print("\n✅ Self-healing analysis complete")


if __name__ == "__main__":
    main()
