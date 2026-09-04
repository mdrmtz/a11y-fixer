"""Pre/during-generation guardrails: schema validation, path safety, epistemic
confidence gating, overconfidence scanning, and calibration metrics.

The overconfidence scanner (`_OVERCONFIDENCE_MARKERS`, `check_confidence_calibration`)
is ported near-verbatim from Module-06 Lab 6.1
(`11-guardrailing_hallucinations_and_overconfidence_in_agent_outputs_solution.ipynb`),
retargeted from medical-safety report text to accessibility-fix rationale text.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

# --- Pre-generation: axe-core JSON schema validation ---


class AxeNode(BaseModel):
    """A single failing DOM node within an axe-core violation."""

    html: str = ""
    target: list[str] = Field(default_factory=list)
    failureSummary: str | None = (
        None  # noqa: N815 - axe-core's own camelCase field name
    )


class AxeViolation(BaseModel):
    """One axe-core rule violation (may span multiple failing nodes)."""

    id: str
    impact: str | None = None
    tags: list[str] = Field(default_factory=list)
    description: str = ""
    help: str = ""
    helpUrl: str = ""  # noqa: N815 - axe-core's own camelCase field name
    nodes: list[AxeNode] = Field(default_factory=list)


class AxeAuditReport(BaseModel):
    """A single-page axe-core report, as produced by `@axe-core/cli --save`."""

    url: str
    violations: list[AxeViolation] = Field(default_factory=list)


def validate_axe_report(payload: dict) -> tuple[AxeAuditReport | None, str | None]:
    """Validate inbound axe-core JSON before any downstream processing.

    Returns `(report, None)` on success or `(None, error_message)` on failure.
    """
    try:
        return AxeAuditReport.model_validate(payload), None
    except ValidationError as exc:
        return None, str(exc)


def validate_raw_axe_reports(payloads: list[dict]) -> str | None:
    """Validate every per-page raw axe-core report (as stored in a normalized
    report's `raw_reports` list). Returns the first validation error found,
    or `None` if every report is well-formed.
    """
    for payload in payloads:
        _, error = validate_axe_report(payload)
        if error is not None:
            return error
    return None


# --- Pre-generation: path-traversal guard ---

ALLOWED_WRITE_EXTENSIONS: frozenset[str] = frozenset({".html", ".ts", ".scss"})


def validate_write_path(
    candidate: str | Path,
    *,
    root: Path,
    allowed_extensions: frozenset[str] = ALLOWED_WRITE_EXTENSIONS,
) -> tuple[Path | None, str | None]:
    """Reject a proposed write path that escapes `root` or has a non-whitelisted extension.

    Returns `(resolved_path, None)` if safe, else `(None, reason)`.
    """
    root = root.resolve()
    candidate_path = Path(candidate)
    resolved = (
        candidate_path.resolve()
        if candidate_path.is_absolute()
        else (root / candidate_path).resolve()
    )

    if not resolved.is_relative_to(root):
        return None, f"path escapes fixture root {root}: {candidate}"
    if resolved.suffix not in allowed_extensions:
        allowed = ", ".join(sorted(allowed_extensions))
        return None, f"extension {resolved.suffix!r} not in whitelist ({allowed})"
    return resolved, None


# --- During-generation: P(IK) epistemic monitor ---

DEFAULT_P_IK_THRESHOLD: Literal[0.75] = 0.75


def epistemic_gate(p_ik: float, threshold: float = DEFAULT_P_IK_THRESHOLD) -> dict:
    """Block a candidate whose self-reported P(IK) ("probability I know") is too low."""
    if not 0.0 <= p_ik <= 1.0:
        msg = f"p_ik must be in [0, 1], got {p_ik}"
        raise ValueError(msg)
    passed = p_ik >= threshold
    return {
        "p_ik": p_ik,
        "threshold": threshold,
        "passed": passed,
        "verdict": "PASS" if passed else "BLOCK",
    }


# --- During-generation: overconfidence scanner (ported from Module-06 Lab 6.1) ---

_OVERCONFIDENCE_MARKERS: dict[str, str] = {
    "proven": "supported by evidence",
    "always": "in most cases",
    "never": "rarely",
    "guaranteed": "likely",
    "mandated": "recommended",
    "without exception": "in the majority of cases",
    "completely safe": "generally well-tolerated",
    "no risk": "low risk",
    "zero": "minimal",
    "100%": "high probability",
    "certainly": "likely",
    "undoubtedly": "evidence suggests",
    "definitively": "current evidence indicates",
}


def check_confidence_calibration(text: str) -> dict:
    """During-generation guardrail: flag overconfident language in fix rationale.

    Scans `text` for overconfidence markers and returns the flagged phrases, an
    overconfidence score (fraction of sentences with >=1 marker), a PASS/WARN/FAIL
    verdict, and a hedged rewrite.
    """
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    sentences = [s for s in sentences if len(s.strip()) > 10]

    flagged_phrases: list[tuple[str, str, str]] = []
    flagged_sentence_indices: set[int] = set()
    hedged_text = text

    for marker, alternative in _OVERCONFIDENCE_MARKERS.items():
        pattern = re.compile(re.escape(marker), re.IGNORECASE)
        for i, sent in enumerate(sentences):
            match = pattern.search(sent)
            if match:
                start = max(0, match.start() - 20)
                end = min(len(sent), match.end() + 20)
                context = sent[start:end]
                flagged_phrases.append((marker, f"...{context}...", alternative))
                flagged_sentence_indices.add(i)
        hedged_text = pattern.sub(alternative, hedged_text)

    total = len(sentences) if sentences else 1
    score = len(flagged_sentence_indices) / total

    if score < 0.3:  # noqa: PLR2004
        verdict = "PASS"
    elif score < 0.5:  # noqa: PLR2004
        verdict = "WARN"
    else:
        verdict = "FAIL"

    return {
        "original_text": text,
        "flagged_phrases": flagged_phrases,
        "overconfidence_score": round(score, 3),
        "verdict": verdict,
        "hedged_text": hedged_text,
    }


# --- Calibration math: Brier score and Expected Calibration Error ---


def brier_score(predictions: list[float], outcomes: list[int]) -> float:
    """Mean squared error between predicted probabilities and binary outcomes."""
    if len(predictions) != len(outcomes):
        msg = "predictions and outcomes must be the same length"
        raise ValueError(msg)
    if not predictions:
        return float("nan")
    return sum((p - o) ** 2 for p, o in zip(predictions, outcomes, strict=True)) / len(
        predictions
    )


def expected_calibration_error(
    predictions: list[float], outcomes: list[int], n_bins: int = 10
) -> float:
    """Weighted average of |accuracy(bin) - confidence(bin)| across equal-width confidence bins."""
    if len(predictions) != len(outcomes):
        msg = "predictions and outcomes must be the same length"
        raise ValueError(msg)
    if not predictions:
        return float("nan")

    n = len(predictions)
    bins: list[list[tuple[float, int]]] = [[] for _ in range(n_bins)]
    for p, o in zip(predictions, outcomes, strict=True):
        idx = min(int(p * n_bins), n_bins - 1)
        bins[idx].append((p, o))

    ece = 0.0
    for bucket in bins:
        if not bucket:
            continue
        confidence = sum(p for p, _ in bucket) / len(bucket)
        accuracy = sum(o for _, o in bucket) / len(bucket)
        ece += (len(bucket) / n) * abs(accuracy - confidence)
    return ece
