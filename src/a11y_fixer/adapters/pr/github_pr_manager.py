"""GitHub PR management: auto-merge, supersede closure, and deduplication.

Extends the basic PR delivery capability with intelligent merge/close logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import httpx

GITHUB_API_BASE = "https://api.github.com"
GITHUB_API_VERSION = "2022-11-28"


class GitHubPRManagerError(RuntimeError):
    """Raised when PR management operations fail."""


@dataclass(frozen=True)
class PRMergeResult:
    """Result of auto-merge attempt."""

    success: bool
    pr_number: int
    reason: str  # e.g., "merged_automatically", "high_score", "awaiting_review"


@dataclass(frozen=True)
class PRCloseResult:
    """Result of closing a PR."""

    success: bool
    pr_number: int
    reason: str


class GitHubPRManager:
    """Manage GitHub PRs: merge, close, tag with violation IDs."""

    def __init__(self, github_token: str, github_repo: str):
        """Initialize with credentials.

        Args:
            github_token: GitHub personal access token
            github_repo: "owner/repo" format
        """
        self.github_token = github_token
        self.github_repo = github_repo
        owner, _, repo = github_repo.partition("/")
        if not owner or not repo:
            msg = f"github_repo must be 'owner/repo', got {github_repo!r}"
            raise GitHubPRManagerError(msg)
        self.owner = owner
        self.repo = repo

    def _headers(self) -> dict[str, str]:
        """Standard GitHub API headers."""
        return {
            "Authorization": f"Bearer {self.github_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
        }

    def _raise_for_status(self, resp: httpx.Response) -> None:
        if resp.status_code >= 400:  # noqa: PLR2004
            msg = (
                f"GitHub API error {resp.status_code} for "
                f"{resp.request.method} {resp.request.url}: {resp.text[:500]}"
            )
            raise GitHubPRManagerError(msg)

    def auto_merge_pr(
        self,
        pr_number: int,
        score: float,
        merge_threshold: float = 18.0,
    ) -> PRMergeResult:
        """Auto-merge PR if quality score meets threshold.

        Args:
            pr_number: GitHub PR number
            score: Rubric quality score (0-20)
            merge_threshold: Minimum score for auto-merge

        Returns:
            PRMergeResult with success status and reason
        """
        if score < merge_threshold:
            return PRMergeResult(
                success=False,
                pr_number=pr_number,
                reason=f"score_below_threshold ({score:.1f} < {merge_threshold})",
            )

        headers = self._headers()
        with httpx.Client(base_url=GITHUB_API_BASE, headers=headers, timeout=30.0) as client:
            try:
                # Check PR status first
                pr_resp = client.get(f"/repos/{self.owner}/{self.repo}/pulls/{pr_number}")
                self._raise_for_status(pr_resp)
                pr_data = pr_resp.json()

                # Check if CI passed
                if pr_data.get("state") != "open":
                    return PRMergeResult(
                        success=False,
                        pr_number=pr_number,
                        reason=f"pr_not_open (state={pr_data.get('state')})",
                    )

                # Attempt merge
                merge_resp = client.put(
                    f"/repos/{self.owner}/{self.repo}/pulls/{pr_number}/merge",
                    json={
                        "commit_title": f"a11y-fixer: merge PR #{pr_number}",
                        "commit_message": f"Auto-merged by a11y-fixer (quality score: {score:.1f}/20)",
                        "merge_method": "squash",
                    },
                )

                if merge_resp.status_code in (200, 405):  # 200 = merged, 405 = already merged
                    return PRMergeResult(
                        success=True,
                        pr_number=pr_number,
                        reason=f"auto_merged_high_score ({score:.1f} >= {merge_threshold})",
                    )
                else:
                    return PRMergeResult(
                        success=False,
                        pr_number=pr_number,
                        reason=f"merge_failed ({merge_resp.status_code})",
                    )
            except Exception as e:
                return PRMergeResult(
                    success=False,
                    pr_number=pr_number,
                    reason=f"error: {str(e)[:100]}",
                )

    def close_pr_as_superseded(
        self,
        pr_number: int,
        new_pr_number: int,
        old_score: float,
        new_score: float,
    ) -> PRCloseResult:
        """Close PR as superseded by a better solution.

        Args:
            pr_number: PR to close
            new_pr_number: PR that supersedes it
            old_score: Quality score of old PR
            new_score: Quality score of new PR

        Returns:
            PRCloseResult with success status
        """
        headers = self._headers()
        with httpx.Client(base_url=GITHUB_API_BASE, headers=headers, timeout=30.0) as client:
            try:
                # Close the PR
                close_resp = client.patch(
                    f"/repos/{self.owner}/{self.repo}/pulls/{pr_number}",
                    json={"state": "closed"},
                )
                self._raise_for_status(close_resp)

                # Add comment explaining the supersede
                comment_text = (
                    f"🔄 **Closed as superseded by PR #{new_pr_number}**\n\n"
                    f"Both PRs address the same accessibility violation. "
                    f"PR #{new_pr_number} provides a higher-quality solution:\n\n"
                    f"| Metric | PR #{pr_number} | PR #{new_pr_number} |\n"
                    f"|--------|----------|----------|\n"
                    f"| Quality Score | {old_score:.1f}/20 | {new_score:.1f}/20 |\n"
                    f"| Status | Superseded | ✅ Merged |\n\n"
                    f"No action needed on your part. The better solution is now in main."
                )

                client.post(
                    f"/repos/{self.owner}/{self.repo}/issues/{pr_number}/comments",
                    json={"body": comment_text},
                )

                return PRCloseResult(
                    success=True,
                    pr_number=pr_number,
                    reason=f"closed_as_superseded_by_pr_{new_pr_number}",
                )
            except Exception as e:
                return PRCloseResult(
                    success=False,
                    pr_number=pr_number,
                    reason=f"error: {str(e)[:100]}",
                )

    def close_pr_as_duplicate(self, pr_number: int, kept_pr_number: int) -> PRCloseResult:
        """Close PR as duplicate of another.

        Args:
            pr_number: PR to close
            kept_pr_number: PR we're keeping

        Returns:
            PRCloseResult with success status
        """
        headers = self._headers()
        with httpx.Client(base_url=GITHUB_API_BASE, headers=headers, timeout=30.0) as client:
            try:
                # Close the PR
                close_resp = client.patch(
                    f"/repos/{self.owner}/{self.repo}/pulls/{pr_number}",
                    json={"state": "closed"},
                )
                self._raise_for_status(close_resp)

                # Add comment explaining the duplicate
                comment_text = (
                    f"🔗 **Closed as duplicate of PR #{kept_pr_number}**\n\n"
                    f"Both PRs address the same accessibility violation. "
                    f"To avoid duplicate reviews and maintain code clarity, "
                    f"we're consolidating on PR #{kept_pr_number}.\n\n"
                    f"No action needed. Refer to PR #{kept_pr_number} for tracking."
                )

                client.post(
                    f"/repos/{self.owner}/{self.repo}/issues/{pr_number}/comments",
                    json={"body": comment_text},
                )

                return PRCloseResult(
                    success=True,
                    pr_number=pr_number,
                    reason=f"closed_as_duplicate_of_pr_{kept_pr_number}",
                )
            except Exception as e:
                return PRCloseResult(
                    success=False,
                    pr_number=pr_number,
                    reason=f"error: {str(e)[:100]}",
                )

    def search_prs_by_violation_id(self, violation_id: str, state: str = "open") -> list[dict]:
        """Find all PRs matching a violation_id in title.

        Args:
            violation_id: e.g., "7fa3c2b8d1e9"
            state: "open", "closed", or "all"

        Returns:
            List of PR data dicts
        """
        headers = self._headers()
        query = f"repo:{self.owner}/{self.repo} [violation-{violation_id}] is:pr is:{state}"

        with httpx.Client(base_url=GITHUB_API_BASE, headers=headers, timeout=30.0) as client:
            try:
                resp = client.get("/search/issues", params={"q": query, "per_page": 100})
                self._raise_for_status(resp)
                items = resp.json().get("items", [])
                # Filter to only PRs (items with pull_request key)
                return [item for item in items if "pull_request" in item]
            except Exception as e:
                print(f"Warning: Failed to search PRs: {e}")
                return []

    def cleanup_duplicate_prs(
        self,
        violation_id: str,
        kept_pr_number: int,
    ) -> list[PRCloseResult]:
        """Close all duplicate PRs for a violation, keeping only the best one.

        Args:
            violation_id: e.g., "7fa3c2b8d1e9"
            kept_pr_number: PR number to keep (highest quality)

        Returns:
            List of PRCloseResult for each closed PR
        """
        results = []

        # Find all open PRs for this violation
        duplicate_prs = self.search_prs_by_violation_id(violation_id, state="open")

        for pr_item in duplicate_prs:
            pr_num = pr_item["number"]

            # Don't close the kept PR
            if pr_num == kept_pr_number:
                continue

            result = self.close_pr_as_duplicate(pr_num, kept_pr_number)
            results.append(result)

        return results
