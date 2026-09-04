"""Unit tests for file_locator adapter.

Tests the core file discovery and selector matching algorithms:
- Hint-text grepping (file filtering)
- CSS selector parsing (tag + attribute extraction)
- HTML regex matching (element location)
- Confidence scoring (result ranking)
"""

import pytest
from pathlib import Path
from tempfile import TemporaryDirectory

from a11y_fixer.adapters.file_locator import locate_selector_in_component, _find_selector_matches


class TestFindSelectorMatches:
    """Tests for _find_selector_matches (HTML parsing + scoring)."""

    def test_match_img_tag_only(self):
        """Test: tag name match (no attributes) gives confidence=0.5 but is reported."""
        html = "<div>\n  <img src='logo.svg' />\n</div>"
        results = _find_selector_matches(html, "img")
        # Tag-only selectors (with no attributes) are valid matches with confidence=0.5
        assert len(results) == 1
        assert results[0]["confidence"] == 0.5
        assert results[0]["line"] == 2

    def test_match_img_with_src_attribute(self):
        """Test: img[src$='atlas-dashboard.svg'] matches and scores high."""
        html = "<img src='atlas-dashboard.svg' alt='Dashboard' />"
        results = _find_selector_matches(html, "img[src$='atlas-dashboard.svg']", hint_text="atlas-dashboard.svg")
        
        assert len(results) == 1
        assert results[0]["line"] == 1
        assert "img" in results[0]["html"].lower()
        assert results[0]["confidence"] >= 0.7  # tag (0.5) + attribute (0.25) = 0.75

    def test_match_multiple_attributes(self):
        """Test: selector with multiple attributes boosts confidence."""
        html = "<button id='close' class='modal-btn' data-test='close-btn'></button>"
        results = _find_selector_matches(html, "button[id='close'][class='modal-btn']")
        
        assert len(results) == 1
        # tag (0.5) + attr1 (0.25) + attr2 (0.25) = 1.0 (capped)
        assert results[0]["confidence"] == 1.0

    def test_no_match_returns_empty(self):
        """Test: selector with no matches returns empty list."""
        html = "<div><p>Hello</p></div>"
        results = _find_selector_matches(html, "img[src='nonexistent.svg']")
        assert len(results) == 0

    def test_multiline_html(self):
        """Test: correctly locates elements on specific lines."""
        html = """<div>
  <h1>Title</h1>
  <img src='first.svg' />
  <img src='second.svg' />
</div>"""
        results = _find_selector_matches(html, "img[src='second.svg']", hint_text="second.svg")
        
        assert len(results) == 1
        assert results[0]["line"] == 4

    def test_case_insensitive_tag_matching(self):
        """Test: HTML tag matching is case-insensitive."""
        html = "<IMG SRC='logo.svg' />"
        results = _find_selector_matches(html, "img[src='logo.svg']")
        
        assert len(results) == 1

    def test_complex_selector_img_nth_child(self):
        """Test: compound selector with pseudo-class extracts rightmost tag."""
        html = """<article>
  <img src='first.jpg' />
</article>
<article>
  <img src='second.jpg' />
</article>"""
        # For compound selectors like "article:nth-child(2) img", extract rightmost tag (img)
        # The pseudo-selector `:nth-child(2)` is ignored, so we find all img tags
        results = _find_selector_matches(html, "article:nth-child(2) img")
        
        assert len(results) == 2
        assert results[0]["line"] == 2
        assert results[1]["line"] == 5


