# Example: Self-Healing Observability for Phase 2

This document shows what Phase 2 observability logs would look like with self-healing inference enabled.

## Scenario

After Phase 2 run with Tier 1 instrumentation, we want to understand:

1. Why 0% clearance rate?
2. Which failures can be auto-retried?
3. What should we optimize first?
4. When should we escalate to human?

## Example Output from metrics_summary.all.json

```json
{
  "phase": "all",
  "timestamp": "2026-09-02T01:25:00+00:00",
  "schema_version": "1.0",

  "failure_analysis": {
    "by_category": {
      "code_generation": {
        "occurrence_count": 16,
        "percentage_of_failures": 0.727,
        "severity": "critical",
        "top_subcategories": [
          "missing_import",
          "syntax_error",
          "type_mismatch"
        ],
        "systemic_indicator": true,
        "remediation_focus": "Improve LLM code generation guardrails - add import validation"
      },

      "llm_reasoning": {
        "occurrence_count": 17,
        "percentage_of_failures": 0.773,
        "severity": "high",
        "top_subcategories": ["incomplete_solution", "incorrect_selector"],
        "systemic_indicator": true,
        "remediation_focus": "Better violation enumeration and multi-pass violation fixing"
      },

      "timeout": {
        "occurrence_count": 9,
        "percentage_of_failures": 0.409,
        "severity": "high",
        "systemic_indicator": true,
        "remediation_focus": "Implement caching and parallel execution"
      }
    },

    "cascade_failures": [
      {
        "trigger": "build_pass fails",
        "consequence": "wcag_compliance cannot be measured",
        "frequency": 0.818,
        "cases": 18,
        "implication": "FIX THIS FIRST - blocking 82% of all violations from being properly scored"
      },
      {
        "trigger": "llm_reasoning incomplete",
        "consequence": "visual_stability measurement skipped",
        "frequency": 0.545,
        "cases": 12,
        "implication": "Cannot measure regressions when partial fix prevents build"
      }
    ]
  },

  "optimization_recommendations": [
    {
      "rank": 1,
      "recommendation": "Fix build pass criterion first (blocking 82% of cascading failures)",
      "impact_on_clearance_rate": 0.45,
      "estimated_effort_hours": 4,
      "confidence": 0.92,
      "affected_cases": 18,
      "actions": [
        "Add import validation in code generation system prompt",
        "Implement AST-based syntax checking before build attempt",
        "Cache Angular dependency compilation to speed builds"
      ]
    },
    {
      "rank": 2,
      "recommendation": "Improve WCAG reasoning with multi-pass violation fixing",
      "impact_on_clearance_rate": 0.35,
      "estimated_effort_hours": 6,
      "confidence": 0.85,
      "affected_cases": 12,
      "actions": [
        "Enumerate all violations upfront as a checklist",
        "Split fixes by violation type (images, links, structure)",
        "Use larger LLM model (claude-opus) for complex multi-violation cases"
      ]
    },
    {
      "rank": 3,
      "recommendation": "Reduce latency with parallel execution",
      "impact_on_clearance_rate": 0.05,
      "estimated_effort_hours": 8,
      "confidence": 0.78,
      "affected_cases": 9,
      "actions": [
        "Implement build output caching",
        "Parallelize criterion scoring evaluation",
        "Use batch LLM inference where possible"
      ]
    }
  ],

  "self_healing_readiness": {
    "can_auto_retry": 0.59,
    "can_auto_fix_with_strategy": 0.82,
    "can_escalate_to_human": 1.0,
    "estimated_auto_fix_success_rate": 0.35,
    "manual_intervention_required": true,
    "human_expertise_needed": [
      "llm_prompting",
      "angular_codegen",
      "wcag_standards"
    ]
  }
}
```

## Example Output from scores_breakdown.all.json

### Case 1: Code Generation Issue (Retryable)

