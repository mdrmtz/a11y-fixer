# Observability Log Schema v1.0
## Self-Healing Inference Format

This document defines the machine-parseable schema for observability logs designed to support automated self-healing mechanisms.

---

## Design Principles

1. **Actionability**: Every failure includes root cause classification and suggested remediation
2. **Causality**: Track failure dependencies (does X failure cause Y failure?)
3. **Patterns**: Aggregate failure reasons to identify systemic issues
4. **Severity**: Distinguish between terminal failures and transient issues
5. **Context**: Capture full diagnostic context for replay/debugging
6. **Confidence**: Machine learning models need confidence/uncertainty scores

---

## Log File Structure

### 1. scores-breakdown-{phase}.json (Tier 1 - CRITICAL)

**Purpose**: Per-criterion scoring with root cause classification for self-healing.

Filename examples: `scores-breakdown-all.json`, `scores-breakdown-2.json`, `scores-breakdown-3.json`

```json
{
  "phase": "all",
  "timestamp": "2026-09-02T01:25:00+00:00",
  "total_cases": 22,
  "schema_version": "1.0",
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
        "total_awarded": 0.0,
        "max_total": 20.0,
        "visual_stability_measured": false,
        "criteria_breakdown": [
          {
            "name": "Build Pass",
            "criterion_id": "build_pass",
            "max_points": 8.0,
            "awarded": 0.0,
            "passed": false,
            "severity": "critical",
            "reason": "Missing import statement in generated code",
            
            "root_cause": {
              "category": "code_generation",
              "subcategory": "missing_import",
              "confidence": 0.95,
              "error_pattern": "ImportError: No module named 'HtmlModule'",
              "affected_file": "src/main.ts",
              "line_number": 12,
              "extract": "import { HtmlModule } from '@angular/platform-browser';",
              "is_deterministic": true,
              "is_recoverable": true
            },
            
            "remediation": {
              "strategy": "regenerate_with_imports_focus",
              "priority": "high",
              "suggested_actions": [
                {
                  "action": "retry_llm_generation",
                  "parameters": {
                    "system_prompt_hint": "Ensure all Angular module imports are included",
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
                  "expected_success_rate": 1.0
                }
              ],
              "prevention": "Add import validation step before build"
            },
            
            "diagnostics": {
              "llm_attempt": 1,
              "llm_model": "anthropic/claude-3-haiku",
              "build_command": "ng build --configuration production",
              "build_output_lines": 150,
              "build_stderr_excerpt": "Error: src/main.ts:12:22 - error TS2307: Cannot find module...",
              "similar_failures_in_phase": 3,
              "first_occurrence": "case-01",
              "last_occurrence": "case-22"
            }
          },
          {
            "name": "WCAG Compliance",
            "criterion_id": "wcag_compliance",
            "max_points": 5.0,
            "awarded": 0.0,
            "passed": false,
            "severity": "high",
            "reason": "LLM judge confidence: 15.0% - only fixed 1/3 missing images",
            
            "root_cause": {
              "category": "llm_reasoning",
              "subcategory": "incomplete_solution",
              "confidence": 0.82,
              "error_pattern": "Partial fix - addressed only visible violation",
              "violation_count": {
                "original": 3,
                "fixed": 1,
                "remaining": 2,
                "coverage": 0.333
              },
              "is_deterministic": false,
              "is_recoverable": true,
              "root_factors": [
                {
                  "factor": "llm_context_window_limitation",
                  "weight": 0.4,
                  "evidence": "Missing images 2-3 are below the fold"
                },
                {
                  "factor": "insufficient_prompting",
                  "weight": 0.35,
                  "evidence": "Prompt did not enumerate all violations upfront"
                },
                {
                  "factor": "llm_model_capability",
                  "weight": 0.25,
                  "evidence": "Claude-3-Haiku lower accuracy on multi-violation cases"
                }
              ]
            },
            
            "remediation": {
              "strategy": "improved_llm_prompting",
              "priority": "high",
              "suggested_actions": [
                {
                  "action": "enumerate_violations_upfront",
                  "parameters": {
                    "format": "markdown_checklist",
                    "detail_level": "full_html_context"
                  },
                  "expected_success_rate": 0.65
                },
                {
                  "action": "use_larger_model",
                  "parameters": {
                    "model": "anthropic/claude-3-opus",
                    "cost_multiplier": 3.0
                  },
                  "expected_success_rate": 0.88
                },
                {
                  "action": "split_violations_by_area",
                  "parameters": {
                    "strategy": "above_fold_then_below_fold",
                    "batch_size": 2
                  },
                  "expected_success_rate": 0.78
                }
              ],
              "prevention": "Implement violation enumeration in prompt template"
            },
            
            "diagnostics": {
              "llm_attempt": 1,
              "llm_model": "anthropic/claude-3-haiku",
              "judge_confidence": 0.15,
              "judge_reasoning": "Only img tags with class='product-image' were fixed, missed class='thumbnail' and class='gallery'",
              "violations_detected_by_axe": ["image-alt[0]", "image-alt[1]", "image-alt[2]"],
              "violations_in_solution": ["image-alt[0]"],
              "solution_diff_lines": 45,
              "violations_per_fix_attempt": 1.0
            }
          },
          {
            "name": "Build Pass",
            "criterion_id": "visual_stability",
            "max_points": 3.0,
            "awarded": 0.0,
            "passed": false,
            "severity": "medium",
            "reason": "Not measured (no screenshots captured)",
            
            "root_cause": {
              "category": "instrumentation",
              "subcategory": "measurement_skipped",
              "confidence": 1.0,
              "error_pattern": "CLS/bbox_drift measurement not enabled",
              "is_deterministic": true,
              "is_recoverable": true
            },
            
            "remediation": {
              "strategy": "enable_visual_stability_measurement",
              "priority": "medium",
              "suggested_actions": [
                {
                  "action": "enable_chrome_devtools_tracing",
                  "parameters": {
                    "trace_categories": ["blink.user_timing", "loading"]
                  },
                  "expected_success_rate": 0.95
                }
              ],
              "prevention": "Make visual stability measurement mandatory for all cases"
            },
            
            "diagnostics": {
              "measurement_available": false,
              "reason": "Not attempted (prior criteria failed)"
            }
          }
        ]
      }
    }
  ]
}
```

