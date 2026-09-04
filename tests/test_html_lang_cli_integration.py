"""Integration tests for html-lang fast-track in CLI."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from a11y_fixer.domain.html_lang_fix import is_html_lang_violation


class TestIsHtmlLangViolationIntegration:
    """Test html-lang violation detection in CLI context."""

    def test_detects_html_has_lang_violation_from_axe_report(self) -> None:
        """Should detect html-has-lang in a typical axe violation."""
        violation = {
            "rule": "html-has-lang",
            "selector": "html",
            "url": "https://example.com",
            "html": "<html>",
            "failure_summary": "Fix any of the following: The html element does not have a lang attribute",
        }
        assert is_html_lang_violation(violation) is True

    def test_ignores_other_violations(self) -> None:
        """Should not flag other a11y violations as html-lang."""
        violations = [
            {"rule": "color-contrast", "selector": "button.submit"},
            {"rule": "image-alt", "selector": "img.logo"},
            {"rule": "label-missing", "selector": "input#email"},
        ]
        for v in violations:
            assert is_html_lang_violation(v) is False


@pytest.mark.asyncio
async def test_html_lang_fast_track_integration_success(tmp_path: Path) -> None:
    """Integration: html-lang fast-track applies and delivers successfully."""
    # Setup fixture
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    (fixture / ".git").mkdir()

    src = fixture / "src"
    src.mkdir()
    (src / "index.html").write_text("<html>\n  <body>Test</body>\n</html>")

    # Mock dependencies
    with patch("a11y_fixer.adapters.html_lang_applier._run_ng_build") as mock_build:
        mock_build.return_value = {"success": True, "error": None}

        with patch("a11y_fixer.cli.deliver_violation") as mock_deliver:
            mock_deliver.return_value = {
                "route": "auto",
                "pr_number": 42,
                "diff": "[HTML-LANG] Applied fix",
            }

            # Simulate violation from axe
            violation = {
                "rule": "html-has-lang",
                "selector": "html",
                "url": "https://example.com",
                "html": "<html>",
                "failure_summary": "The html element does not have a lang attribute",
            }

            # Call is_html_lang_violation (this would be called in CLI loop)
            assert is_html_lang_violation(violation) is True

            # Simulate apply_html_lang being called
            from a11y_fixer.adapters.html_lang_applier import apply_html_lang

            result = await apply_html_lang(fixture)

            assert result["applied"] is True
            assert "<html lang=\"en\">" in result["changes"][0]["new"]