```json
{
  "case_id": "case-01",
  "rule": "html-has-lang",
  "page": "/",
  "rubric_score": 0.0,
  "cleared": false,
  "route": "human",
  "latency_seconds": 45.3,
  "error": null,

  "scoring_details": {
    "criteria_breakdown": [
      {
        "name": "Build Pass",
        "criterion_id": "build_pass",
        "max_points": 8.0,
        "awarded": 0.0,
        "passed": false,
        "severity": "critical",
        "reason": "ng build failed: Cannot find module 'CommonModule'",

        "root_cause": {
          "category": "code_generation",
          "subcategory": "missing_import",
          "confidence": 0.95,
          "error_pattern": "ImportError: No module named 'CommonModule'",
          "affected_file": "src/app.module.ts",
          "line_number": 12,
          "extract": "imports: [CommonModule, FormsModule]",
          "is_deterministic": false,
          "is_recoverable": true
        },

        "remediation": {
          "strategy": "regenerate_with_imports_focus",
          "priority": "high",
          "suggested_actions": [
            {
              "action": "retry_llm_generation",
              "parameters": {
                "system_prompt_hint": "Ensure ALL required Angular module imports are included. Check @angular/common, @angular/forms, @angular/platform-browser imports.",
                "temperature": 0.5,
                "max_tokens": 2000
              },
              "expected_success_rate": 0.75
            },
            {
              "action": "validate_against_import_audit",
              "parameters": {
                "check_type": "tsconfig_paths",
                "allow_missing": false
              },
              "expected_success_rate": 0.95
            },
            {
              "action": "use_larger_model",
              "parameters": {
                "model": "anthropic/claude-3-opus",
                "cost_multiplier": 3.0
              },
              "expected_success_rate": 0.88
            }
          ],
          "prevention": "Add Angular import checklist to code generation prompt"
        },

        "diagnostics": {
          "llm_attempt": 1,
          "llm_model": "anthropic/claude-3-haiku",
          "build_stderr_excerpt": "error TS2307: Cannot find module '@angular/common'",
          "similar_failures_in_phase": 9,
          "first_occurrence": "case-01",
          "last_occurrence": "case-22"
        }
      }
    ]
  }
}
```

**Self-Healing Decision: RETRY**

- `is_recoverable`: true
- `confidence`: 0.95 (high confidence in diagnosis)
- `suggested_actions[0].expected_success_rate`: 0.75 (>70% success likely)
- Action: Retry with improved prompt → if success_rate > 0.6, apply to all cases

---

### Case 2: LLM Reasoning Issue (Retryable but Complex)