**Key Additions for Self-Healing:**

| Field | Purpose | Self-Healing Use |
|-------|---------|------------------|
| `criterion_id` | Machine-readable criterion name | Route to specific fixer modules |
| `severity` | critical/high/medium/low | Prioritize which failures to fix first |
| `root_cause.category` | Failure taxonomy | Classify and aggregate patterns |
| `root_cause.confidence` | 0-1 probability | Weight in decision tree |
| `root_cause.is_deterministic` | bool | Can retry safely? |
| `root_cause.is_recoverable` | bool | Is this fixable? |
| `root_factors` | List of contributing factors | Multi-cause analysis (e.g., context + prompting) |
| `remediation.strategy` | Fix approach | Select remedy tactic |
| `remediation.suggested_actions` | Ranked actions | Execute in priority order |
| `expected_success_rate` | 0-1 probability | Cost-benefit analysis |
| `diagnostics.similar_failures_in_phase` | Count | Detect systemic issues |
| `first_occurrence` / `last_occurrence` | Test case IDs | Correlate across cases |

---

### 2. metrics-summary-{phase}.json (Tier 1+)

**Purpose**: Phase-level metrics with failure patterns for self-healing prioritization.

```json
{
  "phase": "all",
  "timestamp": "2026-09-02T01:25:00+00:00",
  "schema_version": "1.0",
  "summary": {
    "total_cases": 22,
    "violation_clearance_rate": 0.0,
    "human_escalation_rate": 0.409,
    "error_rate": 0.409,
    "mean_latency_seconds": 63.6,
    "brier_score": 0.0755,
    "expected_calibration_error": 0.129
  },
  
  "failure_analysis": {
    "by_criterion": {
      "build_pass": {
        "total_cases": 22,
        "passed": 0,
        "failed": 22,
        "pass_rate": 0.0,
        "top_root_causes": [
          {
            "category": "code_generation",
            "subcategory": "missing_import",
            "count": 9,
            "percentage": 0.409,
            "affected_cases": ["case-01", "case-03", "case-06", "case-08", "case-10", "case-12", "case-14", "case-16", "case-18"],
            "severity": "critical",
            "remediation_priority": 1,
            "suggested_fix": "improved_import_validation"
          },
          {
            "category": "code_generation",
            "subcategory": "syntax_error",
            "count": 7,
            "percentage": 0.318,
            "affected_cases": ["case-02", "case-04", "case-07", "case-09", "case-11", "case-13", "case-15"],
            "severity": "critical",
            "remediation_priority": 2,
            "suggested_fix": "llm_syntax_validation"
          },
          {
            "category": "llm_reasoning",
            "subcategory": "incomplete_solution",
            "count": 5,
            "percentage": 0.227,
            "affected_cases": ["case-05", "case-17", "case-19", "case-20", "case-21"],
            "severity": "high",
            "remediation_priority": 3,
            "suggested_fix": "improved_llm_prompting"
          }
        ]
      },
      "wcag_compliance": {
        "total_cases": 22,
        "passed": 2,
        "failed": 20,
        "pass_rate": 0.091,
        "top_root_causes": [
          {
            "category": "llm_reasoning",
            "subcategory": "incomplete_solution",
            "count": 12,
            "percentage": 0.545,
            "affected_cases": ["case-01", "case-02", "case-03", "case-06", "case-08", "case-10", "case-12", "case-14", "case-16", "case-18", "case-20", "case-22"],
            "severity": "high",
            "remediation_priority": 1,
            "suggested_fix": "multi_pass_violation_fixing"
          }
        ]
      }
    },
    
    "by_category": {
      "code_generation": {
        "occurrence_count": 16,
        "percentage_of_failures": 0.727,
        "severity": "critical",
        "top_subcategories": ["missing_import", "syntax_error", "type_mismatch"],
        "systemic_indicator": true,
        "remediation_focus": "Improve LLM code generation guardrails"
      },
      "llm_reasoning": {
        "occurrence_count": 17,
        "percentage_of_failures": 0.773,
        "severity": "high",
        "top_subcategories": ["incomplete_solution", "incorrect_selector", "wrong_attribute"],
        "systemic_indicator": true,
        "remediation_focus": "Better violation enumeration and multi-pass fixing"
      },
      "timeout": {
        "occurrence_count": 9,
        "percentage_of_failures": 0.409,
        "severity": "high",
        "root_cause_factors": ["llm_api_latency", "build_compilation_time"],
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
        "implication": "Fix build issues first before optimizing WCAG detection"
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
  
  "cases_by_route": {
    "auto": 0,
    "human": 22
  },
  
  "cases_by_error": {
    "cleared": 0,
    "errored": 9,
    "pending_review": 13
  },
  
  "by_rule": {
    "html-has-lang": {
      "total": 11,
      "cleared": 0,
      "error_rate": 0.364,
      "mean_score": 3.2,
      "top_failure_reason": "incomplete_solution (62%)",
      "estimated_fix_effort": "medium"
    },
    "image-alt": {
      "total": 5,
      "cleared": 0,
      "error_rate": 0.4,
      "mean_score": 2.8,
      "top_failure_reason": "missing_import (55%)",
      "estimated_fix_effort": "high"
    }
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
        "Add import validation in code generation prompt",
        "Implement AST-based syntax checking before build",
        "Cache Angular dependencies to speed compilation"
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
        "Enumerate all violations upfront",
        "Split fixes by violation type",
        "Use larger LLM model for complex cases"
      ]
    },
    {
      "rank": 3,
      "recommendation": "Reduce latency with parallel execution and caching",
      "impact_on_clearance_rate": 0.05,
      "estimated_effort_hours": 8,
      "confidence": 0.78,
      "affected_cases": 9,
      "actions": [
        "Implement build output caching",
        "Parallelize criterion scoring",
        "Use batch LLM inference"
      ]
    }
  ],
  
  "self_healing_readiness": {
    "can_auto_retry": 0.59,
    "can_auto_fix_with_strategy": 0.82,
    "can_escalate_to_human": 1.0,
    "estimated_auto_fix_success_rate": 0.35,
    "manual_intervention_required": true,
    "human_expertise_needed": ["llm_prompting", "angular_codegen", "wcag_standards"]
  }
}
```

