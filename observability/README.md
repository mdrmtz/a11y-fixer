# Observability Logs

This directory contains detailed observability logs generated during benchmark runs and evaluation phases. Logs are structured for **both human readability and automated self-healing inference**.

## Directory Structure

```
observability/
├── log/                          # Evaluation run logs
│   ├── scores-breakdown-all.json        # Per-criterion scoring details (Tier 1)
│   ├── metrics-summary-all.json         # Calibration metrics + self-healing recommendations
│   ├── timing-profile-all.json          # Latency breakdown by component (Tier 2)
│   └── audit-trail-violations.json      # Violation lifecycle events (Tier 3)
├── README.md                     # This file
└── log/
    ├── SCHEMA.md                 # Machine-parseable schema for self-healing
```

**⭐ log/SCHEMA.md** - Read this first if building a self-healing mechanism. It documents:
- Root cause classification taxonomy
- Remediation strategy structure
- Cascade failure tracking
- Self-healing readiness assessment
- Example inference rules for auto-retry and escalation

## Log Files

### scores-breakdown-{phase}.json (Tier 1 - CRITICAL)

**Purpose**: Per-criterion scoring breakdown for each test case.

Filename examples:
- `scores-breakdown-all.json` - All benchmark cases
- `scores-breakdown-2.json` - Phase 2 cases only
- `scores-breakdown-custom.json` - Custom case range

**Content:**
```json
{
  "phase": "all",
  "timestamp": "2026-09-02T01:25:00+00:00",
  "total_cases": 22,
  "cases": [
    {
      "case_id": "case-06",
      "rule": "image-alt",
      "page": "/case-studies",
      "rubric_score": 0.0,
      "cleared": false,
      "route": "human",
      "latency_seconds": 109.7,
      "error": null,
      "scoring_details": {
        "criteria_breakdown": [
          {
            "name": "Build Pass",
            "max_points": 8.0,
            "awarded": 0.0,
            "passed": false,
            "reason": "Missing import statement in generated code"
          },
          {
            "name": "AST Valid",
            "max_points": 4.0,
            "awarded": 0.0,
            "passed": false,
            "reason": "Unclosed div tag in template"
          },
          {
            "name": "WCAG Compliance",
            "max_points": 5.0,
            "awarded": 0.0,
            "passed": false,
            "reason": "LLM judge confidence: 15.0% - only fixed 1/3 missing images"
          },
          {
            "name": "Visual Stability",
            "max_points": 3.0,
            "awarded": 0.0,
            "passed": false,
            "reason": "Not measured (no screenshots captured)"
          }
        ]
      }
    }
  ]
}
```

**How to use:**
- Identify which criteria consistently fail across cases
- Understand if bottleneck is build (compilation), AST (syntax), WCAG (logic), or visual (regression)
- Prioritize fixes (e.g., "fix build issues first" vs. "improve prompts for WCAG reasoning")

**Analysis:**
```bash
# Show all failures by criterion across all cases
jq '.cases[].scoring_details.criteria_breakdown[] | select(.passed == false) | .name' observability/log/scores-breakdown-all.json | sort | uniq -c

# Show cases with build failures
jq '.cases[] | select(.scoring_details.criteria_breakdown[0].passed == false)' observability/log/scores-breakdown-all.json
```

---

### metrics-summary-{phase}.json (Tier 1+)

**Purpose**: Phase-level metrics with failure patterns for self-healing prioritization.

**Content:**
```json
{
  "phase": "all",
  "timestamp": "2026-09-02T01:25:00+00:00",
  "summary": {
    "total_cases": 22,
    "violation_clearance_rate": 0.0,
    "human_escalation_rate": 0.409,
    "error_rate": 0.409,
    "mean_latency_seconds": 63.6,
    "brier_score": 0.0755,
    "expected_calibration_error": 0.129,
    "by_rule": { ... }
  },
  "cases_by_route": {
    "auto": 0,
    "human": 22
  },
  "cases_by_error": {
    "cleared": 0,
    "errored": 9,
    "pending_review": 13
  }
}
```