```json
{
  "case_id": "case-06",
  "rule": "image-alt",
  "page": "/blog",
  "rubric_score": 2.5,
  "cleared": false,
  "route": "human",
  "latency_seconds": 109.7,
  "error": null,

  "scoring_details": {
    "criteria_breakdown": [
      {
        "name": "Build Pass",
        "criterion_id": "build_pass",
        "max_points": 8.0,
        "awarded": 8.0,
        "passed": true,
        "severity": "low",
        "reason": "Build succeeded"
      },
      {
        "name": "WCAG Compliance",
        "criterion_id": "wcag_compliance",
        "max_points": 5.0,
        "awarded": 2.5,
        "passed": false,
        "severity": "high",
        "reason": "LLM judge confidence: 50% - only fixed 2/4 violations",

        "root_cause": {
          "category": "llm_reasoning",
          "subcategory": "incomplete_solution",
          "confidence": 0.82,
          "error_pattern": "Partial fix - missed 2/4 image violations",
          "violation_count": {
            "original": 4,
            "fixed": 2,
            "remaining": 2,
            "coverage": 0.5
          },
          "is_deterministic": false,
          "is_recoverable": true,

          "root_factors": [
            {
              "factor": "insufficient_violation_enumeration",
              "weight": 0.5,
              "evidence": "Prompt did not list all violations upfront with visual context"
            },
            {
              "factor": "llm_context_window_limitation",
              "weight": 0.3,
              "evidence": "Last 2 violations appear below the fold in HTML"
            },
            {
              "factor": "model_capability",
              "weight": 0.2,
              "evidence": "Claude-3-Haiku may lack multi-violation reasoning"
            }
          ]
        },

        "remediation": {
          "strategy": "improved_violation_enumeration",
          "priority": "high",
          "suggested_actions": [
            {
              "action": "enumerate_violations_upfront",
              "parameters": {
                "format": "markdown_checklist",
                "detail_level": "full_visual_context",
                "include_line_numbers": true
              },
              "expected_success_rate": 0.72
            },
            {
              "action": "use_larger_model",
              "parameters": {
                "model": "anthropic/claude-3-opus",
                "cost_multiplier": 3.0
              },
              "expected_success_rate": 0.85
            },
            {
              "action": "split_by_violation_type",
              "parameters": {
                "strategy": "image_alts_first_then_other",
                "batch_size": 2
              },
              "expected_success_rate": 0.68
            }
          ],
          "prevention": "Implement violation enumeration in prompt template + use larger model for multi-violation cases"
        },

        "diagnostics": {
          "llm_attempt": 1,
          "llm_model": "anthropic/claude-3-haiku",
          "judge_confidence": 0.5,
          "violations_detected_by_axe": [
            "image-alt[0]",
            "image-alt[1]",
            "image-alt[2]",
            "image-alt[3]"
          ],
          "violations_in_solution": ["image-alt[0]", "image-alt[1]"],
          "solution_diff_lines": 45,
          "violations_per_fix_attempt": 2.0
        }
      }
    ]
  }
}
```

**Self-Healing Decision: ESCALATE (but with high retry potential)**

- `estimated_auto_fix_success`: ~72% (but requires model upgrade)
- Action: Escalate to human with recommendation: "Retry with Claude-Opus or split violations into 2 passes"

---

### Case 3: Timeout Issue (Non-Retryable Without Infrastructure Change)

```json
{
  "case_id": "case-08",
  "rule": "color-contrast",
  "page": "/pricing",
  "rubric_score": 0.0,
  "cleared": false,
  "route": "human",
  "latency_seconds": 125.3,
  "error": "LLM request timeout after 120s",

  "scoring_details": {
    "criteria_breakdown": [
      {
        "name": "Build Pass",
        "criterion_id": "build_pass",
        "max_points": 8.0,
        "awarded": 0.0,
        "passed": false,
        "severity": "critical",
        "reason": "LLM timeout - code generation never completed",

        "root_cause": {
          "category": "timeout",
          "subcategory": "llm_api_latency",
          "confidence": 0.98,
          "error_pattern": "Request timeout: OpenRouter backend exceeded 120s limit",
          "timeout_seconds": 125.3,
          "expected_duration_seconds": 8.0,
          "is_deterministic": false,
          "is_recoverable": true
        },

        "remediation": {
          "strategy": "reduce_context_size",
          "priority": "high",
          "suggested_actions": [
            {
              "action": "retry_with_shorter_context",
              "parameters": {
                "context_limit_tokens": 2000,
                "include_partial_solution_history": false
              },
              "expected_success_rate": 0.7
            },
            {
              "action": "use_faster_model",
              "parameters": {
                "model": "anthropic/claude-3-haiku",
                "tradeoff": "may have lower accuracy"
              },
              "expected_success_rate": 0.5
            },
            {
              "action": "implement_caching",
              "parameters": {
                "cache_level": "build_artifacts",
                "cache_ttl_minutes": 60
              },
              "expected_success_rate": 0.85
            }
          ],
          "prevention": "Implement request timeout handling and context size optimization"
        },

        "diagnostics": {
          "llm_attempt": 1,
          "llm_model": "anthropic/claude-3-haiku",
          "timeout_occurred_at": "code_generation_phase",
          "api_backend": "openrouter",
          "context_tokens_sent": 4200,
          "similar_timeouts_in_phase": 9
        }
      }
    ]
  }
}
```

**Self-Healing Decision: ESCALATE (infrastructure change needed)**