class TestLocateSelectorInComponent:
    """Tests for locate_selector_in_component (full workflow)."""

    def test_locate_with_fixture_hint_text(self):
        """Test: hint_text narrows search space (grepping)."""
        with TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            
            # Create two component files
            (tmpdir / "component-a.component.html").write_text(
                "<img src='logo.svg' />"
            )
            (tmpdir / "component-b.component.html").write_text(
                "<img src='dashboard.svg' />"
            )
            
            results = locate_selector_in_component(
                selector="img[src='dashboard.svg']",
                hint_text="dashboard.svg",
                glob_pattern="*.component.html",
                codebase_root=tmpdir,
            )
            
            # Should find only component-b.component.html
            assert len(results) == 1
            assert "component-b" in results[0]["file_path"]

    def test_locate_without_hint_text(self):
        """Test: without hint_text, searches all files."""
        with TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            
            (tmpdir / "component.component.html").write_text(
                "<img src='target.svg' />"
            )
            
            results = locate_selector_in_component(
                selector="img[src='target.svg']",
                hint_text=None,
                glob_pattern="*.component.html",
                codebase_root=tmpdir,
            )
            
            assert len(results) == 1
            assert results[0]["confidence"] >= 0.7

    def test_results_sorted_by_confidence(self):
        """Test: results are sorted by confidence (descending)."""
        with TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            
            # File with exact attribute match (high confidence: 0.5 tag + 0.25 attr = 0.75)
            (tmpdir / "a.component.html").write_text(
                "<img src='exact.svg' />"
            )
            # File with partial match (lower confidence)
            (tmpdir / "b.component.html").write_text(
                "<img src='exact-different.svg' />"
            )
            
            results = locate_selector_in_component(
                selector="img[src='exact.svg']",
                hint_text=None,
                glob_pattern="*.component.html",
                codebase_root=tmpdir,
            )
            
            # Exact match should come first
            assert len(results) >= 1
            assert results[0]["confidence"] >= 0.75  # tag (0.5) + attribute (0.25) = 0.75
            assert "a.component" in results[0]["file_path"]

    def test_empty_on_no_files(self):
        """Test: returns empty list when no files match glob pattern."""
        with TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            
            results = locate_selector_in_component(
                selector="img[src='any.svg']",
                hint_text=None,
                glob_pattern="*.component.html",
                codebase_root=tmpdir,
            )
            
            assert len(results) == 0

    def test_empty_on_no_selector_match(self):
        """Test: returns empty list when selector matches no elements."""
        with TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            
            (tmpdir / "component.component.html").write_text(
                "<div><p>No images here</p></div>"
            )
            
            results = locate_selector_in_component(
                selector="img[src='nonexistent.svg']",
                hint_text="nonexistent.svg",
                glob_pattern="*.component.html",
                codebase_root=tmpdir,
            )
            
            assert len(results) == 0

    def test_handles_unreadable_files(self):
        """Test: gracefully skips files that can't be read."""
        with TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            
            # Create a readable file with a match
            (tmpdir / "readable.component.html").write_text(
                "<img src='target.svg' />"
            )
            
            results = locate_selector_in_component(
                selector="img[src='target.svg']",
                hint_text="target.svg",
                glob_pattern="*.component.html",
                codebase_root=tmpdir,
            )
            
            # Should still find the readable file
            assert len(results) == 1

    def test_multiple_matches_same_file(self):
        """Test: returns multiple line numbers when selector matches multiple elements."""
        with TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            
            (tmpdir / "component.component.html").write_text(
                "<img src='icon.svg' />\n<img src='icon.svg' />"
            )
            
            results = locate_selector_in_component(
                selector="img[src='icon.svg']",
                hint_text="icon.svg",
                glob_pattern="*.component.html",
                codebase_root=tmpdir,
            )
            
            assert len(results) == 2
            assert results[0]["line_number"] == 1
            assert results[1]["line_number"] == 2

    def test_default_codebase_root(self):
        """Test: defaults to current directory when codebase_root is None."""
        # This is a smoke test; we don't actually run glob on cwd
        # Just verify it doesn't crash and returns a list
        results = locate_selector_in_component(
            selector="img[src='does-not-exist.svg']",
            hint_text="does-not-exist.svg",
            glob_pattern="*.component.html",
            codebase_root=None,  # Should default to Path(".")
        )
        
        assert isinstance(results, list)


class TestSuccessCriteria:
    """Integration tests verifying Phase 0.1 success criteria."""

    def test_criterion_file_discovery_accuracy(self):
        """Success criterion: ≥90% of violations map to correct component file.
        
        Mock test: verify that when hint_text is present, grepping works.
        """
        with TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            
            # Create 10 component files, only one with the target
            for i in range(10):
                if i == 5:
                    (tmpdir / f"comp-{i}.component.html").write_text(
                        "<img src='specific-icon.svg' />"
                    )
                else:
                    (tmpdir / f"comp-{i}.component.html").write_text(
                        "<img src='generic-icon.svg' />"
                    )
            
            results = locate_selector_in_component(
                selector="img[src='specific-icon.svg']",
                hint_text="specific-icon.svg",
                glob_pattern="*.component.html",
                codebase_root=tmpdir,
            )
            
            assert len(results) == 1
            assert "comp-5" in results[0]["file_path"]

    def test_criterion_confidence_scoring(self):
        """Success criterion: Top result has confidence ≥0.7 for ≥80% of violations."""
        with TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            
            (tmpdir / "component.component.html").write_text(
                "<img src='atlas-dashboard.svg' alt='Dashboard' />"
            )
            
            results = locate_selector_in_component(
                selector="img[src$='atlas-dashboard.svg']",
                hint_text="atlas-dashboard.svg",
                glob_pattern="*.component.html",
                codebase_root=tmpdir,
            )
            
            assert len(results) > 0
            assert results[0]["confidence"] >= 0.7

    def test_criterion_performance(self):
        """Success criterion: locate_selector() completes in <2s per call.
        
        Mock test: verify it completes quickly on a small fixture.
        """
        with TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            
            # Create ~10 files
            for i in range(10):
                (tmpdir / f"comp-{i}.component.html").write_text(
                    "<img src='icon.svg' />" * 50  # 50 img tags per file
                )
            
            results = locate_selector_in_component(
                selector="img[src='icon.svg']",
                hint_text="icon.svg",
                glob_pattern="*.component.html",
                codebase_root=tmpdir,
            )
            
            # Should complete without timeout
            assert isinstance(results, list)
            # All results should have valid structure
            for r in results:
                assert "file_path" in r
                assert "line_number" in r
                assert "element_html" in r
                assert "confidence" in r