**Key Additions for Self-Healing:**

| Section | Purpose | Self-Healing Use |
|---------|---------|------------------|
| `failure_analysis.by_criterion` | Breakdown by scoring criterion | Identify which criteria to optimize first |
| `failure_analysis.by_category` | Aggregate by root cause | Detect systemic issues (e.g., "LLM reasoning is broken") |
| `cascade_failures` | Show dependencies | Fix trigger issues first |
| `optimization_recommendations` | Prioritized actions | Execute highest-impact fixes first |
| `self_healing_readiness` | Feasibility assessment | Know when to escalate to human |

---

### 3. timing-profile-{phase}.json (Tier 2 - Future)

```json
{
  "phase": "all",
  "timestamp": "2026-09-02T01:25:00+00:00",
  "schema_version": "1.0",
  "cases": [
    {
      "case_id": "case-06",
      "total_seconds": 109.7,
      
      "phase_breakdown": [
        {
          "phase": "violation_analysis",
          "duration_seconds": 5.2,
          "percentage": 0.047,
          "status": "success",
          "details": "Axe-core audit ran successfully"
        },
        {
          "phase": "generate_solution",
          "duration_seconds": 72.3,
          "percentage": 0.659,
          "status": "timeout",
          "details": "LLM inference took 45.2s, retried twice",
          "slow_tool_calls": [
            {
              "tool": "llm_inference",
              "latency_seconds": 45.2,
              "model": "anthropic/claude-3-haiku",
              "tokens_generated": 285,
              "root_cause": "api_backend_latency",
              "attempt": 1
            }
          ]
        },
        {
          "phase": "validate_solution",
          "duration_seconds": 25.1,
          "percentage": 0.229,
          "status": "failure",
          "details": "Build compilation failed, no retry",
          "tool_calls": [
            {
              "tool": "angular_cli_build",
              "latency_seconds": 25.1,
              "exit_code": 1,
              "error": "error TS2307: Cannot find module 'HtmlModule'"
            }
          ]
        },
        {
          "phase": "score_solution",
          "duration_seconds": 7.1,
          "percentage": 0.065,
          "status": "success",
          "details": "Scoring completed despite invalid code"
        }
      ],
      
      "tool_call_summary": {
        "llm_inference": {
          "calls": 2,
          "total_time": 72.3,
          "mean_time": 36.15,
          "max_time": 45.2,
          "attempts": 2,
          "failures": 0
        },
        "angular_cli_build": {
          "calls": 1,
          "total_time": 25.1,
          "failures": 1,
          "error_rate": 1.0
        }
      },
      
      "performance_insights": [
        {
          "anomaly": "llm_latency_high",
          "severity": "high",
          "duration_seconds": 45.2,
          "expected_seconds": 8.0,
          "multiplier": 5.65,
          "possible_causes": [
            "OpenRouter backend congestion",
            "Model context too large",
            "API rate limit backoff"
          ]
        },
        {
          "anomaly": "build_failure",
          "severity": "critical",
          "error_type": "ImportError",
          "recovery_possible": true,
          "suggested_recovery": "Retry with import validation"
        }
      ]
    }
  ],
  
  "phase_totals": {
    "total_phases": 22,
    "mean_total_time": 63.6,
    "slowest_phase": "generate_solution",
    "slowest_percentage": 0.659,
    "bottleneck_analysis": "LLM inference is 65.9% of time - optimize model selection or caching"
  }
}
```

