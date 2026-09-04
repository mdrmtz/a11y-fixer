"""Tests for html_lang_applier module."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from a11y_fixer.adapters.html_lang_applier import (
    HtmlLangApplierError,
    apply_html_lang,
)


@pytest.fixture
def fixture_dir(tmp_path: Path) -> Path:
    """Create a mock fixture directory with git repo."""
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    (fixture / ".git").mkdir()

    # Create src/index.html with basic structure
    src = fixture / "src"
    src.mkdir()
    (src / "index.html").write_text("<html>\n  <head><title>Test</title></head>\n</html>")

    return fixture


@pytest.mark.asyncio
async def test_apply_html_lang_success(fixture_dir: Path) -> None:
    """Should apply fix successfully when build passes."""
    with patch(
        "a11y_fixer.adapters.html_lang_applier._run_ng_build"
    ) as mock_build:
        mock_build.return_value = {"success": True, "error": None}

        result = await apply_html_lang(fixture_dir)

    assert result["applied"] is True
    assert result["error"] is None
    assert len(result["changes"]) == 1
    assert result["changes"][0]["path"] == "src/index.html"
    assert "<html lang=\"en\">" in result["changes"][0]["new"]
    assert "<html>" not in result["changes"][0]["new"]


@pytest.mark.asyncio
async def test_apply_html_lang_not_git_repo(tmp_path: Path) -> None:
    """Should fail gracefully if fixture is not a git repo."""
    fixture = tmp_path / "not_git"
    fixture.mkdir()
    (fixture / "src").mkdir()
    (fixture / "src" / "index.html").write_text("<html></html>")

    result = await apply_html_lang(fixture)

    assert result["applied"] is False
    assert "not a git repository" in result["error"].lower()
    assert result["changes"] == []


@pytest.mark.asyncio
async def test_apply_html_lang_file_not_found(fixture_dir: Path) -> None:
    """Should fail gracefully if src/index.html does not exist."""
    (fixture_dir / "src" / "index.html").unlink()

    result = await apply_html_lang(fixture_dir)

    assert result["applied"] is False
    assert "not found" in result["error"].lower()
    assert result["changes"] == []


@pytest.mark.asyncio
async def test_apply_html_lang_already_fixed(fixture_dir: Path) -> None:
    """Should fail if html-lang attribute already exists."""
    (fixture_dir / "src" / "index.html").write_text('<html lang="en"></html>')

    result = await apply_html_lang(fixture_dir)

    assert result["applied"] is False
    assert "no effect" in result["error"].lower()
    assert result["changes"] == []


@pytest.mark.asyncio
async def test_apply_html_lang_build_fails_and_rollbacks(fixture_dir: Path) -> None:
    """Should rollback if ng build fails after applying fix."""

    with patch(
        "a11y_fixer.adapters.html_lang_applier._run_ng_build"
    ) as mock_build:
        # apply_html_lang() now runs a baseline check (dev config) before the
        # real verification build (prod config) - see the "5.5. IMPROVEMENT:
        # Baseline check" step. The baseline call must succeed so the
        # verification call is the one that fails and triggers rollback,
        # matching what this test actually exercises.
        mock_build.side_effect = [
            {"success": True, "error": None},
            {"success": False, "error": "ng build exited"},
        ]

        with patch("a11y_fixer.adapters.html_lang_applier._rollback_file"):
            result = await apply_html_lang(fixture_dir)

    assert result["applied"] is False
    assert "ng build failed" in result["error"]
    assert result["changes"] == []


@pytest.mark.asyncio
async def test_apply_html_lang_build_fails_with_rollback_error(
    fixture_dir: Path,
) -> None:
    """Should report both build and rollback errors."""
    with patch(
        "a11y_fixer.adapters.html_lang_applier._run_ng_build"
    ) as mock_build:
        # Same baseline-then-verification sequencing as the test above: the
        # baseline call must succeed so control reaches the verification
        # build (whose failure is what should trigger the rollback path).
        mock_build.side_effect = [
            {"success": True, "error": None},
            {"success": False, "error": "ng build exited"},
        ]

        with patch(
            "a11y_fixer.adapters.html_lang_applier._rollback_file"
        ) as mock_rollback:
            mock_rollback.side_effect = HtmlLangApplierError("rollback failed")

            result = await apply_html_lang(fixture_dir)

    assert result["applied"] is False
    assert "rollback also failed" in result["error"]
    assert result["changes"] == []


@pytest.mark.asyncio
async def test_apply_html_lang_write_error(fixture_dir: Path) -> None:
    """Should handle write errors gracefully."""
    with patch.object(Path, "write_text", side_effect=IOError("Permission denied")):
        result = await apply_html_lang(fixture_dir)

    assert result["applied"] is False
    assert "Failed to write" in result["error"]
    assert result["changes"] == []


@pytest.mark.asyncio
async def test_apply_html_lang_read_error(fixture_dir: Path) -> None:
    """Should handle read errors gracefully."""
    with patch.object(Path, "read_text", side_effect=IOError("Permission denied")):
        result = await apply_html_lang(fixture_dir)

    assert result["applied"] is False
    assert "Failed to read" in result["error"]
    assert result["changes"] == []


@pytest.mark.asyncio
async def test_rollback_file_success() -> None:
    """Should successfully rollback file via git checkout."""
    mock_fixture = Path("/mock/repo")

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)

        from a11y_fixer.adapters.html_lang_applier import _rollback_file

        _rollback_file(mock_fixture, "src/index.html")

        mock_run.assert_called_once()
        call_args = mock_run.call_args
        assert call_args[0][0][0] == "git"
        assert call_args[0][0][1] == "checkout"
        assert call_args[1]["cwd"] == mock_fixture


@pytest.mark.asyncio
async def test_rollback_file_failure() -> None:
    """Should raise error if git checkout fails."""
    mock_fixture = Path("/mock/repo")

    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.CalledProcessError(
            1, "git", stderr="fatal error"
        )

        from a11y_fixer.adapters.html_lang_applier import _rollback_file

        with pytest.raises(HtmlLangApplierError, match="Failed to rollback"):
            _rollback_file(mock_fixture, "src/index.html")


@pytest.mark.asyncio
async def test_run_ng_build_timeout() -> None:
    """Should handle ng build timeout."""
    from a11y_fixer.adapters.html_lang_applier import _run_ng_build

    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.TimeoutExpired("ng", 120)

        result = await _run_ng_build(Path("/mock"))

    assert result["success"] is False
    assert "timed out" in result["error"].lower()


@pytest.mark.asyncio
async def test_run_ng_build_missing_npx() -> None:
    """Should handle missing npx gracefully."""
    from a11y_fixer.adapters.html_lang_applier import _run_ng_build

    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = FileNotFoundError("npx not found")

        result = await _run_ng_build(Path("/mock"))

    assert result["success"] is False
    assert "not found" in result["error"].lower()
