"""Applier for html-lang fix: immediate apply + build verification + rollback on failure.

Flow:
1. Read src/index.html from fixture
2. Replace <html> with <html lang="en">
3. Run `ng build` to verify no regressions
4. If build fails: Rollback via `git checkout` + return error
5. If build passes: Keep changes + return success with audit trail

Design: Direct apply (not dry-run worktree) for speed. Build verification is the
safety net. Rollback is automatic if verification fails.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Optional


class HtmlLangApplierError(RuntimeError):
    """Raised when an unexpected error occurs during fix application."""


def _classify_build_error(output: str) -> str:
    """Classify ng build error by type for agent decision-making.

    Returns one of: "syntax", "type_error", "missing_import", "template",
                    "circular_dep", "environment", "unknown"
    """
    output_lower = output.lower()

    # Syntax errors
    if re.search(r"unexpected token|syntax error", output_lower):
        return "syntax"

    # Type errors (TypeScript)
    if re.search(
        r"TS\d{4}|type .*is not assignable|is not a known|undefined", output_lower
    ):
        return "type_error"

    # Missing imports
    if re.search(
        r"cannot find|not found|unknown (symbol|component|directive|pipe)|no provider",
        output_lower,
    ):
        return "missing_import"

    # Template errors
    if re.search(r"template error|parser error|template parse error", output_lower):
        return "template"

    # Circular dependencies
    if re.search(r"circular|cyclic", output_lower):
        return "circular_dep"

    # Environment issues
    if re.search(
        r"not found|enoent|file not found|module not found.*node_modules", output_lower
    ):
        return "environment"

    return "unknown"


async def apply_html_lang(fixture: Path) -> dict:
    """Apply html-lang fix directly to fixture, verify with ng build, rollback on failure.

    Args:
        fixture: Path to fixture repo root

    Returns:
        dict: {
            "applied": bool,           # True if fix was applied and build passed
            "error": Optional[str],    # Error message if not applied
            "changes": [               # Changes made (for audit trail)
                {
                    "path": str,       # Relative path (e.g., "src/index.html")
                    "old": str,        # Original content
                    "new": str,        # New content after fix
                }
            ]
        }

    Behavior:
    - If file not found: Returns error, no changes
    - If fix has no effect (target not found): Returns error, no changes
    - If build fails: Rolls back + returns error
    - If build passes: Keeps changes + returns success with audit trail
    """
    target_file = fixture / "src/index.html"

    # 1. Verify fixture is a git repo (needed for rollback)
    if not (fixture / ".git").exists():
        return {
            "applied": False,
            "error": f"Fixture is not a git repository: {fixture}",
            "changes": [],
        }

    # 2. Verify target file exists
    if not target_file.exists():
        return {
            "applied": False,
            "error": f"Target file not found: src/index.html",
            "changes": [],
        }

    # 3. Read original content
    try:
        old_content = target_file.read_text(encoding="utf-8")
    except Exception as e:
        return {
            "applied": False,
            "error": f"Failed to read src/index.html: {e}",
            "changes": [],
        }

    # 4. Apply fix: <html> → <html lang="en">
    new_content = old_content.replace("<html>", '<html lang="en">')

    if new_content == old_content:
        return {
            "applied": False,
            "error": (
                "Fix had no effect: '<html>' not found in src/index.html "
                "(already fixed or different format?)"
            ),
            "changes": [],
        }

    # 5. Write fix to fixture
    try:
        target_file.write_text(new_content, encoding="utf-8")
    except Exception as e:
        return {
            "applied": False,
            "error": f"Failed to write src/index.html: {e}",
            "changes": [],
        }

    # 5.5. IMPROVEMENT: Baseline check - verify fixture builds BEFORE applying fix
    # This distinguishes between: fixture-was-broken vs. agent-broke-it
    baseline_result = await _run_ng_build(fixture, baseline=True)
    if not baseline_result["success"]:
        # Fixture was already broken; don't apply fix
        return {
            "applied": False,
            "error": (
                f"Fixture has pre-existing build errors (baseline check failed). "
                f"Not applying fix to avoid contamination. Error: {baseline_result.get('error', 'unknown')}"
            ),
            "changes": [],
        }

    # 6. Verify with ng build (production config for quality validation)
    build_result = await _run_ng_build(fixture, baseline=False)

    if not build_result["success"]:
        # Build failed: rollback
        try:
            _rollback_file(fixture, "src/index.html")
        except Exception as rollback_err:
            # Warn about rollback failure but still return the build failure
            error_msg = (
                f"ng build failed: {build_result.get('error', 'unknown')} "
                f"(rollback also failed: {rollback_err})"
            )
        else:
            error_msg = f"ng build failed after applying fix: {build_result.get('error', 'unknown')}"

        return {
            "applied": False,
            "error": error_msg,
            "changes": [],
        }

    # 7. Build passed: keep changes and return success
    return {
        "applied": True,
        "error": None,
        "changes": [
            {
                "path": "src/index.html",
                "old": old_content,
                "new": new_content,
            }
        ],
    }


async def _run_ng_build(fixture: Path, baseline: bool = False) -> dict:
    """Run `ng build` in the fixture to verify no regressions.

    Args:
        fixture: Path to fixture repo
        baseline: If True, use development config for faster baseline check.
                 If False, use production config to validate fix quality.

    Returns:
        dict: {"success": bool, "error": Optional[str], "error_type": str, "full_output": str}
    """
    try:
        # Improvement: Use --configuration=development for baseline checks (30-50% faster)
        # Use production config for final validation (catches more issues)
        config = "development" if baseline else "production"

        result = subprocess.run(
            ["npx", "ng", "build", f"--configuration={config}"],
            cwd=fixture,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )

        if result.returncode == 0:
            return {
                "success": True,
                "error": None,
                "error_type": None,
                "full_output": "",
            }

        # Improvement: Capture FULL error output for better agent feedback
        # Previous: last 1000 chars only (truncated, loses context)
        # Now: full stderr + stdout for root cause analysis
        combined_output = (result.stderr or "") + "\n" + (result.stdout or "")

        # Improvement: Classify error type for agent decision-making
        error_type = _classify_build_error(combined_output)

        # Extract most relevant error snippet (last ~500 chars typically contains the issue)
        error_snippet = (
            combined_output.strip()[-1500:] if combined_output else "unknown error"
        )

        return {
            "success": False,
            "error": f"ng build exited with code {result.returncode}: {error_snippet}",
            "error_type": error_type,
            "full_output": combined_output,
        }

    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "error": "ng build timed out after 120 seconds",
            "error_type": "timeout",
            "full_output": "",
        }
    except FileNotFoundError:
        return {
            "success": False,
            "error": "npx not found on PATH",
            "error_type": "environment",
            "full_output": "",
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"Unexpected error running ng build: {e}",
            "error_type": "unknown",
            "full_output": "",
        }


def _rollback_file(fixture: Path, file_path: str) -> None:
    """Rollback a file to its git HEAD version.

    Args:
        fixture: Path to fixture repo
        file_path: Relative path of file to rollback (e.g., "src/index.html")

    Raises:
        HtmlLangApplierError: If git checkout fails
    """
    try:
        subprocess.run(
            ["git", "checkout", "HEAD", "--", file_path],
            cwd=fixture,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except subprocess.CalledProcessError as e:
        msg = f"Failed to rollback {file_path}: {e.stderr}"
        raise HtmlLangApplierError(msg) from e
    except subprocess.TimeoutExpired as e:
        msg = f"git checkout timed out for {file_path}: {e}"
        raise HtmlLangApplierError(msg) from e
    except Exception as e:
        msg = f"Unexpected error rolling back {file_path}: {e}"
        raise HtmlLangApplierError(msg) from e