**How to use:**
- Verify calibration quality (Brier score, ECE)
- Check routing decisions (auto vs. human escalation)
- Track phase-to-phase improvement

---

### timing-profile-{phase}.json (Tier 2 - Coming in Phase 3)

**Purpose:** Latency breakdown by component (LLM inference, build, validation, scoring).

**Usage:** Identify which component is the bottleneck for optimization.

---

### audit_trail_violations.json (Tier 3 - Coming in Phase 4)

**Purpose:** Full lifecycle events for each violation (generated, PR opened, human reviewed, regenerated, merged).

**Usage:** Trace why decisions were made, measure human review turnaround, track iteration history.

---

## Integration with Phase Progression

### Phase 2 (Current) - Focus: Logging Infrastructure
- ✅ Tier 1: Scoring breakdown implemented
- 📝 Metrics summary: Tracks calibration quality
- ❌ Tier 2: Latency profiling (not yet)
- ❌ Tier 3: Audit trail (not yet)

### Phase 3 (Next) - Focus: Optimization
- ✅ Use Tier 1 data to identify failing criteria
- 📝 Implement Tier 2 to profile optimization impact
- 📝 Compare metrics_summary across iterations

### Phase 4 (Future) - Focus: Production Deployment
- ✅ Use Tier 3 audit trail for HITL workflow
- 📝 Set HITL thresholds based on Brier score / ECE from Phase 2
- 📝 Monitor human review turnaround times

---

## How Logs Are Generated

Logs are automatically generated by the evaluation harness (`evaluation/run_eval.py`) after each benchmark run:

```bash
# Run a benchmark phase
python -m evaluation.run_eval --phase all --no-live

# Logs will be created in:
#   observability/log/scores-breakdown-all.json
#   observability/log/metrics-summary-all.json
```

The harness calls `_save_observability_logs()` which:
1. Extracts scoring details from each `CaseResult`
2. Computes phase-level metrics
3. Saves to `observability/log/` with ISO 8601 timestamps
4. Prints confirmation messages

---

## Accessing Logs Programmatically

```python
import json
from pathlib import Path

log_file = Path("observability/log/scores-breakdown-all.json")
data = json.loads(log_file.read_text())

# Find all cases with build failures
build_failures = [
    case for case in data["cases"]
    if case.get("scoring_details", {}).get("criteria_breakdown", [])[0].get("passed") == False
]

# Aggregate failure reasons
reasons = {}
for case in build_failures:
    reason = case["scoring_details"]["criteria_breakdown"][0]["reason"]
    reasons[reason] = reasons.get(reason, 0) + 1

print("Build failure reasons:")
for reason, count in sorted(reasons.items(), key=lambda x: -x[1]):
    print(f"  {count}x: {reason}")
```

---

## Storage & Retention

- **Location:** `cmu-capstone/agent/observability/log/`
- **Format:** JSON (human-readable, machine-parseable)
- **Naming:** `{log_type}_phase_{phase_name}.json`
- **Retention:** Keep all logs for historical comparison across phases
- **Git:** Committed to repo (not in `.gitignore`)

---

## Self-Healing Inference (Coming in Phase 3)

The logs are structured to enable **automated self-healing mechanisms**. Key features:

### Root Cause Classification

Each failure includes structured root cause data:
```json
"root_cause": {
  "category": "code_generation|llm_reasoning|timeout|instrumentation",
  "subcategory": "missing_import|incomplete_solution|syntax_error",
  "confidence": 0.95,
  "is_deterministic": true,
  "is_recoverable": true
}
```

**Self-healing use**: Classify failures automatically → route to appropriate fixer → decide if safe to retry

### Remediation Strategies

Each criterion failure includes ranked suggested actions:
```json
"remediation": {
  "strategy": "improved_llm_prompting|regenerate_with_imports_focus|enable_measurement",
  "suggested_actions": [
    {
      "action": "retry_llm_generation",
      "expected_success_rate": 0.75
    },
    {
      "action": "use_larger_model",
      "expected_success_rate": 0.88
    }
  ]
}
```