---

### 4. audit_trail_violations.json (Tier 3 - Future)

```json
{
  "timestamp": "2026-09-02T01:25:00+00:00",
  "schema_version": "1.0",
  "violations": {
    "889e3288588d": {
      "violation_id": "889e3288588d",
      "rule_id": "image-alt",
      "selector": "img.product-image",
      "first_occurrence": "2026-09-02T08:00:00Z",
      
      "events": [
        {
          "timestamp": "2026-09-02T08:00:00Z",
          "event_type": "generated",
          "status": "attempted",
          "severity": "medium",
          
          "context": {
            "phase": "Phase 2 (benchmark run all)",
            "case_id": "case-06",
            "attempt": 1,
            "llm_model": "anthropic/claude-3-haiku"
          },
          
          "outcome": {
            "route": "human",
            "score": 0.0,
            "reason": "Build failed - missing import"
          },
          
          "root_cause_classification": {
            "category": "code_generation",
            "subcategory": "missing_import",
            "confidence": 0.95
          },
          
          "self_healing_data": {
            "is_retryable": true,
            "suggested_retry_action": "regenerate with import validation",
            "backoff_strategy": "exponential",
            "max_retries_recommended": 3
          }
        },
        {
          "timestamp": "2026-09-02T09:00:00Z",
          "event_type": "pr_created",
          "status": "attempted",
          "severity": "low",
          
          "context": {
            "pr_number": 11,
            "branch": "fix/image-alt-889e3288588d",
            "commit_hash": "abc123def456"
          },
          
          "outcome": {
            "result": "pr_created_but_build_fails",
            "merge_eligible": false
          }
        },
        {
          "timestamp": "2026-09-02T10:00:00Z",
          "event_type": "analysis_requested",
          "status": "escalated_to_human",
          "severity": "high",
          
          "context": {
            "reason": "Multiple failures - auto-fixing unlikely",
            "failure_count": 4,
            "failure_categories": ["code_generation", "llm_reasoning"]
          },
          
          "self_healing_assessment": {
            "estimated_auto_fix_success": 0.15,
            "recommended_action": "human_review",
            "human_expertise_needed": ["angular_codegen", "wcag_standards"],
            "priority": "high"
          }
        }
      ]
    }
  }
}
```

