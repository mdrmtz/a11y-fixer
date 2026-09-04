"""Tests for html_lang_fix module."""

import pytest

from a11y_fixer.domain.html_lang_fix import (
    HtmlLangFix,
    get_html_lang_fix,
    is_html_lang_violation,
)


class TestIsHtmlLangViolation:
    """Test detection of html-has-lang violations."""

    def test_html_has_lang_on_root_element_is_detected(self) -> None:
        """Should detect html-has-lang violation on root html element."""
        violation = {"rule": "html-has-lang", "selector": "html"}
        assert is_html_lang_violation(violation) is True

    def test_html_has_lang_on_non_root_is_not_detected(self) -> None:
        """Should not detect html-has-lang on non-root element."""
        violation = {"rule": "html-has-lang", "selector": "html div"}
        assert is_html_lang_violation(violation) is False

    def test_different_rule_is_not_detected(self) -> None:
        """Should not detect other rules."""
        violation = {"rule": "color-contrast", "selector": "html"}
        assert is_html_lang_violation(violation) is False

    def test_missing_rule_key_is_not_detected(self) -> None:
        """Should handle missing rule key gracefully."""
        violation = {"selector": "html"}
        assert is_html_lang_violation(violation) is False

    def test_missing_selector_key_is_not_detected(self) -> None:
        """Should handle missing selector key gracefully."""
        violation = {"rule": "html-has-lang"}
        assert is_html_lang_violation(violation) is False


class TestGetHtmlLangFix:
    """Test HtmlLangFix singleton retrieval."""

    def test_returns_html_lang_fix_instance(self) -> None:
        """Should return HtmlLangFix dataclass instance."""
        fix = get_html_lang_fix()
        assert isinstance(fix, HtmlLangFix)

    def test_singleton_is_consistent(self) -> None:
        """Should return the same instance on multiple calls."""
        fix1 = get_html_lang_fix()
        fix2 = get_html_lang_fix()
        assert fix1 is fix2

    def test_template_has_correct_values(self) -> None:
        """Should have deterministic template values."""
        fix = get_html_lang_fix()
        assert fix.target_file == "src/index.html"
        assert fix.template_code == '<html lang="en">'
        assert fix.wcag_sc == "3.1.1"
        assert fix.technique_id == "H57"

    def test_template_rationale_mentions_wcag(self) -> None:
        """Rationale should explain WCAG requirement."""
        fix = get_html_lang_fix()
        assert "WCAG 3.1.1" in fix.rationale
        assert "H57" in fix.rationale
        assert "lang" in fix.rationale

    def test_instance_is_frozen(self) -> None:
        """HtmlLangFix instance should be immutable."""
        fix = get_html_lang_fix()
        with pytest.raises(AttributeError):
            fix.template_code = '<html lang="fr">'  # type: ignore[misc]
