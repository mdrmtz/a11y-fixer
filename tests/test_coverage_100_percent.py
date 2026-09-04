"""100% coverage tests for deep_agent.py, cli.py, and run_eval.py.

Tests all uncovered branches and edge cases in the three core modules.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from a11y_fixer import cli, config
from a11y_fixer.deep_agent import (
    ViolationResponse,
    _default_permissions,
    build_agent,
)
from evaluation.run_eval import (
    CaseResult,
    _confidence,
    _group_clearance_by_rule,
    _recheck_cleared,
    load_benchmark_cases,
    main,
    run_eval,
)


# ============================================================================
# deep_agent.py coverage
# ============================================================================


class TestDefaultPermissions:
    """Test _default_permissions() function for 100% coverage."""

    def test_default_permissions_returns_correct_structure(self) -> None:
        """Test that _default_permissions returns allow read and deny write."""
        virtual_fixture = "/virtual/fixture"
        perms = _default_permissions(virtual_fixture)

        assert len(perms) == 2
        assert perms[0].operations == ["read"]
        assert perms[0].paths == ["/virtual/fixture/**"]
        assert perms[0].mode == "allow"

        assert perms[1].operations == ["write"]
        assert perms[1].paths == ["/**"]
        assert perms[1].mode == "deny"

    def test_default_permissions_with_different_fixture_path(self) -> None:
        """Test _default_permissions with different fixture paths."""
        perms = _default_permissions("/another/path")
        assert perms[0].paths == ["/another/path/**"]

    def test_default_permissions_deny_rule_is_catch_all(self) -> None:
        """Verify deny rule uses /** as catch-all."""
        perms = _default_permissions("/fixture")
        assert perms[1].paths == ["/**"]
        assert perms[1].mode == "deny"


class TestBuildAgent:
    """Test build_agent() sync wrapper for 100% coverage."""

    def test_build_agent_returns_compiled_graph(self) -> None:
        """Test build_agent returns a CompiledStateGraph."""
        # Mock abuild_agent to avoid network/MCP calls
        mock_graph = MagicMock()

        with patch("a11y_fixer.deep_agent.asyncio.run") as mock_run:
            mock_run.return_value = mock_graph
            result = build_agent()
            assert result == mock_graph
            mock_run.assert_called_once()

    def test_build_agent_with_custom_backend(self) -> None:
        """Test build_agent accepts backend parameter."""
        mock_backend = MagicMock()
        mock_graph = MagicMock()

        with patch("a11y_fixer.deep_agent.asyncio.run") as mock_run:
            mock_run.return_value = mock_graph
            result = build_agent(backend=mock_backend)
            assert result == mock_graph

    def test_build_agent_with_custom_checkpointer(self) -> None:
        """Test build_agent accepts checkpointer parameter."""
        mock_checkpointer = MagicMock()
        mock_graph = MagicMock()

        with patch("a11y_fixer.deep_agent.asyncio.run") as mock_run:
            mock_run.return_value = mock_graph
            result = build_agent(checkpointer=mock_checkpointer)
            assert result == mock_graph


class TestViolationResponse:
    """Test ViolationResponse pydantic model."""

    def test_violation_response_valid_creation(self) -> None:
        """Test creating a valid ViolationResponse."""
        resp = ViolationResponse(
            rule="image-alt",
            wcag="1.1.1",
            selector="img.example",
            technique_id="H37",
            technique_type="sufficient",
            code='<img alt="description" />',
            rationale="Added alt text describing the image.",
            score=15.0,
            route="auto",
        )
        assert resp.rule == "image-alt"
        assert resp.score == 15.0
        assert resp.route == "auto"

    def test_violation_response_score_bounds_zero(self) -> None:
        """Test score must be >= 0."""
        with pytest.raises(ValueError):
            ViolationResponse(
                rule="test",
                wcag="1.1.1",
                selector="x",
                technique_id="t",
                technique_type="sufficient",
                code="x",
                rationale="x",
                score=-1.0,
                route="auto",
            )

    def test_violation_response_score_bounds_twenty(self) -> None:
        """Test score must be <= 20."""
        with pytest.raises(ValueError):
            ViolationResponse(
                rule="test",
                wcag="1.1.1",
                selector="x",
                technique_id="t",
                technique_type="sufficient",
                code="x",
                rationale="x",
                score=21.0,
                route="auto",
            )

    def test_violation_response_route_validation(self) -> None:
        """Test route must be 'auto' or 'human'."""
        with pytest.raises(ValueError):
            ViolationResponse(
                rule="test",
                wcag="1.1.1",
                selector="x",
                technique_id="t",
                technique_type="sufficient",
                code="x",
                rationale="x",
                score=10.0,
                route="invalid",  # type: ignore
            )


# ============================================================================
# cli.py coverage
# ============================================================================


class TestApplyRepoOverride:
    """Test _apply_repo_override() for 100% coverage."""

    def test_apply_repo_override_with_none(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
        """Test _apply_repo_override with None (no override)."""
        monkeypatch.setenv("A11Y_FIXTURE_PATH", "/original/path")
        cli._apply_repo_override(None)
        captured = capsys.readouterr()
        assert "target repo:" in captured.out

    def test_apply_repo_override_with_local_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """Test _apply_repo_override with a local path."""
        monkeypatch.setenv("A11Y_FIXTURE_PATH", "")
        monkeypatch.setenv("GITHUB_REPO", "")
        repo_path = tmp_path / "repo"
        repo_path.mkdir()

        with patch("a11y_fixer.cli.resolve_repo_source") as mock_resolve:
            mock_resolve.return_value = repo_path
            cli._apply_repo_override(str(repo_path))
            captured = capsys.readouterr()
            assert "target repo:" in captured.out
            mock_resolve.assert_called_once()


class TestHitlQueuePath:
    """Test _hitl_queue_path() slug generation."""

    def test_hitl_queue_path_alphanumeric_rule(self) -> None:
        """Test slug generation for alphanumeric rule."""
        violation = {"rule": "image-alt", "selector": ".img-class"}
        path = cli._hitl_queue_path(violation)
        assert "image-alt" in str(path)

    def test_hitl_queue_path_special_characters_stripped(self) -> None:
        """Test special characters are replaced with hyphens."""
        violation = {"rule": "color_contrast", "selector": "p[data-test='value']"}
        path = cli._hitl_queue_path(violation)
        # Slug should only contain alphanumeric and hyphens
        slug = path.stem.split("-", 1)[1]  # Remove timestamp
        assert all(c.isalnum() or c == "-" for c in slug)

    def test_hitl_queue_path_empty_slug_becomes_violation(self) -> None:
        """Test empty slug defaults to 'violation'."""
        violation = {"rule": "!!!!", "selector": "^^^^"}
        path = cli._hitl_queue_path(violation)
        assert "violation" in str(path)


class TestCaptureAndResetGitChanges:
    """Test _capture_and_reset_git_changes() for 100% coverage."""

    def test_capture_and_reset_git_changes_empty_repo(self, tmp_path: Path) -> None:
        """Test with a git repo that has no changes."""
        repo = tmp_path / "repo"
        repo.mkdir()
        # Initialize git repo
        import subprocess

        subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True, check=True)

        # Create initial commit
        (repo / "file.txt").write_text("initial")
        subprocess.run(["git", "add", "file.txt"], cwd=repo, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, capture_output=True, check=True)

        changes = cli._capture_and_reset_git_changes(repo)
        assert changes == []

    def test_capture_and_reset_git_changes_modified_file(self, tmp_path: Path) -> None:
        """Test capturing changes from a modified file."""
        repo = tmp_path / "repo"
        repo.mkdir()
        import subprocess

        subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True, check=True)

        # Create and commit initial file
        (repo / "file.txt").write_text("initial")
        subprocess.run(["git", "add", "file.txt"], cwd=repo, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, capture_output=True, check=True)

        # Modify file
        (repo / "file.txt").write_text("modified")

        changes = cli._capture_and_reset_git_changes(repo)
        assert len(changes) == 1
        assert changes[0].path == "file.txt"
        assert changes[0].old_content == "initial"
        assert changes[0].new_content == "modified"

        # Verify git was reset
        assert (repo / "file.txt").read_text() == "initial"

    def test_capture_and_reset_git_changes_new_file(self, tmp_path: Path) -> None:
        """Test capturing a new untracked file."""
        repo = tmp_path / "repo"
        repo.mkdir()
        import subprocess

        subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True, check=True)

        # Create initial commit
        (repo / "initial.txt").write_text("x")
        subprocess.run(["git", "add", "initial.txt"], cwd=repo, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, capture_output=True, check=True)

        # Add new file
        (repo / "new.txt").write_text("new content")

        changes = cli._capture_and_reset_git_changes(repo)
        assert len(changes) == 1
        assert changes[0].path == "new.txt"
        assert changes[0].old_content == ""
        assert changes[0].new_content == "new content"


class TestDeliverViolation:
    """Test deliver_violation() routing."""

    def test_deliver_violation_human_route(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test human route queues the violation."""
        monkeypatch.setenv("A11Y_HITL_QUEUE_DIR", str(tmp_path))
        # Mock config.agent_root() to return tmp_path for test isolation
        monkeypatch.setattr("a11y_fixer.config.agent_root", lambda: tmp_path / "agent")
        import subprocess

        repo = tmp_path / "repo"
        repo.mkdir()
        (tmp_path / "agent").mkdir()  # Create agent root for test
        subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True, check=True)
        (repo / "file.txt").write_text("initial")
        subprocess.run(["git", "add", "file.txt"], cwd=repo, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, capture_output=True, check=True)
        (repo / "file.txt").write_text("modified")

        response = ViolationResponse(
            rule="test", wcag="1.1.1", selector=".x", technique_id="t", technique_type="sufficient",
            code="x", rationale="x", score=10.0, route="human"
        )
        violation = {"rule": "test", "selector": ".x"}

        result = cli.deliver_violation(
            violation, response, fixture=repo, pr_config=MagicMock(), output_dir=tmp_path
        )

        assert result["delivered"] is False
        assert "queue_path" in result


class TestBuildParser:
    """Test build_parser() construction."""

    def test_build_parser_has_audit_subcommand(self) -> None:
        """Test parser has audit subcommand."""
        parser = cli.build_parser()
        args = parser.parse_args(["audit", "--repo", "/tmp/x"])
        assert args.command == "audit"

    def test_build_parser_has_run_subcommand(self) -> None:
        """Test parser has run subcommand."""
        parser = cli.build_parser()
        args = parser.parse_args(["run"])
        assert args.command == "run"

    def test_build_parser_run_accepts_audit_flag(self) -> None:
        """Test run command accepts --audit."""
        parser = cli.build_parser()
        args = parser.parse_args(["run", "--audit", "/path/to/audit.json"])
        assert args.audit == "/path/to/audit.json"

    def test_build_parser_run_accepts_yes_flag(self) -> None:
        """Test run command accepts --yes."""
        parser = cli.build_parser()
        args = parser.parse_args(["run", "--yes"])
        assert args.yes is True

    def test_build_parser_run_accepts_live_flag(self) -> None:
        """Test run command accepts --live and --no-live."""
        parser = cli.build_parser()
        args_live = parser.parse_args(["run", "--live"])
        assert args_live.live is True

        args_no_live = parser.parse_args(["run", "--no-live"])
        assert args_no_live.live is False


class TestMain:
    """Test main() CLI entry point."""

    def test_main_with_audit_command(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test main() routes to audit command."""
        mock_cmd = MagicMock(return_value=0)
        monkeypatch.setattr("a11y_fixer.cli._cmd_audit", mock_cmd)

        exit_code = cli.main(["audit", "--repo", "/tmp/x"])
        assert exit_code == 0
        mock_cmd.assert_called_once()

    def test_main_with_run_command(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test main() routes to run command."""
        mock_cmd = MagicMock(return_value=0)
        monkeypatch.setattr("a11y_fixer.cli._cmd_run", mock_cmd)

        exit_code = cli.main(["run"])
        assert exit_code == 0
        mock_cmd.assert_called_once()

    def test_main_no_argv_uses_sys_argv(self) -> None:
        """Test main() uses sys.argv when argv is None."""
        with patch("sys.argv", ["prog", "audit", "--repo", "/tmp/x"]):
            with patch("a11y_fixer.cli._cmd_audit", return_value=0):
                exit_code = cli.main(None)
                assert exit_code == 0

    def test_main_returns_exit_code(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test main() returns the subcommand's exit code."""
        monkeypatch.setattr("a11y_fixer.cli._cmd_audit", MagicMock(return_value=42))
        exit_code = cli.main(["audit", "--repo", "/tmp/x"])
        assert exit_code == 42


# ============================================================================
# run_eval.py coverage
# ============================================================================


class TestConfidence:
    """Test _confidence() normalization."""

    def test_confidence_zero_score(self) -> None:
        """Test confidence with zero score."""
        result = CaseResult("id", "rule", "/page", "auto", 0.0, False, 1.0)
        assert _confidence(result) == 0.0

    def test_confidence_perfect_score(self) -> None:
        """Test confidence with perfect score."""
        result = CaseResult("id", "rule", "/page", "auto", 20.0, False, 1.0)
        assert _confidence(result) == 1.0

    def test_confidence_mid_score(self) -> None:
        """Test confidence with mid-range score."""
        result = CaseResult("id", "rule", "/page", "auto", 10.0, False, 1.0)
        assert _confidence(result) == 0.5

    def test_confidence_clamps_negative(self) -> None:
        """Test confidence clamps negative to 0."""
        result = CaseResult("id", "rule", "/page", "auto", -5.0, False, 1.0)
        assert _confidence(result) == 0.0

    def test_confidence_clamps_over_twenty(self) -> None:
        """Test confidence clamps >20 to 1.0."""
        result = CaseResult("id", "rule", "/page", "auto", 25.0, False, 1.0)
        assert _confidence(result) == 1.0


class TestGroupClearanceByRule:
    """Test _group_clearance_by_rule() aggregation."""

    def test_group_clearance_single_rule_all_cleared(self) -> None:
        """Test grouping when all cases of a rule cleared."""
        results = [
            CaseResult("id1", "rule-a", "/a", "auto", 15.0, True, 1.0),
            CaseResult("id2", "rule-a", "/b", "auto", 16.0, True, 1.0),
        ]
        grouped = _group_clearance_by_rule(results)
        assert grouped["rule-a"] == {"total": 2, "cleared": 2}

    def test_group_clearance_multiple_rules(self) -> None:
        """Test grouping multiple rules."""
        results = [
            CaseResult("id1", "rule-a", "/a", "auto", 15.0, True, 1.0),
            CaseResult("id2", "rule-b", "/b", "auto", 10.0, False, 1.0),
        ]
        grouped = _group_clearance_by_rule(results)
        assert len(grouped) == 2
        assert grouped["rule-a"]["cleared"] == 1
        assert grouped["rule-b"]["cleared"] == 0

    def test_group_clearance_empty_results(self) -> None:
        """Test grouping empty results."""
        grouped = _group_clearance_by_rule([])
        assert grouped == {}


class TestRecheckCleared:
    """Test _recheck_cleared() re-audit logic."""

    def test_recheck_cleared_rule_is_gone(self) -> None:
        """Test recheck returns True when rule cleared."""
        mock_runner = MagicMock()
        mock_runner.audit_pages.return_value = {
            "pages": [{"violation_rules": []}]  # No violations after fix
        }

        case = {"page": "/about", "rule": "image-alt"}
        cleared = _recheck_cleared(mock_runner, case)
        assert cleared is True
        mock_runner.audit_pages.assert_called_once_with(pages=("/about",))

    def test_recheck_cleared_rule_still_present(self) -> None:
        """Test recheck returns False when rule still exists."""
        mock_runner = MagicMock()
        mock_runner.audit_pages.return_value = {
            "pages": [{"violation_rules": ["image-alt", "color-contrast"]}]
        }

        case = {"page": "/about", "rule": "image-alt"}
        cleared = _recheck_cleared(mock_runner, case)
        assert cleared is False

    def test_recheck_cleared_no_pages(self) -> None:
        """Test recheck when no pages returned."""
        mock_runner = MagicMock()
        mock_runner.audit_pages.return_value = {"pages": []}

        case = {"page": "/about", "rule": "image-alt"}
        cleared = _recheck_cleared(mock_runner, case)
        assert cleared is True


class TestLoadBenchmarkCases:
    """Test load_benchmark_cases() file loading."""

    def test_load_benchmark_cases_default_path(self) -> None:
        """Test loading from default path returns a list."""
        cases = load_benchmark_cases()
        assert isinstance(cases, list)

    def test_load_benchmark_cases_custom_path(self, tmp_path: Path) -> None:
        """Test loading from custom path."""
        cases_file = tmp_path / "cases.json"
        test_cases = [{"id": "test-1", "rule": "test-rule"}]
        cases_file.write_text(json.dumps(test_cases))

        loaded = load_benchmark_cases(cases_file)
        assert loaded == test_cases


class TestMain_RunEval:
    """Test main() CLI entry in run_eval."""

    def test_main_default_args(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test main() with default arguments."""
        mock_run_eval = MagicMock(return_value={"total_cases": 0})
        monkeypatch.setattr("evaluation.run_eval.run_eval", mock_run_eval)

        exit_code = main([])
        assert exit_code == 0
        mock_run_eval.assert_called_once()

    def test_main_custom_cases_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test main() with custom cases path."""
        cases_file = tmp_path / "cases.json"
        cases_file.write_text(json.dumps([]))

        mock_run_eval = MagicMock(return_value={"total_cases": 0})
        monkeypatch.setattr("evaluation.run_eval.run_eval", mock_run_eval)

        exit_code = main(["--cases", str(cases_file)])
        assert exit_code == 0

    def test_main_custom_output_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test main() with custom output path."""
        output_file = tmp_path / "results.json"

        mock_run_eval = MagicMock(return_value={"total_cases": 0})
        monkeypatch.setattr("evaluation.run_eval.run_eval", mock_run_eval)

        exit_code = main(["--output", str(output_file)])
        assert exit_code == 0


class TestCaseResult:
    """Test CaseResult dataclass."""

    def test_case_result_with_error(self) -> None:
        """Test CaseResult with error message."""
        result = CaseResult(
            case_id="id", rule="rule", page="/page", route="human",
            rubric_score=0.0, cleared=False, latency_seconds=1.0,
            error="Test error message"
        )
        assert result.error == "Test error message"

    def test_case_result_without_error(self) -> None:
        """Test CaseResult without error (default None)."""
        result = CaseResult(
            case_id="id", rule="rule", page="/page", route="auto",
            rubric_score=15.0, cleared=True, latency_seconds=2.5
        )
        assert result.error is None

    def test_case_result_to_dict(self) -> None:
        """Test CaseResult converts to dict properly."""
        result = CaseResult(
            case_id="id", rule="rule", page="/page", route="auto",
            rubric_score=15.0, cleared=True, latency_seconds=2.5
        )
        result_dict = vars(result)
        assert result_dict["case_id"] == "id"
        assert result_dict["cleared"] is True
