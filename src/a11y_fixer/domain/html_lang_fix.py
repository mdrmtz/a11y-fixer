"""Html-lang attribute fix for WCAG 3.1.1 Primary Language of Page (H57).

This module provides detection and the deterministic fix template for the
html-has-lang violation:
  - Rule: html-has-lang
  - Selector: html (document root)
  - WCAG: 3.1.1 Language of Page (Level A)
  - Technique: H57 (sufficient) - Using language attributes on the html element
  - Fix: <html> → <html lang="en">

Detection is specific to this one rule+selector pair. No generalization to other
accessibility fixes - each one would require its own module with domain-specific
reasoning.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HtmlLangFix:
    """The deterministic fix for html-has-lang violations (WCAG 3.1.1, H57)."""

    target_file: str = "src/index.html"
    """Path to the file containing the <html> element (relative to fixture root)."""

    template_code: str = '<html lang="en">'
    """The exact replacement code."""

    rationale: str = (
        "WCAG 3.1.1 Primary Language of Page requires the default language "
        "to be programmatically determinable via the html element's lang attribute. "
        "H57 (sufficient technique): Using language attributes on the html element."
    )
    """Human-readable explanation for audit trail."""

    wcag_sc: str = "3.1.1"
    """WCAG Success Criterion."""

    technique_id: str = "H57"
    """Technique ID."""


# Singleton instance - always the same for this WCAG requirement
_HTML_LANG_FIX = HtmlLangFix()


def is_html_lang_violation(violation: dict) -> bool:
    """Check if a violation is the html-has-lang case we can auto-fix.

    Args:
        violation: dict with 'rule' and 'selector' keys from axe report

    Returns:
        True if this is html-has-lang on the root <html> element, False otherwise
    """
    return (
        violation.get("rule") == "html-has-lang"
        and violation.get("selector") == "html"
    )


def get_html_lang_fix() -> HtmlLangFix:
    """Get the singleton HtmlLangFix template.

    Returns:
        HtmlLangFix instance with template code and metadata
    """
    return _HTML_LANG_FIX
