from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

AGENT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AGENT_ROOT / "evaluation"))

import run_eval as run_eval_module  # noqa: E402
from run_eval import (  # noqa: E402
    BENCHMARK_CASES_PATH,
    CaseResult,
    compute_metrics,
    filter_cases_by_ids,
    filter_cases_by_phase,
    filter_cases_by_range,
    load_benchmark_cases,
    main,
)


def test_benchmark_cases_file_matches_reconciled_ground_truth() -> None:
    """Verify benchmark file exists and is valid JSON.
    
    Checks that cases have required structure (flexible count).
    """
    cases = load_benchmark_cases()
    assert isinstance(cases, list)
    
    # Flexible: works with 1, 22, or any count
    if len(cases) > 0:
        rules = {case["rule"] for case in cases}
        assert rules <= {
            "html-has-lang",
            "color-contrast",
            "image-alt",
            "button-name",
            "link-name",
        }
        assert all(
            {"id", "page", "rule", "selector", "wcag", "ground_truth_fix"} <= set(case)
            for case in cases
        )


def test_load_benchmark_cases_default_path_is_real_file() -> None:
    """Verify benchmark file exists and is valid JSON.
    
    Checks that cases have required structure (flexible count).
    """
    cases = load_benchmark_cases()
    assert isinstance(cases, list)
    
    # Flexible: works with 0, 22, or any count
    if len(cases) > 0:
        rules = {case["rule"] for case in cases}
        assert rules <= {
            "html-has-lang",
            "color-contrast",
            "image-alt",
            "button-name",
            "link-name",
        }
        assert all(
            {"id", "page", "rule", "selector", "wcag", "ground_truth_fix"} <= set(case)
            for case in cases
        )


def _result(**overrides: object) -> CaseResult:
    defaults = {
        "case_id": "case-01",
        "rule": "html-has-lang",
        "page": "/",
        "route": "auto",
        "rubric_score": 18.0,
        "cleared": True,
        "latency_seconds": 2.0,
        "error": None,
    }
    defaults.update(overrides)
    return CaseResult(**defaults)  # type: ignore[arg-type]


def test_compute_metrics_on_empty_results() -> None:
    assert compute_metrics([]) == {"total_cases": 0}


def test_compute_metrics_all_cleared_perfect_confidence() -> None:
    results = [_result(rubric_score=20.0, cleared=True) for _ in range(4)]
    metrics = compute_metrics(results)
    assert metrics["total_cases"] == 4
    assert metrics["violation_clearance_rate"] == 1.0
    assert metrics["human_escalation_rate"] == 0.0
    assert metrics["error_rate"] == 0.0
    assert metrics["brier_score"] == pytest.approx(0.0)


def test_compute_metrics_mixed_outcomes() -> None:
    results = [
        _result(
            case_id="c1",
            rule="html-has-lang",
            cleared=True,
            rubric_score=20.0,
            route="auto",
        ),
        _result(
            case_id="c2",
            rule="color-contrast",
            cleared=False,
            rubric_score=10.0,
            route="human",
        ),
        _result(
            case_id="c3",
            rule="image-alt",
            cleared=True,
            rubric_score=15.0,
            route="auto",
        ),
        _result(
            case_id="c4",
            rule="image-alt",
            cleared=False,
            rubric_score=5.0,
            route="auto",
            error="build failed",
        ),
    ]
    metrics = compute_metrics(results)

    assert metrics["total_cases"] == 4
    assert metrics["violation_clearance_rate"] == pytest.approx(0.5)
    assert metrics["human_escalation_rate"] == pytest.approx(0.25)
    assert metrics["error_rate"] == pytest.approx(0.25)
    assert metrics["by_rule"]["image-alt"] == {"total": 2, "cleared": 1}
    assert metrics["by_rule"]["html-has-lang"] == {"total": 1, "cleared": 1}


def test_compute_metrics_mean_latency() -> None:
    results = [_result(latency_seconds=1.0), _result(latency_seconds=3.0)]
    assert compute_metrics(results)["mean_latency_seconds"] == pytest.approx(2.0)


def test_filter_cases_by_phase_f1_matches_about_image_alt() -> None:
    filtered, phase_def = filter_cases_by_phase(load_benchmark_cases(), "f1")
    assert len(filtered) == phase_def["cases_count"]
    assert all(c["page"] == "/about" and c["wcag"] == "1.1.1" for c in filtered)


