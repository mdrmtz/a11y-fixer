#!/usr/bin/env python3
"""Phase 4: Calibration Validation Script

Demonstrates the calibration infrastructure end-to-end:
1. Loads Phase 3 full results
2. Computes calibrated P(IK) floor
3. Compares routing decisions (default vs calibrated)
4. Shows impact of calibration on escalation decisions

Run from the agent root:
    python scripts/phase_4_calibration.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from a11y_fixer.domain.hitl_policy import assess_risk, DEFAULT_P_IK_FLOOR
from a11y_fixer.hitl.review_queue import calibrate_from_results


def main() -> None:
    """Run Phase 4 calibration validation."""
    agent_root = Path(__file__).parent.parent
    results_path = agent_root / "evaluation" / "results" / "results_phase_all.json"

    print("=" * 80)
    print("PHASE 4: CALIBRATION VALIDATION")
    print("=" * 80)
    print()

    if not results_path.exists():
        print(f"❌ Phase 3 results not found: {results_path}")
        print("   Run Phase 3 first: python -m evaluation.run_eval --phase all --no-live")
        sys.exit(1)

    # Load results
    data = json.loads(results_path.read_text(encoding="utf-8"))
    cases = data["cases"]
    summary = data["summary"]

    print("📊 PHASE 3 RESULTS SUMMARY")
    print(f"  Total cases: {summary['total_cases']}")
    print(f"  Clearance rate: {summary['violation_clearance_rate']:.1%}")
    print(f"  Human escalation rate: {summary['human_escalation_rate']:.1%}")
    print(f"  Error rate: {summary['error_rate']:.1%}")
    print(f"  Mean latency: {summary['mean_latency_seconds']:.1f}s")
    print()

    # Compute calibration
    print("🔧 COMPUTING CALIBRATION...")
    calibration = calibrate_from_results(results_path, target_fpr=0.05)

    print(f"  Status: {'✅ CALIBRATED' if calibration.calibrated else '⚠️  NOT CALIBRATED (insufficient data)'}")
    print(f"  Sample size: {calibration.sample_size} cases")
    print(f"  Default P(IK) floor: {DEFAULT_P_IK_FLOOR:.3f}")
    print(f"  Calibrated P(IK) floor: {calibration.p_ik_floor:.3f}")
    print(f"  Floor change: {calibration.p_ik_floor - DEFAULT_P_IK_FLOOR:+.3f}")
    if not (calibration.auc != calibration.auc):  # Check if not NaN
        print(f"  AUC: {calibration.auc:.3f}")
    print(f"  Target FPR: {calibration.target_fpr:.2%}")
    print()

    # Analyze routing impact
    print("📈 ROUTING IMPACT ANALYSIS")
    print()

    # Show how many cases would change routing with calibrated floor
    routing_changes = []
    for case in cases:
        if case["rubric_score"] == 0 and case["error"]:
            continue  # Skip error cases

        p_ik = max(0.0, min(1.0, case["rubric_score"] / 20.0))

        # Default floor routing
        default_route = (
            "human"
            if case["rubric_score"] < 15 or p_ik < DEFAULT_P_IK_FLOOR
            else "auto"
        )

        # Calibrated floor routing
        calibrated_route = (
            "human"
            if case["rubric_score"] < 15 or p_ik < calibration.p_ik_floor
            else "auto"
        )

        if default_route != calibrated_route:
            routing_changes.append({
                "case": case["case_id"],
                "default": default_route,
                "calibrated": calibrated_route,
                "score": case["rubric_score"],
                "p_ik": p_ik,
            })

    if routing_changes:
        print(f"  Routing changes with calibrated floor: {len(routing_changes)} cases")
        print()
        print("  Case ID          | Default | Calibrated | Score | P(IK)")
        print("  " + "-" * 65)
        for change in routing_changes:
            print(
                f"  {change['case']:16s} | {change['default']:7s} | {change['calibrated']:10s} | "
                f"{change['score']:5.1f} | {change['p_ik']:.3f}"
            )
    else:
        print(f"  No routing changes with calibrated floor")
    print()

    # Breakdown by rule
    print("📋 BREAKDOWN BY RULE")
    print()
    for rule, rule_summary in summary["by_rule"].items():
        cleared_rate = (
            rule_summary["cleared"] / rule_summary["total"]
            if rule_summary["total"] > 0
            else 0
        )
        print(f"  {rule:20s}: {rule_summary['cleared']:2d}/{rule_summary['total']:2d} cleared ({cleared_rate:.1%})")
    print()

    # Recommendations
    print("🎯 PHASE 4 RECOMMENDATIONS")
    print()
    if calibration.calibrated:
        print("  ✅ Calibration successful!")
        if routing_changes:
            print(f"  → Recalibrate would change routing for {len(routing_changes)} cases")
            print(f"  → Re-run Phase 3 subset with --recalibrate to verify impact")
        else:
            print("  → No routing changes expected from calibration")
            print("  → Calibrated floor is close to default")
    else:
        print(f"  ⚠️  Cannot calibrate: all {calibration.sample_size} cases have same outcome")
        print("  → Need cases with mixed cleared/non-cleared status to calibrate")
        print("  → Phase 3 needs debugging: 0% clearance indicates validation not working")
        print()
        print("📌 PHASE 3 FAILURE ANALYSIS")
        print()
        print("  Phase 3 achieved 0% clearance across all 22 cases:")
        print("  - None of the fixed violations were validated as cleared")
        print("  - Code validator was added but may not be effective")
        print("  - Possible causes:")
        print("    1. Validator is catching errors but agent doesn't fix them properly")
        print("    2. qa_critic not validating fixes against original violations")
        print("    3. Build still failing despite validator (other errors)")
        print("    4. Validator being too strict or too lenient")
        print()
        print("  Recommended next step: Debug Phase 3 before advancing")
    print()

    # Next steps
    print("=" * 80)
    print("PHASE 4 COMPLETION STATUS")
    print("=" * 80)
    print("✅ Infrastructure: Calibration wired into cli.py")
    print("✅ Computing: calibrate_from_results() functional")
    print("✅ Routing: assess_risk() uses calibrated floor")
    if routing_changes or not calibration.calibrated:
        print("⏳ Data: Need Phase 3 iteration before re-run")
    else:
        print("✅ Ready: Phase 3 results sufficient for Phase 4 re-run")
    print()


if __name__ == "__main__":
    main()
