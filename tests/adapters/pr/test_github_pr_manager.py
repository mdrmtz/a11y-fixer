"""Tests for GitHub PR manager: auto-merge, close, deduplication."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from a11y_fixer.adapters.pr.github_pr_manager import (
    GitHubPRManager,
    GitHubPRManagerError,
    PRCloseResult,
    PRMergeResult,
)


class TestGitHubPRManagerInit:
    """Tests for initialization."""

    def test_valid_repo_format(self):
        """Valid owner/repo format initializes successfully."""
        mgr = GitHubPRManager(github_token="token", github_repo="mdrmtz/Hallucinate.io")

        assert mgr.owner == "mdrmtz"
        assert mgr.repo == "Hallucinate.io"

    def test_invalid_repo_format(self):
        """Invalid format raises error."""
        with pytest.raises(GitHubPRManagerError, match="owner/repo"):
            GitHubPRManager(github_token="token", github_repo="invalid")


class TestAutoMergePR:
    """Tests for PR auto-merge logic."""

    @pytest.fixture
    def manager(self):
        return GitHubPRManager(github_token="test-token", github_repo="owner/repo")

    def test_merge_high_score(self, manager):
        """Score >= threshold triggers merge."""
        with patch("httpx.Client") as MockClient:
            mock_client = MagicMock()
            MockClient.return_value.__enter__.return_value = mock_client

            # Mock PR exists and is open
            pr_resp = MagicMock()
            pr_resp.status_code = 200
            pr_resp.json.return_value = {"state": "open", "number": 42}

            # Mock successful merge
            merge_resp = MagicMock()
            merge_resp.status_code = 200

            mock_client.get.return_value = pr_resp
            mock_client.put.return_value = merge_resp

            result = manager.auto_merge_pr(pr_number=42, score=19.0, merge_threshold=18.0)

            assert result.success
            assert result.pr_number == 42
            assert "auto_merged" in result.reason

    def test_skip_low_score(self, manager):
        """Score below threshold skips merge."""
        result = manager.auto_merge_pr(pr_number=42, score=15.0, merge_threshold=18.0)

        assert not result.success
        assert result.pr_number == 42
        assert "below_threshold" in result.reason


class TestClosePRAsSuperseded:
    """Tests for closing PR as superseded."""

    @pytest.fixture
    def manager(self):
        return GitHubPRManager(github_token="test-token", github_repo="owner/repo")

    def test_close_with_comment(self, manager):
        """Closing PR adds supersede comment."""
        with patch("httpx.Client") as MockClient:
            mock_client = MagicMock()
            MockClient.return_value.__enter__.return_value = mock_client

            # Mock close and comment responses
            close_resp = MagicMock()
            close_resp.status_code = 200

            comment_resp = MagicMock()
            comment_resp.status_code = 201

            mock_client.patch.return_value = close_resp
            mock_client.post.return_value = comment_resp

            result = manager.close_pr_as_superseded(
                pr_number=2, new_pr_number=10, old_score=15.0, new_score=19.0
            )

            assert result.success
            assert result.pr_number == 2
            assert "superseded" in result.reason

            # Verify comment was posted
            mock_client.post.assert_called_once()
            call_args = mock_client.post.call_args
            assert "issues/2/comments" in call_args[0][0]


class TestClosePRAsDuplicate:
    """Tests for closing PR as duplicate."""

    @pytest.fixture
    def manager(self):
        return GitHubPRManager(github_token="test-token", github_repo="owner/repo")

    def test_close_duplicate(self, manager):
        """Closing duplicate PR adds duplicate comment."""
        with patch("httpx.Client") as MockClient:
            mock_client = MagicMock()
            MockClient.return_value.__enter__.return_value = mock_client

            close_resp = MagicMock()
            close_resp.status_code = 200

            comment_resp = MagicMock()
            comment_resp.status_code = 201

            mock_client.patch.return_value = close_resp
            mock_client.post.return_value = comment_resp

            result = manager.close_pr_as_duplicate(pr_number=3, kept_pr_number=2)

            assert result.success
            assert result.pr_number == 3
            assert "duplicate" in result.reason


class TestSearchPRsByViolationId:
    """Tests for finding PRs by violation ID."""

    @pytest.fixture
    def manager(self):
        return GitHubPRManager(github_token="test-token", github_repo="owner/repo")

    def test_search_finds_matching_prs(self, manager):
        """Search returns PRs with violation ID in title."""
        with patch("httpx.Client") as MockClient:
            mock_client = MagicMock()
            MockClient.return_value.__enter__.return_value = mock_client

            search_resp = MagicMock()
            search_resp.status_code = 200
            search_resp.json.return_value = {
                "items": [
                    {"number": 2, "pull_request": {"url": "..."}},
                    {"number": 3, "pull_request": {"url": "..."}},
                    {"number": 4, "pull_request": {"url": "..."}},
                ]
            }

            mock_client.get.return_value = search_resp

            results = manager.search_prs_by_violation_id("abc123")

            assert len(results) == 3
            assert results[0]["number"] == 2


class TestCleanupDuplicatePRs:
    """Tests for deduplication cleanup."""

    @pytest.fixture
    def manager(self):
        return GitHubPRManager(github_token="test-token", github_repo="owner/repo")

    def test_cleanup_closes_all_duplicates(self, manager):
        """Cleanup closes all duplicates except kept PR."""
        with patch.object(manager, "search_prs_by_violation_id") as mock_search, patch.object(
            manager, "close_pr_as_duplicate"
        ) as mock_close:

            # Simulate finding PRs #2-9
            mock_search.return_value = [
                {"number": 2, "pull_request": {"url": "..."}},
                {"number": 3, "pull_request": {"url": "..."}},
                {"number": 4, "pull_request": {"url": "..."}},
                {"number": 5, "pull_request": {"url": "..."}},
                {"number": 6, "pull_request": {"url": "..."}},
                {"number": 7, "pull_request": {"url": "..."}},
                {"number": 8, "pull_request": {"url": "..."}},
                {"number": 9, "pull_request": {"url": "..."}},
            ]

            # Mock close results
            mock_close.return_value = PRCloseResult(
                success=True, pr_number=0, reason="duplicate"
            )

            # Keep PR #10, close #2-9
            results = manager.cleanup_duplicate_prs(
                violation_id="abc123", kept_pr_number=10
            )

            # Should close 8 PRs (all except the kept one)
            assert len(results) == 8
            assert all(r.success for r in results)

            # Verify close_pr_as_duplicate was called for each
            assert mock_close.call_count == 8

    def test_cleanup_preserves_kept_pr(self, manager):
        """Kept PR is never closed."""
        with patch.object(manager, "search_prs_by_violation_id") as mock_search, patch.object(
            manager, "close_pr_as_duplicate"
        ) as mock_close:

            mock_search.return_value = [
                {"number": 2, "pull_request": {"url": "..."}},
                {"number": 3, "pull_request": {"url": "..."}},
            ]

            mock_close.return_value = PRCloseResult(
                success=True, pr_number=0, reason="duplicate"
            )

            manager.cleanup_duplicate_prs(violation_id="abc123", kept_pr_number=2)

            # Should only close PR #3, not #2
            assert mock_close.call_count == 1
            call_args = mock_close.call_args
            assert call_args[0][0] == 3  # First arg is pr_number


class TestEndToEndMergeAndCleanup:
    """Integration-style tests for complete workflow."""

    @pytest.fixture
    def manager(self):
        return GitHubPRManager(github_token="test-token", github_repo="owner/repo")

    def test_merge_and_cleanup_workflow(self, manager):
        """Simulate: merge better PR, close duplicates."""
        with patch.object(manager, "auto_merge_pr") as mock_merge, patch.object(
            manager, "cleanup_duplicate_prs"
        ) as mock_cleanup:

            mock_merge.return_value = PRMergeResult(
                success=True, pr_number=10, reason="auto_merged_high_score"
            )

            mock_cleanup.return_value = [
                PRCloseResult(success=True, pr_number=2, reason="duplicate"),
                PRCloseResult(success=True, pr_number=3, reason="duplicate"),
                # ... 6 more
            ]

            # Step 1: Auto-merge new solution (PR #10)
            merge_result = manager.auto_merge_pr(pr_number=10, score=19.0)
            assert merge_result.success

            # Step 2: Clean up all duplicate PRs
            cleanup_results = manager.cleanup_duplicate_prs(
                violation_id="abc123", kept_pr_number=10
            )

            # Should have closed 2 PRs (in this test)
            assert len(cleanup_results) == 2
            assert all(r.success for r in cleanup_results)