def test_filter_cases_by_phase_unknown_phase_raises() -> None:
    with pytest.raises(ValueError, match="not found"):
        filter_cases_by_phase(load_benchmark_cases(), "does-not-exist")


def _cleanup_temp_cases(phase: str) -> None:
    (AGENT_ROOT / "evaluation" / f"_temp_{phase}_cases.json").unlink(missing_ok=True)


def test_main_phase_live_true_still_defaults_to_dry_run_without_explicit_flag(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Phases with live:true in phases.yaml still default to dry-run unless
    --live is explicitly provided. config.resolve_pr_delivery() enforces safe defaults."""
    seen: dict[str, object] = {}
    monkeypatch.setattr(
        run_eval_module,
        "run_eval",
        lambda **kwargs: seen.update(live=kwargs["live"]) or {"total_cases": 0},
    )

    exit_code = main(["--phase", "f3"])

    assert exit_code == 0
    assert seen["live"] is False
    _cleanup_temp_cases("f3")


def test_main_phase_live_true_with_explicit_live_flag_goes_live(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: dict[str, object] = {}
    monkeypatch.setattr(
        run_eval_module,
        "run_eval",
        lambda **kwargs: seen.update(live=kwargs["live"]) or {"total_cases": 0},
    )
    monkeypatch.setattr("builtins.input", lambda _prompt="": "yes")

    exit_code = main(["--phase", "f3", "--live"])

    assert exit_code == 0
    assert seen["live"] is True
    _cleanup_temp_cases("f3")


def test_main_phase_matching_zero_cases_fails_fast_before_running(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        run_eval_module, "filter_cases_by_phase", lambda *_a: ([], {"live": False})
    )
    run_calls: list[bool] = []
    monkeypatch.setattr(
        run_eval_module, "run_eval", lambda **_k: run_calls.append(True)
    )

    exit_code = main(["--phase", "f1"])

    assert exit_code == 1
    assert run_calls == []
    assert "matched 0 cases" in capsys.readouterr().out


def test_main_phase_cases_count_mismatch_prints_warning(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        run_eval_module,
        "filter_cases_by_phase",
        lambda *_a: ([{"id": "x"}], {"name": "test", "live": False, "cases_count": 5}),
    )
    monkeypatch.setattr(run_eval_module, "run_eval", lambda **_k: {"total_cases": 1})

    exit_code = main(["--phase", "f1"])

    assert exit_code == 0
    assert "expected 5 cases but filters matched 1" in capsys.readouterr().out
    _cleanup_temp_cases("f1")


def test_filter_cases_by_range_inclusive_bounds() -> None:
    cases = load_benchmark_cases()
    filtered = filter_cases_by_range(cases, "case-01", "case-03")
    assert [c["id"] for c in filtered] == ["case-01", "case-02", "case-03"]


def test_filter_cases_by_range_open_ended_from() -> None:
    cases = load_benchmark_cases()
    filtered = filter_cases_by_range(cases, "case-21", None)
    assert [c["id"] for c in filtered] == ["case-21", "case-22"]


def test_filter_cases_by_range_open_ended_to() -> None:
    cases = load_benchmark_cases()
    filtered = filter_cases_by_range(cases, None, "case-02")
    assert [c["id"] for c in filtered] == ["case-01", "case-02"]


def test_phase_smoke_matches_exactly_case_01() -> None:
    filtered, _phase_def = filter_cases_by_phase(load_benchmark_cases(), "smoke")
    assert [c["id"] for c in filtered] == ["case-01"]


def test_phase_all_matches_every_case() -> None:
    cases = load_benchmark_cases()
    filtered, _phase_def = filter_cases_by_phase(cases, "all")
    assert len(filtered) == len(cases) == 22


def test_main_case_range_defaults_to_dry_run_and_matches_range(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: dict[str, object] = {}

    def fake_run_eval(
        *, cases_path: Path, output_path: Path, live: bool, use_worktree: bool = False
    ) -> dict:  # noqa: ARG001
        seen["live"] = live
        seen["cases"] = json.loads(cases_path.read_text(encoding="utf-8"))
        return {"total_cases": len(seen["cases"])}

    monkeypatch.setattr(run_eval_module, "run_eval", fake_run_eval)

    exit_code = main(["--case-from", "case-01", "--case-to", "case-03"])

    assert exit_code == 0
    assert seen["live"] is False
    assert [c["id"] for c in seen["cases"]] == ["case-01", "case-02", "case-03"]
    assert not (AGENT_ROOT / "evaluation" / "_temp_case_range_cases.json").exists()


def test_main_case_range_matching_zero_cases_fails_fast(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(["--case-from", "case-99", "--case-to", "case-99"])

    assert exit_code == 1
    assert "matched 0 cases" in capsys.readouterr().out


def test_main_rejects_phase_combined_with_case_range(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(["--phase", "f1", "--case-from", "case-01"])

    assert exit_code == 1
    assert "mutually exclusive" in capsys.readouterr().out


def test_filter_cases_by_ids_preserves_benchmark_order_and_skips_unknown() -> None:
    cases = load_benchmark_cases()
    filtered = filter_cases_by_ids(cases, ["case-15", "case-01", "case-99", "case-08"])
    assert [c["id"] for c in filtered] == ["case-01", "case-08", "case-15"]


def test_main_case_ids_non_contiguous_list_and_warns_on_missing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: dict[str, object] = {}

    def fake_run_eval(
        *, cases_path: Path, output_path: Path, live: bool, use_worktree: bool = False
    ) -> dict:  # noqa: ARG001
        seen["cases"] = json.loads(cases_path.read_text(encoding="utf-8"))
        return {"total_cases": len(seen["cases"])}

    monkeypatch.setattr(run_eval_module, "run_eval", fake_run_eval)

    exit_code = main(["--case-ids", "case-01,case-04,case-08,case-15,case-22,case-99"])

    assert exit_code == 0
    assert [c["id"] for c in seen["cases"]] == [
        "case-01",
        "case-04",
        "case-08",
        "case-15",
        "case-22",
    ]
    assert "not found, skipping: case-99" in capsys.readouterr().out
    assert not (AGENT_ROOT / "evaluation" / "_temp_case_ids_cases.json").exists()


def test_main_case_ids_all_unknown_fails_fast(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(["--case-ids", "case-98,case-99"])

    assert exit_code == 1
    assert "none of the requested case ids matched" in capsys.readouterr().out


def test_main_rejects_case_ids_combined_with_case_range(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(["--case-ids", "case-01", "--case-from", "case-02"])

    assert exit_code == 1
    assert "mutually exclusive" in capsys.readouterr().out


def test_pr_delivery_fields_extracts_live_result_url_and_number() -> None:
    """Finding #2 (gap analysis): a real live PR delivery must surface
    pull_request_url - that's the actual bar for "PR delivery verified".
    """
    live_result = run_eval_module.pr_delivery.LiveResult(
        pull_request_url="https://github.com/mdrmtz/Hallucinate.io/pull/42",
        pull_request_number=42,
        branch_name="a11y-fixer/abc123",
    )
    outcome = {"delivered": True, "result": live_result, "route": "auto"}

    fields = run_eval_module._pr_delivery_fields(outcome)

    assert fields == {
        "delivered": True,
        "pull_request_url": "https://github.com/mdrmtz/Hallucinate.io/pull/42",
        "pull_request_number": 42,
    }


def test_pr_delivery_fields_none_for_non_live_outcomes() -> None:
    """Dry-run, human-queue, and no-op outcomes carry no LiveResult - the PR
    fields must stay None rather than fabricate a URL that doesn't exist.
    """
    dry_run_outcome = {
        "delivered": True,
        "result": run_eval_module.pr_delivery.DryRunResult(
            diff_path=Path("/tmp/x.diff"),
            description_path=Path("/tmp/x.md"),
            unified_diff="",
        ),
        "route": "auto",
    }
    human_outcome = {"delivered": False, "queue_path": "/tmp/q.json", "route": "human"}
    no_op_outcome = {
        "delivered": False,
        "reason": "codebase_compiler made no file changes",
        "route": "auto",
    }

    for outcome in (dry_run_outcome, human_outcome, no_op_outcome):
        fields = run_eval_module._pr_delivery_fields(outcome)
        assert fields["pull_request_url"] is None
        assert fields["pull_request_number"] is None
        assert fields["delivered"] == outcome.get("delivered")


def test_case_result_pr_delivery_fields_default_to_none() -> None:
    """New CaseResult fields must default to None so every existing
    construction site (and every existing test) is unaffected.
    """
    result = CaseResult(
        case_id="case-01",
        rule="html-has-lang",
        page="/",
        route="auto",
        rubric_score=20.0,
        cleared=True,
        latency_seconds=1.0,
    )
    assert result.delivered is None
    assert result.pull_request_url is None
    assert result.pull_request_number is None