**Self-healing use**: Execute highest-success-rate action first → measure impact → iterate

### Cascade Failure Detection

Metrics summary includes dependency analysis:
```json
"cascade_failures": [
  {
    "trigger": "build_pass fails",
    "consequence": "wcag_compliance cannot be measured",
    "frequency": 0.818,
    "implication": "Fix build issues first before optimizing WCAG detection"
  }
]
```

**Self-healing use**: Identify which criterion to fix first for maximum impact

### Optimization Recommendations

Phase-level metrics include prioritized actions:
```json
"optimization_recommendations": [
  {
    "rank": 1,
    "recommendation": "Fix build pass criterion first (blocking 82% of cascading failures)",
    "impact_on_clearance_rate": 0.45,
    "affected_cases": 18,
    "confidence": 0.92
  }
]
```

**Self-healing use**: Execute recommendations in rank order → measure clearance rate improvement

### Self-Healing Readiness Assessment

Metrics include a feasibility check:
```json
"self_healing_readiness": {
  "can_auto_retry": 0.59,
  "can_auto_fix_with_strategy": 0.82,
  "can_escalate_to_human": 1.0,
  "estimated_auto_fix_success_rate": 0.35,
  "manual_intervention_required": true
}
```

**Self-healing use**: Know when to escalate to human vs. auto-fix

---

## Querying Logs for Self-Healing Insights

### Find All Retryable Failures

```bash
jq '.cases[] | select(.scoring_details.root_cause.is_recoverable == true) | {case_id: .case_id, strategy: .scoring_details.remediation.strategy}' observability/log/scores-breakdown-all.json
```

### Identify Systemic Issues

```bash
jq '.failure_analysis.by_category | to_entries[] | select(.value.systemic_indicator == true) | {category: .key, occurrence_count: .value.occurrence_count, remediation_focus: .value.remediation_focus}' observability/log/metrics-summary-all.json
```

### Get Top Optimization Actions

```bash
jq '.optimization_recommendations | sort_by(.rank) | .[0:3] | .[] | {rank: .rank, recommendation: .recommendation, impact: .impact_on_clearance_rate}' observability/log/metrics-summary-all.json
```

### Check Cascade Dependencies

```bash
jq '.cascade_failures[] | {trigger: .trigger, consequence: .consequence, frequency: .frequency, implication: .implication}' observability/log/metrics-summary-all.json
```

---

## Next Steps for Self-Healing Implementation

### Phase 2 (Current)
- ✅ Tier 1 logs generated with root cause classification
- 📝 Human manually reviews suggested remediation strategies
- 📝 Choose top 1-2 optimizations to implement

### Phase 3 (Optimization)
- 📝 Tier 2 logs capture timing profiles
- 📝 Implement recommended strategies, measure impact
- 📝 Create retry loop for retryable failures
- 📝 Build self-healing CLI: `python -m a11y_fixer.self_heal --target-rate 0.5`

### Phase 4 (Production)
- 📝 Tier 3 logs track violation lifecycle
- 📝 Deploy self-healing agent to monitor live runs
- 📝 Auto-retry on transient failures
- 📝 Escalate to human when success unlikely
- 📝 Feedback loop: update remediation strategies based on real outcomes

---

1. **Run Phase 2 with Tier 1 logging enabled**
   ```bash
   cd cmu-capstone/agent
   python -m evaluation.run_eval --phase all --no-live
   ```

2. **Analyze scoring breakdown to identify top improvements**
   ```bash
   jq '.cases[].scoring_details.criteria_breakdown[] | select(.passed == false) | .name' observability/log/scores-breakdown-all.json | sort | uniq -c
   ```

3. **Use metrics summary to validate calibration**
   - Brier score should be < 0.1 (good calibration)
   - ECE should be < 0.15 (predictions match reality)

4. **During Phase 3, add Tier 2 timing profiles** to understand latency bottlenecks

5. **Before Phase 4, add Tier 3 audit trail** for human-in-the-loop tracking