---

## Self-Healing Inference Rules

### Rule 1: Retry on Transient Failures
```
IF root_cause.is_deterministic == False 
  AND root_cause.is_recoverable == True
  AND attempts < max_attempts
THEN retry_with_improved_parameters()
```

### Rule 2: Fix Cascade Blockers First
```
IF cascade_failures.trigger == "current_criterion_failed"
THEN prioritize_fixing_trigger_first()
```

### Rule 3: Escalate to Human When Auto-Fix Unlikely
```
IF self_healing_readiness.estimated_auto_fix_success < 0.3
THEN escalate_to_human_review(priority="high")
```

### Rule 4: Apply Optimization Recommendations
```
FOR each recommendation IN optimization_recommendations
  SORTED BY impact_on_clearance_rate DESCENDING
DO implement_and_measure_impact()
```

---

## Example Self-Healing Flow

```
1. Load observability/log/scores-breakdown-all.json
2. Identify systemic failures: build_pass (100% fail rate)
3. Find root causes: "missing_import" (40%), "syntax_error" (30%)
4. Load metrics-summary-all.json: confirm "code_generation" is blocker
5. Get optimization recommendations: "Fix build pass first (82% cascade impact)"
6. Execute: 
   - Retry case-06 with improved import validation prompt
   - If success rate > 0.6, deploy to all cases
   - If success rate < 0.3, escalate to human (add import validation to code gen)
7. Measure impact: Re-run Phase 2, compare new scores
8. Update wiki/lessons with what worked
9. Feed success rate back into self_healing_readiness scores
```

---

## Integration Checklist

- [ ] Update run_eval.py to generate logs in this schema
- [ ] Implement root_cause classification in score_rubric tool
- [ ] Add failure_analysis computation in metrics_summary generation
- [ ] Create self_healing_readiness assessment function
- [ ] Build retry loop in cli.py using suggested_actions
- [ ] Create dashboards querying this schema for optimization prioritization
- [ ] Test cascade_failure detection with Phase 2 re-run