- `is_deterministic`: false (might succeed on retry due to random backend variance)
- `is_recoverable`: true (but requires infrastructure changes)
- Immediate action: Add exponential backoff retry
- Longer-term: Implement context caching and reduce prompt verbosity

---

## Self-Healing Decision Tree

```
┌─────────────────────────────────────────┐
│ Case Failed - Review Scoring Details    │
└────────────────┬────────────────────────┘
                 │
         ┌───────┴────────┐
         │                │
         v                v
    ERROR present?    root_cause?
         │                │
    ┌────┴────┐       ┌───┴────────┐
    │yes      │no     │yes         │no
    │         │       │            │
    v         │       v            v
 ESCALATE    │   Retryable?    SKIP
 to human    │       │         (no info)
    │        │   ┌───┴────┐
    │        │   │yes     │no
    │        │   │        │
    │        │   v        v
    │        │ RETRY   ESCALATE
    │        │ with     to human
    │        │ best     (analyze
    │        │ action   why?)
    │        │
    └────────┴────────────┘
             │
             v
          ESCALATE
```

---

## Example: Using Observability for Phase 3 Optimization

### Step 1: Parse Phase 2 Logs

```bash
cd cmu-capstone/agent
python -m a11y_fixer.observability
```

Output:

```
📊 Analyzing observability logs for self-healing...

🔄 Retryable cases: 13/22
   - case-01: 75.0% estimated success
   - case-03: 72.0% estimated success
   - case-06: 72.0% estimated success

⛓️  Cascade failures: 2
   Top blocker: build_pass fails → wcag_compliance cannot be measured (81.8% frequency)

📈 Failure patterns (3 categories):
   - code_generation: 16 (72.7%)
     ⚠️  Systemic issue - Improve LLM code generation guardrails
   - llm_reasoning: 17 (77.3%)
     ⚠️  Systemic issue - Better violation enumeration
   - timeout: 9 (40.9%)
     ⚠️  Systemic issue - Implement caching

🎯 Recommended optimizations (top 3 by impact):
   1. Fix build pass criterion first (blocking 82% of cascading failures)
      Impact: +45% clearance rate
      Effort: ~4h, Confidence: 92%
   2. Improve WCAG reasoning with multi-pass violation fixing
      Impact: +35% clearance rate
      Effort: ~6h, Confidence: 85%
   3. Reduce latency with parallel execution
      Impact: +5% clearance rate
      Effort: ~8h, Confidence: 78%

✅ Self-healing analysis complete
```

### Step 2: Implement Top Optimization

Focus on Recommendation #1: Fix build pass (affects 18/22 cases)

**Action 1**: Add import validation to code generation system prompt
**Action 2**: Implement AST syntax checking before build
**Action 3**: Cache Angular dependencies

### Step 3: Retry Affected Cases

```bash
# Pseudo-code
for case in affected_cases:
    if case.remediation.suggested_actions[0].expected_success_rate > 0.70:
        retry_case(case, action=suggested_actions[0])
```

### Step 4: Measure Impact

Compare new Phase 2 results to baseline:

- Clearance rate before: 0%
- Clearance rate after: ???
- Cases fixed: 13/18 (72% of affected cases)?

### Step 5: Update Observability

Log learnings in `wiki/lessons/Phase-3-Optimization-Results.md`:

- What actually worked
- What didn't (and why)
- Which optimization had best ROI
- Recommendations for Phase 4

---

## Self-Healing as Infrastructure

Once self-healing mechanisms are in place, the flow becomes:

```
1. Run Phase 2 benchmark
2. Generate observability logs with root causes
3. Parse logs → identify systemic issues
4. Auto-retry retryable cases
5. Escalate remaining cases to human
6. Human reviews, decides on optimizations
7. Implement optimizations
8. Measure impact in logs
9. Iterate: Phase 3 = implement optimizations
10. Iterate: Phase 4 = deploy with human loop
```

This is how **observability enables continuous improvement**.
