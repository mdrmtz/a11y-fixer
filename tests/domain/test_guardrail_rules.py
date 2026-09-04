from __future__ import annotations

from pathlib import Path

import pytest

from a11y_fixer.domain.guardrail_rules import (
    brier_score,
    check_confidence_calibration,
    epistemic_gate,
    expected_calibration_error,
    validate_axe_report,
    validate_raw_axe_reports,
    validate_write_path,
)

# --- axe-core schema validation ---


def test_validate_axe_report_accepts_real_shaped_payload() -> None:
    payload = {
        "url": "http://localhost:4200/blog",
        "violations": [
            {
                "id": "button-name",
                "impact": "critical",
                "tags": ["wcag2a", "wcag412"],
                "description": "Ensures buttons have discernible text",
                "help": "Buttons must have discernible text",
                "helpUrl": "https://dequeuniversity.com/rules/axe/4.13/button-name",
                "nodes": [{"html": "<button></button>", "target": ["button"], "failureSummary": "Fix any of the following"}],
            }
        ],
    }
    report, error = validate_axe_report(payload)
    assert error is None
    assert report is not None
    assert report.violations[0].id == "button-name"


def test_validate_axe_report_rejects_missing_required_field() -> None:
    report, error = validate_axe_report({"violations": []})
    assert report is None
    assert error is not None
    assert "url" in error


def test_validate_raw_axe_reports_accepts_all_valid() -> None:
    payloads = [
        {"url": "http://localhost:4200/", "violations": []},
        {"url": "http://localhost:4200/blog", "violations": [{"id": "image-alt", "nodes": []}]},
    ]
    assert validate_raw_axe_reports(payloads) is None


def test_validate_raw_axe_reports_returns_first_error() -> None:
    payloads = [
        {"url": "http://localhost:4200/", "violations": []},
        {"violations": []},  # missing required "url"
    ]
    error = validate_raw_axe_reports(payloads)
    assert error is not None
    assert "url" in error


def test_validate_raw_axe_reports_accepts_empty_list() -> None:
    assert validate_raw_axe_reports([]) is None


# --- path-traversal guard ---


def test_validate_write_path_accepts_whitelisted_extension_inside_root(tmp_path: Path) -> None:
    resolved, error = validate_write_path("src/app/pages/blog/blog.component.html", root=tmp_path)
    assert error is None
    assert resolved == (tmp_path / "src/app/pages/blog/blog.component.html").resolve()


def test_validate_write_path_rejects_traversal_outside_root(tmp_path: Path) -> None:
    resolved, error = validate_write_path("../../etc/passwd", root=tmp_path)
    assert resolved is None
    assert error is not None
    assert "escapes fixture root" in error


def test_validate_write_path_rejects_non_whitelisted_extension(tmp_path: Path) -> None:
    resolved, error = validate_write_path("src/app/app.config.py", root=tmp_path)
    assert resolved is None
    assert error is not None
    assert "not in whitelist" in error


# --- P(IK) epistemic gate ---


def test_epistemic_gate_passes_at_or_above_threshold() -> None:
    assert epistemic_gate(0.75)["passed"] is True
    assert epistemic_gate(0.9)["verdict"] == "PASS"


def test_epistemic_gate_blocks_below_threshold() -> None:
    result = epistemic_gate(0.5)
    assert result["passed"] is False
    assert result["verdict"] == "BLOCK"


def test_epistemic_gate_rejects_out_of_range_input() -> None:
    with pytest.raises(ValueError, match="p_ik"):
        epistemic_gate(1.5)


# --- overconfidence scanner ---


def test_well_calibrated_text_passes() -> None:
    text = (
        "Evidence suggests this fix improves contrast for most users. "
        "Further manual review is recommended for complex backgrounds."
    )
    result = check_confidence_calibration(text)
    assert result["verdict"] == "PASS"
    assert result["overconfidence_score"] < 0.3


def test_overconfident_text_fails_and_is_hedged() -> None:
    text = (
        "This fix is guaranteed to always pass WCAG with zero risk. "
        "It is completely safe and definitively resolves the violation without exception."
    )
    result = check_confidence_calibration(text)
    assert result["verdict"] == "FAIL"
    assert result["overconfidence_score"] >= 0.5
    assert "guaranteed" not in result["hedged_text"].lower() or "likely" in result["hedged_text"].lower()
    assert len(result["flagged_phrases"]) > 0


# --- calibration math ---


def test_brier_score_of_perfect_predictions_is_zero() -> None:
    assert brier_score([1.0, 0.0, 1.0], [1, 0, 1]) == 0.0


def test_brier_score_of_worst_predictions_is_one() -> None:
    assert brier_score([1.0, 0.0], [0, 1]) == 1.0


def test_brier_score_requires_matching_lengths() -> None:
    with pytest.raises(ValueError, match="same length"):
        brier_score([1.0], [1, 0])


def test_expected_calibration_error_is_zero_when_perfectly_calibrated() -> None:
    # each bin's empirical accuracy exactly matches its predicted confidence
    predictions = [0.1] * 10 + [0.9] * 10
    outcomes = [0] * 9 + [1] + [1] * 9 + [0]
    ece = expected_calibration_error(predictions, outcomes, n_bins=10)
    assert ece == pytest.approx(0.0, abs=1e-9)


def test_expected_calibration_error_requires_matching_lengths() -> None:
    with pytest.raises(ValueError, match="same length"):
        expected_calibration_error([0.5], [1, 0])
